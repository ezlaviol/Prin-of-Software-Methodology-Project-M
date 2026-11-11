# Feedback — social app

**Relative standing in this class: B.** Read from the code on 11 November 2026.

This letter is search and sharding. Those are the two pieces that set the standing. Other notes are at the end and are separate from it.

Both are in the running process, which is why this sits above projects that never sharded. The standing is a B because a post written onto one shard is not what the page and the search read back.

## Search

`GET /api/search` calls `search.search_messages` with a bound query, an optional author email, a minimum like count, and a limit of 20. That is a real full-text function, not a scan with `ILIKE`. The response is `results` and `facets`. It does not include `page`, `page_size`, or `total`, so the caller cannot tell whether another page exists. Add the same envelope the feed already builds.

The connection that runs this query is `get_db()`, which is shard 0. Search only sees posts that live on that database. A post created on shard 1 is absent from search even when the SQL function is right. Search has to run on every shard that holds posts and merge by post id, or the posts have to live where search already looks. One of those two, not a function that quietly searches half the data.

## Connecting search to the feed

The API is not connected. `templates/feed.html` has no search box and no `fetch`. `GET /feed` renders posts from `_feed_query` in `feed_page`. The only caller of `/api/search` is `load_search.py`.

Connect the page you have. Do not add a `q` branch inside `feed_page` that filters with `ILIKE` or `_feed_query`. That would be a second search, and the page would still ignore the API.

**1. Look at one real response.** The result column names are whatever `search.search_messages` returns, and that function is not in the repo.

```text
curl "http://127.0.0.1:8000/api/search?q=hello&page=1"
```

`facets` is fixed by `app/search_sql.py`: each item is `author` and `n`. Write down the keys on `results[0]` (`body`, the author column, `created_at`, a like count, and `total` if the function already returns it).

**2. Echo the page from the API.** `page` is accepted and not returned, so the next button cannot tell where it is. `load_search.py` already treats the sum of `facets[].n` as the match count, which is right when each post has one author. If a result row has `total`, use that instead.

```python
total = rows[0]["total"] if rows and "total" in rows[0] else sum(f["n"] for f in facets)
return {
    "q": q,
    "page": page,
    "page_size": 20,
    "total": total,
    "results": [dict(row) for row in rows],
    "facets": [dict(row) for row in facets],
}
```

Leave the two commented `/api/search` handlers below this one commented. One of them is a product search (`category`, `max_price`). The live handler is `search_messages`.

**3. Call it from `feed.html`.** The feed is a Jinja page, and this API does not require the login cookie, so a same-origin `fetch` is enough. Wrap the existing post loop in `<div id="post-list">`, give the "Page N of M" paragraph `id="feed-pager"`, and add this above "Recent Posts":

```html
<form id="search-form">
  <input id="q" type="search" placeholder="Search posts" maxlength="200">
  <input id="author" type="search" placeholder="Author email, optional">
  <input id="min-likes" type="number" min="0" placeholder="Min likes">
  <button type="submit">Search</button>
  <button id="clear-search" type="button">Clear</button>
</form>
<div id="facets"></div>
```

