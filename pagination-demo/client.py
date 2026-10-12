"""
A client that walks a list endpoint page by page, the way an app with a
"Load more" button or infinite scroll would.

The client never computes where the next page starts. It sends back
whatever the server told it to: next_page for OFFSET, next_cursor for keyset.

Examples:
  python client.py --mode keyset --q 6 --pages 1000 --report-every 100
  python client.py --mode offset --q 6 --pages 1000 --report-every 100
  python client.py --mode offset --q 6 --start-page 10000 --pages 3
  python client.py --mode offset --q 6 --pages 20 --insert-every 1
"""
import argparse
import time

import requests


def main():
    parser = argparse.ArgumentParser(description="Walk a paginated endpoint.")
    parser.add_argument("--base", default="http://localhost:8000", help="API base URL")
    parser.add_argument("--mode", choices=["offset", "keyset"], required=True)
    parser.add_argument("--q", default=None, help="filter: email contains this text")
    parser.add_argument("--size", type=int, default=20, help="rows per page (max 100)")
    parser.add_argument("--pages", type=int, default=10, help="how many pages to fetch")
    parser.add_argument("--start-page", type=int, default=1,
                        help="OFFSET only: jump straight to page N")
    parser.add_argument("--report-every", type=int, default=1,
                        help="print one line every N pages")
    parser.add_argument("--insert-every", type=int, default=0,
                        help="simulate sign-ups every N pages (0 = never)")
    parser.add_argument("--insert-count", type=int, default=5,
                        help="how many users each simulated sign-up adds")
    args = parser.parse_args()

    if args.mode == "keyset" and args.start_page != 1:
        parser.error("keyset pagination cannot jump to page N; "
                     "it can only continue from a cursor")

    session = requests.Session()
    url = f"{args.base}/users/{args.mode}"
    params = {"size": args.size}
    if args.q:
        params["q"] = args.q
    if args.mode == "offset":
        params["page"] = args.start_page

    seen_ids = set()
    duplicates = 0
    rows_so_far = 0

    print(f"{'page':>6} {'rows':>9} {'db ms':>9} {'request ms':>11} {'dups':>6}")
    for n in range(1, args.pages + 1):
        # Someone else signs up while we are paging.
        if args.insert_every and n > 1 and (n - 1) % args.insert_every == 0:
            session.post(f"{args.base}/users/fake",
                         params={"count": args.insert_count}).raise_for_status()

        page_label = params["page"] if args.mode == "offset" else n
        start = time.perf_counter()
        response = session.get(url, params=params)
        request_ms = (time.perf_counter() - start) * 1000
        response.raise_for_status()
        data = response.json()

        for item in data["items"]:
            if item["id"] in seen_ids:
                duplicates += 1
            seen_ids.add(item["id"])
        rows_so_far += len(data["items"])

        # What the server told us to send next time.
        if args.mode == "offset":
            next_value = data["next_page"]
            params["page"] = next_value
        else:
            next_value = data["next_cursor"]
            params["cursor"] = next_value

        last = next_value is None or n == args.pages
        if n == 1 or n % args.report_every == 0 or last:
            print(f"{page_label:>6} {rows_so_far:>9} {data['db_ms']:>9.1f} "
                  f"{request_ms:>11.1f} {duplicates:>6}")
        if next_value is None:
            print("No more pages.")
            break

    print(f"\nFetched {rows_so_far} rows, {len(seen_ids)} distinct, {duplicates} duplicates.")


if __name__ == "__main__":
    main()
