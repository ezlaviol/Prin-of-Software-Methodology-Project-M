import os
import random
import time
import statistics
from collections import defaultdict

import psycopg
import numpy as np

DB_URL = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
N_RUNS = 100
WARMUP = 5

# use IDs from your output; tweak if needed
EXAMPLE_USER = 11
BUSIEST_USER = 17569

QUERIES = {
    "q2_like_counts_busiest": """
        SELECT m.id, count(l.id) AS likes
        FROM messages m
        LEFT JOIN likes l ON l.message_id = m.id
        WHERE m.user_id = %s
        GROUP BY m.id
    """,
    "q3_feed_recent_friends": """
        SELECT m.id, u.email
        FROM messages m
        JOIN friendships f ON f.friend_id = m.user_id
        JOIN users u ON u.id = m.user_id
        WHERE f.user_id = %s
        ORDER BY m.created_at DESC
        LIMIT 20
    """,
    "q4_users_never_messaged": """
        SELECT count(*)
        FROM users u
        WHERE NOT EXISTS (
            SELECT 1 FROM messages m WHERE m.user_id = u.id
        )
    """,
    "q5_top3_per_friend": """
        SELECT x.user_id, x.message_id, x.like_count, x.rank
        FROM (
            SELECT
                m.user_id,
                m.id AS message_id,
                count(l.id) AS like_count,
                row_number() OVER (
                    PARTITION BY m.user_id
                    ORDER BY count(l.id) DESC
                ) AS rank
            FROM messages m
            JOIN friendships f ON f.friend_id = m.user_id
            LEFT JOIN likes l ON l.message_id = m.id
            WHERE f.user_id = %s
            GROUP BY m.user_id, m.id
        ) x
        WHERE x.rank <= 3
        ORDER BY x.user_id, x.rank
    """,
    "q6_update_busiest_messages": """
        UPDATE messages
        SET updated_at = now()
        WHERE user_id = %s
    """,
}

def timed_query(conn, sql, params=(), write=False):
    t0 = time.perf_counter()
    with conn.cursor() as cur:
        cur.execute(sql, params)
        if not write:
            cur.fetchall()
    elapsed = (time.perf_counter() - t0) * 1000.0  # ms
    conn.rollback()  # ends the transaction; undoes the UPDATE so your data is unchanged
    return elapsed

def summarize(samples):
    return {
        "n": len(samples),
        "p50_ms": float(np.percentile(samples, 50)),
        "p95_ms": float(np.percentile(samples, 95)),
        "p99_ms": float(np.percentile(samples, 99)),
        "mean_ms": float(statistics.mean(samples)),
    }

def main():
    results = defaultdict(list)
    with psycopg.connect(DB_URL, prepare_threshold=None) as conn:
        # warmup
        for _ in range(WARMUP):
            timed_query(conn, QUERIES["q3_feed_recent_friends"], (EXAMPLE_USER,))
        # measured runs
        for _ in range(N_RUNS):
            results["q2_like_counts_busiest"].append(
                timed_query(conn, QUERIES["q2_like_counts_busiest"], (BUSIEST_USER,))
            )
            results["q3_feed_recent_friends"].append(
                timed_query(conn, QUERIES["q3_feed_recent_friends"], (EXAMPLE_USER,))
            )
            results["q4_users_never_messaged"].append(
                timed_query(conn, QUERIES["q4_users_never_messaged"])
            )
            results["q5_top3_per_friend"].append(
                timed_query(conn, QUERIES["q5_top3_per_friend"], (EXAMPLE_USER,))
            )
            results["q6_update_busiest_messages"].append(
                timed_query(conn, QUERIES["q6_update_busiest_messages"], (BUSIEST_USER,), write=True)
            )

    print(f"{'query':35} {'p50':>10} {'p95':>10} {'p99':>10} {'mean':>10} {'n':>6}")
    for name, samples in results.items():
        s = summarize(samples)
        print(f"{name:35} {s['p50_ms']:10.1f} {s['p95_ms']:10.1f} {s['p99_ms']:10.1f} {s['mean_ms']:10.1f} {s['n']:6}")

if __name__ == "__main__":
    main()