"""
Shared helpers for filling a PostgreSQL database with realistic test data.

The ideas that make data realistic:
  * skew:   a few users, posts or products get most of the activity
  * time:   children come after parents (a like comes after its post)
  * volume: enough rows that bad queries become visibly slow

The idea that makes loading fast:
  * COPY:   one streamed command per table instead of one INSERT per row
"""
import json
import os
import random
import time
import uuid
from datetime import datetime, timedelta, timezone

import numpy as np
import psycopg
from faker import Faker
from psycopg import sql

NOW = datetime.now(timezone.utc)


# ------------------------------------------------------------------ setup

def connect():
    """Connect using DATABASE_URL. SQLAlchemy-style URLs are accepted too."""
    url = os.environ["DATABASE_URL"]
    for prefix in ("postgresql+psycopg://", "postgresql+psycopg2://", "postgres://"):
        url = url.replace(prefix, "postgresql://")
    return psycopg.connect(url, prepare_threshold=None)


def make_rng(seed):
    """One seed makes every run produce the same data."""
    random.seed(seed)
    Faker.seed(seed)
    return np.random.default_rng(seed)


def reset(conn, tables):
    """Delete all rows from the given tables (and anything that references them)."""
    names = sql.SQL(", ").join(sql.Identifier(t) for t in tables)
    conn.execute(sql.SQL("TRUNCATE {} RESTART IDENTITY CASCADE").format(names))
    conn.commit()
    print(f"Emptied: {', '.join(tables)}")


# ------------------------------------------------------------------ skew

def popularity(n, skew, rng):
    """Probability weights for n items, Zipf-style.

    skew = 0 gives every item the same chance. Around 1 is typical of social
    and shopping data: a few items get a large share. Higher is more extreme.
    The ranks are shuffled so popular items are not simply the oldest ones.
    """
    w = 1.0 / np.arange(1, n + 1) ** skew
    rng.shuffle(w)
    return w / w.sum()


def pick(items, count, weights, rng):
    """Choose count items (with repeats) according to weights."""
    idx = rng.choice(len(items), size=count, p=weights)
    return [items[i] for i in idx.tolist()]


