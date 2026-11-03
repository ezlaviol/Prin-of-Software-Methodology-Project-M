#!/usr/bin/env python3
"""Closed-loop load driver for GET /api/search. Stdlib only.

Each request picks a random query from a size bucket (none / few / many / huge),
so results range from empty to several pages. Prints and appends a per-bucket table.
Faker's default text is Latin lorem ipsum, so the terms below are Latin.
Tune the buckets after checking real hit counts (see --probe).
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from load_baseline import once, percentile

# bucket -> (queries, pages to request). Adjust to your data.
BUCKETS: dict[str, tuple[list[str], list[int]]] = {
    "none": (["zzzxqwv", "nonexistentterm", "qqqqjjjj kkkk"], [1]),
    "few": (["laudantium reiciendis", "voluptatem aperiam sunt", "perspiciatis"], [1]),
    "many": (["quo", "dolor", "ipsum"], [1, 2, 3]),
    "huge": (["et", "in", "consequuntur", "est"], [1, 2, 5, 10]),
}


def build_url(base: str, q: str, page: int, extra: dict[str, str]) -> str:
    params = {"q": q, "page": page, **extra}
    return f"{base}/api/search?{urllib.parse.urlencode(params)}"


def probe(base: str, headers: dict[str, str], extra: dict[str, str], timeout: float) -> None:
    """Show result count (page 1) and total matches per query to validate buckets."""
    import urllib.request

    for bucket, (queries, _) in BUCKETS.items():
        for q in queries:
            req = urllib.request.Request(build_url(base, q, 1, extra), headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    data = json.loads(r.read())
                total = sum(f["n"] for f in data.get("facets", []))
                print(f"{bucket:5} q={q!r:35} page1={len(data['results']):3} total_matches={total}")
            except Exception as e:
                print(f"{bucket:5} q={q!r:35} ERROR {e}")


def worker(stop_at, base, headers, extra, timeout, mix, samples, lock):
    names = list(BUCKETS)
    weights = [mix[n] for n in names]
    while time.perf_counter() < stop_at:
        bucket = random.choices(names, weights)[0]
        queries, pages = BUCKETS[bucket]
        url = build_url(base, random.choice(queries), random.choice(pages), extra)
        ms, status, err = once(url, "GET", None, headers, timeout)
        with lock:
            samples.append((bucket, ms, status, err))


def run_window(seconds, args, headers, extra, mix):
    samples: list[tuple[str, float, int, str]] = []
    lock = threading.Lock()
    stop_at = time.perf_counter() + seconds
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futs = [
            pool.submit(worker, stop_at, args.base.rstrip("/"), headers, extra,
                        args.timeout, mix, samples, lock)
            for _ in range(args.concurrency)
        ]
        for f in futs:
            f.result()
    return samples


def summarize(label, concurrency, bucket, rows, wall):
    lat = sorted(r[1] for r in rows)
    oks = [r for r in rows if 200 <= r[2] < 400]
    return {
        "label": label,
        "bucket": bucket,
        "concurrency": concurrency,
        "attempts": len(rows),
        "errors": len(rows) - len(oks),
        "rps": round(len(oks) / wall, 2) if wall else 0,
        "p50_ms": round(percentile(lat, 50), 2),
        "p95_ms": round(percentile(lat, 95), 2),
        "p99_ms": round(percentile(lat, 99), 2),
        "max_ms": round(lat[-1], 2) if lat else float("nan"),
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Search load test")
    p.add_argument("--base", required=True)
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--duration", type=float, default=60.0)
    p.add_argument("--warmup", type=float, default=10.0)
    p.add_argument("--timeout", type=float, default=30.0)
    p.add_argument("--label", default="search")
    p.add_argument("--out-dir", default="m2/results")
    p.add_argument("--min-likes", default="", help="optional filter")
    p.add_argument("--author-email", default="", help="optional filter")
    p.add_argument("--probe", action="store_true", help="print hit counts per query and exit")
    args = p.parse_args()

    headers = {"Accept": "application/json", "User-Agent": "search-load"}
    extra = {}
    if args.min_likes:
        extra["min_likes"] = args.min_likes
    if args.author_email:
        extra["author_email"] = args.author_email

    if args.probe:
        probe(args.base.rstrip("/"), headers, extra, args.timeout)
        return 0

    # Equal share of each bucket
    mix = {b: 1.0 for b in BUCKETS}

    print(f"warmup {args.warmup}s, measure {args.duration}s, c={args.concurrency}")
    run_window(args.warmup, args, headers, extra, mix)
    t0 = time.perf_counter()
    samples = run_window(args.duration, args, headers, extra, mix)
    wall = time.perf_counter() - t0

    rows = [summarize(args.label, args.concurrency, b,
                      [s for s in samples if s[0] == b], wall) for b in BUCKETS]
    rows.append(summarize(args.label, args.concurrency, "ALL", samples, wall))

    cols = list(rows[0])
    print(" | ".join(cols))
    for r in rows:
        print(" | ".join(str(r[c]) for c in cols))

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "search_summary.csv"
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        if new:
            w.writeheader()
        w.writerows(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())