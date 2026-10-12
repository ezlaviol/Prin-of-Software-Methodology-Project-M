# Seed kit: realistic test data for your app

Scripts that fill your Supabase database with enough realistic data to make
performance problems visible. They write straight to the database tables with
PostgreSQL's `COPY`, so they work the same for FastAPI and Django apps, and
load hundreds of thousands of rows in seconds.

| File | Use it for |
|---|---|
| `seed_social.py` | Social apps: users, posts (or messages), friendships, likes |
| `seed_store.py` | Stores: users, products, orders, order items, cart items |
| `seed_events.py` | Event sign-up apps: users, categories, events, registrations, feedback (written for Django's table naming) |
| `seedlib.py` | Shared helpers. You should not need to edit it |

Pick the script closest to your app. If your app has tables the script does
not cover, copy the pattern of an existing table (see "Adding a table").

## What makes the data realistic

- **Skew.** Real activity is uneven: most users post a little and a few post a
  lot; a few products outsell the rest. The `*_SKEW` settings control this
  (0 = everyone equal, about 1 = typical social or shopping data).
- **Time.** Children come after their parents: a post after its author signed
  up, a like after its post, feedback after the event. More users signed up
  recently, as in a growing app.
- **Constraints.** Pairs that must be unique (a user likes a post once) are
  generated without duplicates, and totals match their line items.
- **Reproducibility.** The same `--seed` gives the same data every run.

## Steps

1. **Install**
   ```
   pip install -r requirements.txt
   ```
2. **Check your table and column names** in the Supabase Table Editor. They
   are not always what your models suggest:
   - Django names tables `<app>_<model>` (for example `events_event`) and
     foreign key columns `<FieldName>_id` (for example `OrganizerID_id`).
   - Column names with capital letters are case-sensitive; the scripts quote
     them for you.
3. **Edit the CONFIG section** at the top of your script:
   - `ID_TYPE`: `"int"` if your ids are Integer / serial / AutoField,
     `"uuid"` if they are UUIDs.
   - Table names.
   - The `make_*` functions. Each returns one row as `{column: value}`. Give
     a value for every column that is `NOT NULL` and has no database default.
     **Watch for Python-side defaults**: `default=uuid.uuid4` or
     `default=datetime.utcnow` in a model runs in Python, not in the database,
     so the script must supply those values. (`server_default=func.now()` is a
     database default, so you may leave it out.)
   - Remove the lines for any table your app does not have.
   - Row counts: use your estimates from the sizing exercise.
4. **Set a password hash** (optional, but needed for load tests that log in):
   create one account through your app, copy its password hash from the
   database, and paste it into `PASSWORD_HASH`. Every seeded user then has
   that same password. Hashing each password separately would take hours.
5. **Run a small test first**
   ```
   export DATABASE_URL='postgresql://USER:PASSWORD@HOST:5432/postgres'
   python seed_social.py --scale 0.05 --reset
   ```
   Then try your app: log in as `user1@example.com`, open lists and detail pages.
6. **Run at full size**
   ```
   python seed_social.py --scale 1.0 --reset
   ```

`--reset` empties the script's tables first (and, through `CASCADE`, any
table that references them). Never point it at a database you care about.

## Reading the output

```
table                            rows       size
users                          20,000    4040 kB
posts                         200,000      38 MB
...
whole database                            148 MB

posts per author    parents=19,370  median=4  p99=93  max=6223  top 1% hold 31.1%
```

- **size** includes indexes. Keep the whole database well under your
  Supabase plan's limit. Use `--scale` to adjust.
- The skew lines show how uneven the data is. `median=4` but `max=6223` means
  a typical author has 4 posts and the busiest has over six thousand. Pages
  about that author are where slow queries will show up.
- The script runs `ANALYZE` at the end so the query planner has up-to-date
  statistics about the new data.

The script also writes **`loadtest_ids.json`**: a sample of real ids, drawn
with the same skew as the data. The load-test scripts use it, so popular rows
get requested more often, as in real traffic.

## Adding a table

Every table follows the same three steps. For example, comments on posts:

```python
# 1. How many per parent (skewed toward active users)
per_post = counts_per_parent(post_pop, 2.0, rng)
parents = [k for k, c in enumerate(per_post) for _ in range(c)]

# 2. When (after the parent)
times = after([post_times[k] for k in parents], rng)

# 3. Build rows and load them
insert(conn, "comments",
       [{"post_id": post_ids[k], "user_id": u, "body": b, "created_at": t}
        for k, u, b, t in zip(parents, pick(user_ids, len(parents), activity, rng),
                              bodies.take(len(parents)), times)],
       id_type=ID_TYPE, want_ids=False)
```

Use `unique_pairs(...)` instead of `pick` when the table has a unique
constraint on a pair of columns.

## Troubleshooting

| Error | Likely cause |
|---|---|
| `null value in column "id"` | Your ids are UUIDs made in Python: set `ID_TYPE = "uuid"` |
| `null value in column "created_at"` (or similar) | The model's default runs in Python; add the column to the `make_*` function |
| `column "x" does not exist` | A name in a `make_*` function does not match the table; check the Table Editor |
| `relation "x" does not exist` | Wrong table name (Django adds the app prefix) |
| `duplicate key value violates unique constraint` | Run with `--reset`, or the unique columns need different values |
| Supabase reports the database is full | Lower `--scale` |