def unique_pairs(n_left, left_w, n_right, right_w, count, rng, allow_same=True):
    """Pick count distinct (left, right) index pairs, each side weighted.

    Used for tables with a unique pair constraint: a user likes a post once,
    a friendship exists once. Returns two lists of indexes.
    """
    keys = np.empty(0, dtype=np.int64)
    for _ in range(50):
        need = count - len(keys)
        if need <= 0:
            break
        batch = int(need * 1.3) + 100
        a = rng.choice(n_left, size=batch, p=left_w).astype(np.int64)
        b = rng.choice(n_right, size=batch, p=right_w).astype(np.int64)
        if not allow_same:
            keep = a != b
            a, b = a[keep], b[keep]
        keys = np.unique(np.concatenate([keys, a * n_right + b]))
    if len(keys) < count:
        print(f"  note: only {len(keys)} distinct pairs possible with this skew (wanted {count})")
    keys = rng.permutation(keys)[:count]
    return (keys // n_right).tolist(), (keys % n_right).tolist()


def counts_per_parent(weights, mean, rng):
    """How many children each parent gets: `mean` on average, spread by weights."""
    n = len(weights)
    return np.bincount(rng.choice(n, size=int(n * mean), p=weights), minlength=n).tolist()


# ------------------------------------------------------------------ time

def signup_times(n, rng, years=2.0, growth=2.0):
    """Sign-up times over the last `years`, with more recent sign-ups
    (a growing app). growth = 1 means evenly spread."""
    span = years * 365 * 86400
    u = rng.random(n) ** (1.0 / growth)
    start = NOW.timestamp() - span
    return [datetime.fromtimestamp(start + x * span, timezone.utc) for x in u.tolist()]


def after(times, rng, until=None, soon=2.0):
    """For each time, a later time before `until` (default: now).
    soon > 1 makes follow-up activity cluster shortly after the original."""
    until = until or NOW
    out = []
    for t, u in zip(times, (rng.random(len(times)) ** soon).tolist()):
        span = max((until - t).total_seconds(), 1.0)
        out.append(t + timedelta(seconds=u * span))
    return out


# ------------------------------------------------------------------ text

class TextPool:
    """Pre-generated realistic text, reused at random.

    Generating a fresh Faker sentence for each of a million rows is slow;
    sampling from a few thousand pre-made ones is fast and looks the same.
    """

    def __init__(self, make, size, rng):
        self.items = [make() for _ in range(size)]
        self.rng = rng

    def take(self, count):
        return [self.items[i] for i in self.rng.integers(0, len(self.items), size=count).tolist()]


# ------------------------------------------------------------------ loading

def new_uuids(n):
    """Reproducible UUIDs (they follow the random seed)."""
    return [uuid.UUID(int=random.getrandbits(128), version=4) for _ in range(n)]


def insert(conn, table, rows, id_col="id", id_type="int", want_ids=True):
    """Load rows (a list of dicts) with COPY and return their new ids, in order.

    id_type "int":  the database assigns ids (serial / identity / AutoField).
    id_type "uuid": ids are generated here, because many models create UUIDs
                    in Python (default=uuid.uuid4), so the database has no default.
    want_ids=False skips reading integer ids back when nothing references the table.
    """
    if not rows:
        return []
    start = time.perf_counter()
    ids = None
    if id_type == "uuid":
        ids = new_uuids(len(rows))
        for r, i in zip(rows, ids):
            r[id_col] = i
    elif want_ids:
        before = conn.execute(
            sql.SQL("SELECT COALESCE(max({}), 0) FROM {}").format(sql.Identifier(id_col), sql.Identifier(table))
        ).fetchone()[0]

    columns = list(rows[0].keys())
    copy_sql = sql.SQL("COPY {} ({}) FROM STDIN").format(
        sql.Identifier(table), sql.SQL(", ").join(sql.Identifier(c) for c in columns))
    with conn.cursor() as cur, cur.copy(copy_sql) as copy:
        for r in rows:
            copy.write_row([r[c] for c in columns])

    if id_type != "uuid" and want_ids:
        ids = [row[0] for row in conn.execute(
            sql.SQL("SELECT {0} FROM {1} WHERE {0} > %s ORDER BY {0}").format(
                sql.Identifier(id_col), sql.Identifier(table)), (before,))]
    conn.commit()
    print(f"  {table:<24} {len(rows):>10,} rows  {time.perf_counter() - start:6.1f} s")
    return ids


# ------------------------------------------------------------------ checking

def report(conn, tables):
    """Refresh planner statistics, then print row counts and size per table."""
    for t in tables:  # without fresh statistics, the planner guesses badly on new data
        conn.execute(sql.SQL("ANALYZE {}").format(sql.Identifier(t)))
    conn.commit()
    print(f"\n{'table':<24} {'rows':>12} {'size':>10}")
    for t in tables:
        n = conn.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(t))).fetchone()[0]
        size = conn.execute("SELECT pg_size_pretty(pg_total_relation_size(%s::regclass))",
                            (sql.Identifier(t).as_string(conn),)).fetchone()[0]
        print(f"{t:<24} {n:>12,} {size:>10}")
    db = conn.execute("SELECT pg_size_pretty(pg_database_size(current_database()))").fetchone()[0]
    print(f"{'whole database':<24} {'':>12} {db:>10}")


def skew_report(conn, table, fk_col, label):
    """How unevenly children are spread across parents."""
    q = sql.SQL("""
        WITH c AS (SELECT count(*) AS n FROM {t} GROUP BY {fk}),
             r AS (SELECT n, row_number() OVER (ORDER BY n DESC) AS rk, count(*) OVER () AS parents FROM c)
        SELECT max(parents),
               percentile_disc(0.5) WITHIN GROUP (ORDER BY n),
               percentile_disc(0.99) WITHIN GROUP (ORDER BY n),
               max(n),
               round(100.0 * sum(n) FILTER (WHERE rk <= greatest(1, parents / 100)) / sum(n), 1)
        FROM r""").format(t=sql.Identifier(table), fk=sql.Identifier(fk_col))
    parents, median, p99, top, share = conn.execute(q).fetchone()
    print(f"{label:<34} parents={parents:,}  median={median}  p99={p99}  max={top}  top 1% hold {share}%")


def export_ids(path, groups, rng, sample=2000):
    """Write a sample of ids for load tests, drawn with the same skew as the data,
    so load tests hit popular rows more often, as real traffic does."""
    out = {}
    for name, (ids, weights) in groups.items():
        chosen = pick(ids, min(sample, len(ids) * 5), weights, rng)
        out[name] = [str(i) for i in chosen]
    with open(path, "w") as f:
        json.dump(out, f)
    print(f"\nWrote {path} ({', '.join(f'{k}: {len(v)}' for k, v in out.items())})")
