# ORM examples: moving work into the database

The same eight examples written twice, once with SQLAlchemy (FastAPI apps)
and once with the Django ORM. Each example runs a "Python way" and a
"database way" side by side and prints the number of SQL queries and the time.

| File | What it is |
|---|---|
| `sqlalchemy_examples.py` | SQLAlchemy 2.0 version |
| `django_examples/run.py` | Django version (models in `django_examples/djdemo/models.py`) |
| `db_objects.sql` | A view, two functions and a trigger used by examples 5 and 7 |

## Setup

1. Seed the social schema with `seed_social.py` from the seed kit.
2. Run `db_objects.sql` in the Supabase SQL Editor **after** seeding. (The
   trigger adds work to every insert into `likes`, so seeding afterwards
   would be much slower. That is itself a useful lesson about triggers.)
3. Install and run:
   ```
   pip install -r requirements.txt
   export DATABASE_URL='postgresql+psycopg://USER:PASSWORD@HOST:5432/postgres'
   python sqlalchemy_examples.py
   cd django_examples && python run.py
   ```

## The examples

| # | Task | Python way | Database way |
|---|---|---|---|
| 1 | 20 newest posts with author emails | lazy loading (N+1) | `joinedload` / `select_related` |
| 2 | Like counts for one author's posts | count each post's likes in Python | `COUNT ... GROUP BY` / `annotate(Count)` |
| 3 | Feed of friends' posts | loop over friends, then posts | one JOIN across three tables |
| 4 | Users who never posted | Python sets | `NOT EXISTS` |
| 5 | Each friend's 3 most-liked posts | | window function (`row_number`) |
| 6 | Update all of one author's posts | load, modify, save each | one `UPDATE` |
| 7 | Call `feed()` and `like_post()` | | database functions |
| 8 | A query the ORM cannot express well | | raw SQL with bound parameters |

Results from a local test run (20,000 users, 200,000 posts, 600,000 likes):

| # | SQLAlchemy: Python way | SQLAlchemy: database way | Django: Python way | Django: database way |
|---|---|---|---|---|
| 1 | 12 queries, 33 ms | 1 query, 8 ms | 21 queries, 28 ms | 1 query, 2 ms |
| 2 | 6,224 queries, 5,149 ms | 1 query, 85 ms | 6,224 queries, 4,171 ms | 1 query, 143 ms |
| 3 | 22 queries, 56 ms | 1 query, 9 ms | 21 queries, 36 ms | 1 query, 2 ms |
| 4 | 2 queries, 150 ms | 1 query, 25 ms | 2 queries, 110 ms | 1 query, 29 ms |
| 6 | 578 ms | 1 query, 49 ms | 6,224 queries, 5,076 ms | 1 query, 49 ms |

On Supabase, every query also pays a network round trip, so the difference
between 1 query and 6,224 queries is far larger than shown here.

## Things to notice

- **Example 1, SQLAlchemy sends 12 queries, not 21.** Its session remembers
  objects it has already loaded (the identity map), so repeated authors are
  not fetched again. Django has no identity map and fetches every one.
- **Example 5, the view is slow when joined.** Reading `post_stats` for one
  author is fast, because PostgreSQL applies `author_id = ...` before grouping.
  But joined to the friendships table, PostgreSQL computes the whole view (every
  post's like count) first. The SQLAlchemy file shows both versions: 480 ms
  through the view, 9 ms when filtering first. Views are convenient; check
  them with `EXPLAIN`.
- **Example 6, SQLAlchemy's "2 queries" is misleading.** It sends the
  per-row UPDATEs as one batch, but the database still runs one UPDATE per row.
- **Examples 7 and 8 are PostgreSQL-specific.** Everything else is portable:
  the ORMs generate suitable SQL for PostgreSQL, MySQL or SQLite.

## Keeping database objects in version control

Views, functions and triggers belong in migrations, like tables.

**Django:** an empty migration with `RunSQL`.
```
python manage.py makemigrations yourapp --empty -n db_objects
```
```python
from django.db import migrations

class Migration(migrations.Migration):
    dependencies = [("yourapp", "0005_previous")]
    operations = [
        migrations.RunSQL(
            sql=open("yourapp/sql/db_objects.sql").read(),
            reverse_sql="DROP VIEW IF EXISTS post_stats; DROP FUNCTION IF EXISTS feed(int, int);",
        ),
    ]
```

**SQLAlchemy with Alembic:**
```python
def upgrade():
    op.execute(open("sql/db_objects.sql").read())

def downgrade():
    op.execute("DROP VIEW IF EXISTS post_stats; DROP FUNCTION IF EXISTS feed(int, int);")
```
