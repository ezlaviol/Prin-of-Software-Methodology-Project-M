# Pagination demo: OFFSET vs. keyset

A small FastAPI service with two list endpoints over the `users_big` table
(1,000,000 rows), and a client that pages through them the way a real app
would ("Load more" or infinite scroll).

| File | What it is |
|---|---|
| `app.py` | The API: `/users/offset`, `/users/keyset`, and `/users/fake` (simulated sign-ups) |
| `client.py` | Walks an endpoint page by page and reports timing and duplicates |
| `setup.sql` | Creates the index both endpoints need |
| `requirements.txt` | Python packages |

## How the two endpoints differ

Both return users newest first (`ORDER BY created_at DESC, id DESC`),
optionally filtered with `?q=` (email contains the text). The difference is
what the server hands back for the next request.

**OFFSET** returns a page number. The client asks for `?page=N`, and the
server skips `(N - 1) × size` rows.

```
GET /users/offset?q=6&page=1&size=20
→ { "items": [...], "page": 1, "size": 20, "next_page": 2, "db_ms": 1.2 }

GET /users/offset?q=6&page=2&size=20
```

**Keyset** returns a cursor: an opaque token that records where the last
page ended (the `created_at` and `id` of its last row). The client sends it
back unchanged, and the server continues from that position using the index.

```
GET /users/keyset?q=6&size=20
→ { "items": [...], "size": 20, "next_cursor": "eyJjcmVhdGVkX2F0Ijog...", "db_ms": 1.1 }

GET /users/keyset?q=6&size=20&cursor=eyJjcmVhdGVkX2F0Ijog...
```

In both cases `next_page` or `next_cursor` is `null` on the last page. The
server finds out whether there is a next page by asking the database for one
extra row.

## Setup

1. The `users_big` table must exist (from `lecture1_demo.sql`).
2. Run `setup.sql` in the Supabase SQL Editor.
3. Install the packages (a virtual environment is recommended):
   ```
   pip install -r requirements.txt
   ```
4. Get your connection string from Supabase (**Connect** button, session
   pooler or direct connection). Change the start from `postgresql://` to
   `postgresql+psycopg://`, then:
   ```
   export DATABASE_URL='postgresql+psycopg://USER:PASSWORD@HOST:5432/postgres'
   uvicorn app:app --host 0.0.0.0 --port 8000
   ```
5. In a second terminal, run the client. Add `--base http://<your-vm>:8000`
   if the API runs on your Azure VM.

## Experiments

**1. Walk 2,000 pages with each method.** Watch the `db ms` column.

```
python client.py --mode keyset --q 6 --size 100 --pages 2000 --report-every 250
python client.py --mode offset --q 6 --size 100 --pages 2000 --report-every 250
```

Keyset stays flat. OFFSET grows with every page, because each request
re-reads all the rows before the page it returns.

**2. Jump straight to a deep page.** Only OFFSET can do this.

```
python client.py --mode offset --q 6 --start-page 20000 --pages 3
```

Every one of these requests is slow, even though it is only the first
request the client made.

**3. Page while other users sign up.** `--insert-every 1` adds 5 new users
before each page after the first.

```
python client.py --mode offset --q 6 --pages 5 --insert-every 1
python client.py --mode keyset --q 6 --pages 5 --insert-every 1
```

OFFSET shows duplicates: each insert pushes 5 rows from the page you just
read onto the next page. Keyset shows none, because it continues from the
last row you actually saw.

**4. Try to break the cursor.**

```
curl "http://localhost:8000/users/keyset?cursor=garbage"
```

The server rejects cursors it cannot decode, and cursors created for a
different `q`.

## Cleanup

Experiment 3 adds rows. Remove them in the SQL Editor:

```sql
DELETE FROM users_big WHERE email LIKE 'new%';
```

## Notes

- `db ms` is how long the API waited for the database, including the network
  trip to Supabase. `request ms` is what the client saw end to end.
- The cursor here is base64-encoded JSON, so a curious client can decode it.
  That is fine for this demo. Production APIs often sign or encrypt cursors
  so clients cannot tamper with them.
- The same design works in Django. Order with
  `.order_by("-created_at", "-id")`, and write the keyset condition as
  `Q(created_at__lt=c) | Q(created_at=c, id__lt=i)`, which means the same as
  `(created_at, id) < (c, i)`. Return the same `next_page` or `next_cursor`
  fields.
