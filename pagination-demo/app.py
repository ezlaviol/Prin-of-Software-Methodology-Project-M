"""
Pagination demo API: OFFSET pagination vs. keyset (cursor) pagination.

Both endpoints list users from users_big, newest first, with an optional
filter on email. The difference is what the server hands back so the
client can ask for the next page:

  /users/offset  ->  "next_page": 2          (client sends ?page=2)
  /users/keyset  ->  "next_cursor": "eyJ..."  (client sends ?cursor=eyJ...)

Run:
  export DATABASE_URL='postgresql+psycopg://USER:PASSWORD@HOST:5432/postgres'
  uvicorn app:app --host 0.0.0.0 --port 8000
"""
import base64
import json
import os
import time
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from sqlalchemy import create_engine, text

engine = create_engine(
    os.environ["DATABASE_URL"],
    pool_size=5,
    pool_pre_ping=True,
    # Needed if you connect through Supabase's transaction pooler (port 6543);
    # harmless otherwise.
    connect_args={"prepare_threshold": None},
)

app = FastAPI(title="Pagination demo")

MAX_PAGE_SIZE = 100
COLUMNS = "id, email, city, created_at"
# Newest first. created_at can have ties, so id breaks them. This makes the
# order unique, which keyset pagination needs. Index: (created_at, id).
ORDER_BY = "ORDER BY created_at DESC, id DESC"


# ---------------------------------------------------------------- helpers

def email_filter(q: Optional[str]):
    """Return a SQL condition and parameters for 'email contains q'."""
    if not q:
        return [], {}
    # Escape LIKE wildcards so the user's text is matched literally.
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return ["email LIKE :pattern"], {"pattern": f"%{escaped}%"}


def run_query(sql: str, params: dict):
    """Run a query; return the rows and how long we waited for the database."""
    start = time.perf_counter()
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).all()
    db_ms = (time.perf_counter() - start) * 1000
    return rows, round(db_ms, 2)


def to_json(row):
    return {
        "id": row.id,
        "email": row.email,
        "city": row.city,
        "created_at": row.created_at.isoformat(),
    }


def encode_cursor(last_row, q: Optional[str]) -> str:
    """Pack the sort key of the last row shown into an opaque token."""
    payload = {"created_at": last_row.created_at.isoformat(), "id": last_row.id, "q": q}
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


def decode_cursor(token: str, q: Optional[str]):
    """Unpack a token from the client. Never trust it blindly."""
    try:
        payload = json.loads(base64.urlsafe_b64decode(token.encode()))
        created_at = datetime.fromisoformat(payload["created_at"])
        last_id = int(payload["id"])
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid cursor")
    if payload.get("q") != q:
        raise HTTPException(status_code=400, detail="Cursor belongs to a different query")
    return created_at, last_id


# ---------------------------------------------------------------- OFFSET

@app.get("/users/offset")
def list_users_offset(
    q: Optional[str] = None,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=MAX_PAGE_SIZE),
):
    conditions, params = email_filter(q)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    # Ask for one extra row: if it comes back, there is a next page.
    params.update(limit=size + 1, offset=(page - 1) * size)
    sql = f"SELECT {COLUMNS} FROM users_big {where} {ORDER_BY} LIMIT :limit OFFSET :offset"
    rows, db_ms = run_query(sql, params)

    has_more = len(rows) > size
    rows = rows[:size]
    return {
        "items": [to_json(r) for r in rows],
        "page": page,
        "size": size,
        "next_page": page + 1 if has_more else None,
        "db_ms": db_ms,
    }


# ---------------------------------------------------------------- keyset

@app.get("/users/keyset")
def list_users_keyset(
    q: Optional[str] = None,
    cursor: Optional[str] = None,
    size: int = Query(20, ge=1, le=MAX_PAGE_SIZE),
):
    conditions, params = email_filter(q)

    if cursor:
        # "Rows that come after the last one you saw", in sort order.
        # Newest first, so "after" means an older (created_at, id).
        created_at, last_id = decode_cursor(cursor, q)
        conditions.append("(created_at, id) < (:last_created_at, :last_id)")
        params.update(last_created_at=created_at, last_id=last_id)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.update(limit=size + 1)
    sql = f"SELECT {COLUMNS} FROM users_big {where} {ORDER_BY} LIMIT :limit"
    rows, db_ms = run_query(sql, params)

    has_more = len(rows) > size
    rows = rows[:size]
    return {
        "items": [to_json(r) for r in rows],
        "size": size,
        "next_cursor": encode_cursor(rows[-1], q) if has_more else None,
        "db_ms": db_ms,
    }


# ---------------------------------------------------------------- demo helper

@app.post("/users/fake")
def add_fake_users(count: int = Query(5, ge=1, le=1000)):
    """Simulate sign-ups while a client is paging. New users are the newest
    rows, and their emails contain a 6 so they match the ?q=6 filter."""
    sql = """
        INSERT INTO users_big (id, email, city, created_at)
        SELECT m + g, 'new' || (m + g) || '-6@example.com', 'Austin', now()
        FROM (SELECT COALESCE(max(id), 0) AS m FROM users_big) AS s,
             generate_series(1, :count) AS g
    """
    with engine.begin() as conn:
        conn.execute(text(sql), {"count": count})
    return {"inserted": count}