```html
<script>
const list = document.getElementById("post-list");
const facetsEl = document.getElementById("facets");
const pager = document.getElementById("feed-pager");
const searchPager = document.createElement("p");
searchPager.hidden = true;
pager.after(searchPager);

function pick(row, keys) {
  for (const key of keys) {
    if (row[key] !== undefined && row[key] !== null) return String(row[key]);
  }
  return "";
}

function showFeed() {
  searchPager.hidden = true;
  pager.hidden = false;
  facetsEl.replaceChildren();
  window.location = "/feed";
}

async function runSearch(page) {
  const q = document.getElementById("q").value.trim();
  if (!q) { showFeed(); return; }
  const params = new URLSearchParams({ q, page: String(page) });
  const author = document.getElementById("author").value.trim();
  const minLikes = document.getElementById("min-likes").value;
  if (author) params.set("author_email", author);
  if (minLikes !== "") params.set("min_likes", minLikes);

  const res = await fetch("/api/search?" + params);
  if (!res.ok) { list.textContent = "Search failed (" + res.status + ")."; return; }
  const data = await res.json();
  const rows = data.results || [];

  list.replaceChildren();
  if (!rows.length) list.textContent = "No matching posts.";
  for (const row of rows) {
    const div = document.createElement("div");
    div.textContent = [
      pick(row, ["user_email", "author", "email", "author_email"]),
      pick(row, ["body", "message", "content"]),
      pick(row, ["created_at"]),
      pick(row, ["like_count", "likes"]),
    ].filter(Boolean).join("  ");
    list.appendChild(div);
  }

  facetsEl.replaceChildren();
  for (const facet of data.facets || []) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = facet.author + " (" + facet.n + ")";
    button.addEventListener("click", () => {
      document.getElementById("author").value = facet.author || "";
      runSearch(1);
    });
    facetsEl.appendChild(button);
  }

  const pageSize = data.page_size || 20;
  const total = data.total || 0;
  pager.hidden = true;
  searchPager.hidden = false;
  searchPager.replaceChildren();
  if (page > 1) {
    const prev = document.createElement("button");
    prev.type = "button";
    prev.textContent = "Prev";
    prev.addEventListener("click", () => runSearch(page - 1));
    searchPager.appendChild(prev);
  }
  searchPager.append(" Page " + page + " (" + total + " matches) ");
  if (page * pageSize < total) {
    const next = document.createElement("button");
    next.type = "button";
    next.textContent = "Next";
    next.addEventListener("click", () => runSearch(page + 1));
    searchPager.appendChild(next);
  }
}

document.getElementById("search-form").addEventListener("submit", (event) => {
  event.preventDefault();
  runSearch(1);
});
document.getElementById("clear-search").addEventListener("click", showFeed);
</script>
```

Use the keys from step 1 inside `pick`. An empty box returns to `/feed`, which is the unfiltered list you already render. A facet button fills the author box and searches again. Like and edit on a search hit can stay as the forms you already have, once you put the post id from the result into those actions.

If `SHARD_1_URL` points at a second database, this box still only searches shard 0, because `/api/search` uses `get_db()`. A missing post in that case is the shard read, not the form.

## Sharding

The write path knows about shards. A new post is inserted with `get_shard_session(author)`, and a like walks both sessions until it finds the post. The read path mostly does not.

- The HTML feed, `GET /api/messages/{id}`, `GET /api/search`, and friend lookup use `get_db()`, which opens shard 0 only.
- `GET /api/messages` does open both sessions. It takes `page * page_size` rows from each, concatenates them, and sets `total` to the length of that list. That number is how many rows were fetched, not how many posts exist.
- If `SHARD_1_URL` is unset, it falls back to the same database as shard 0. The merge does not drop duplicates, so the JSON feed can show the same post twice.
- If `SHARD_1_URL` is a second database, the website never shows those posts, and search does not either.

Friend add looks the other user up on shard 0. A person whose row is on shard 1 cannot be added once the second database is real.

The fix is one rule, applied to every read the page uses: feed, one post, search, like, and friend lookup. Fan out to both shards and dedupe by id, or stop writing to a shard those reads do not open. `total` is a count on each shard, added together.

## Software engineering notes

These do not change the standing.

- `GET /api/messages`, `GET /api/messages/{id}`, `GET /api/users`, and `GET /api/search` require no login. The HTML feed sends an anonymous visitor to the login page. The API should use that same rule. The feed currently returns email addresses; a display name would let you stop doing that.
- The browser still posts forms to `/posts`, `/posts/{id}/like`, `/posts/{id}/edit`, and `/friends/add`. Those handlers write the database beside the JSON routes. One function per action.
- `GET /api/users/{user_id}/summary` types the id as `int`. User ids are UUIDs, so that route cannot be called with a real id. Run it on the user's shard.
- `GET /api/feed/friends` exists and the home page does not use it. Once the shard reads are fixed, decide whether the home feed is global or friends-only, and make the page call that query. Friendships are one direction today.
