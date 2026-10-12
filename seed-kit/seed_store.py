"""
Seed an online store: users, products, orders, order items, cart items.

Adapt it to your schema by editing the CONFIG section only:
  * table names and id type
  * the make_* functions, which decide the columns of each row
    (delete a table's lines if your app does not have it yet)

Run:
  export DATABASE_URL='postgresql://USER:PASSWORD@HOST:5432/postgres'
  python seed_store.py --scale 1.0 --reset
"""
import argparse
from datetime import timedelta
from decimal import Decimal

from seedlib import (NOW, TextPool, after, connect, counts_per_parent, export_ids, insert,
                     make_rng, pick, popularity, report, reset, signup_times, skew_report,
                     unique_pairs)

# =============================== CONFIG ===============================

# "int" for Integer / serial ids, "uuid" for UUID ids.
ID_TYPE = "int"

USERS, PRODUCTS, ORDERS, ORDER_ITEMS, CART_ITEMS = (
    "users", "products", "orders", "order_items", "cart_items")

# Row counts at --scale 1.0. Use your estimate from the lecture 1 exercise.
N_USERS = 20_000
N_PRODUCTS = 5_000
ORDERS_PER_USER = 3          # average; many users never order, a few order a lot
ITEMS_PER_ORDER = 2.5        # average
CART_SHARE = 0.3             # share of users with something in their cart

ACTIVITY_SKEW = 0.8          # how unevenly users place orders
POPULARITY_SKEW = 0.9        # how unevenly products sell

CATEGORIES = ["Electronics", "Books", "Home", "Toys", "Sports", "Clothing", "Beauty", "Games"]
ORDER_STATUSES = (["completed", "shipped", "placed", "cancelled"], [0.80, 0.08, 0.07, 0.05])
ACCOUNT_TYPES = (["customer", "store_manager"], [0.995, 0.005])

# Tip: create one account through your app and paste its password hash here,
# so every seeded user can log in with the same password.
PASSWORD_HASH = "PASTE-A-REAL-HASH-HERE"


def make_user(i, created_at, account_type):
    return {"email": f"user{i}@example.com", "password_hash": PASSWORD_HASH,
            "account_type": account_type, "created_at": created_at}


def make_product(p):
    # p has: name, slug, description, category, price, stock, created_at
    return {"name": p["name"], "description": p["description"], "category": p["category"],
            "price": p["price"], "stock": p["stock"],
            "created_at": p["created_at"], "updated_at": p["created_at"]}


def make_order(user_id, total, status, created_at):
    return {"user_id": user_id, "status": status, "total": total, "created_at": created_at}


def make_order_item(order_id, product_id, product_name, quantity, unit_price):
    return {"order_id": order_id, "product_id": product_id,
            "quantity": quantity, "unit_price": unit_price}


def make_cart_item(user_id, product_id, quantity, created_at):
    return {"user_id": user_id, "product_id": product_id,
            "quantity": quantity, "created_at": created_at}


# Distribution checks printed at the end: (table, foreign key column, label).
SKEW_CHECKS = [(ORDERS, "user_id", "orders per customer"),
               (ORDER_ITEMS, "product_id", "order lines per product"),
               (ORDER_ITEMS, "order_id", "lines per order")]

# ============================ END CONFIG ==============================


def money(x):
    return Decimal(f"{x:.2f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale", type=float, default=1.0, help="multiply all row counts")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--reset", action="store_true", help="empty the tables first")
    args = ap.parse_args()

    rng = make_rng(args.seed)
    conn = connect()
    if args.reset:
        reset(conn, [CART_ITEMS, ORDER_ITEMS, ORDERS, PRODUCTS, USERS])

    from faker import Faker
    fake = Faker()
    descriptions = TextPool(lambda: fake.paragraph(nb_sentences=3), 3000, rng)

    n_users = int(N_USERS * args.scale)
    n_products = max(50, int(N_PRODUCTS * args.scale))
    print(f"Seeding about {n_users:,} users and {n_products:,} products...")

    # Users
    start = conn.execute(f'SELECT count(*) FROM "{USERS}"').fetchone()[0] + 1
    user_times = signup_times(n_users, rng)
    types = rng.choice(ACCOUNT_TYPES[0], size=n_users, p=ACCOUNT_TYPES[1]).tolist()
    user_ids = insert(conn, USERS, [make_user(start + k, t, a) for k, (t, a)
                                    in enumerate(zip(user_times, types))], id_type=ID_TYPE)

    # Products: listed near the start of the period, prices long-tailed
    first = min(user_times)
    pstart = conn.execute(f'SELECT count(*) FROM "{PRODUCTS}"').fetchone()[0] + 1
    products = []
    for k, (desc, price) in enumerate(zip(descriptions.take(n_products),
                                          rng.lognormal(3.0, 0.9, n_products).tolist())):
        name = f"{fake.color_name()} {fake.word().title()} {fake.word().title()} {pstart + k}"
        products.append({
            "name": name, "slug": name.lower().replace(" ", "-"), "description": desc,
            "category": CATEGORIES[int(rng.integers(len(CATEGORIES)))],
            "price": money(max(price, 1.0)), "stock": int(rng.integers(0, 500)),
            "created_at": first + timedelta(days=float(rng.random() * 90)),
        })
    product_ids = insert(conn, PRODUCTS, [make_product(p) for p in products], id_type=ID_TYPE)

    # Orders and their items: popular products appear in many orders
    activity = popularity(n_users, ACTIVITY_SKEW, rng)
    product_pop = popularity(n_products, POPULARITY_SKEW, rng)
    buyers = [k for k, c in enumerate(counts_per_parent(activity, ORDERS_PER_USER, rng)) for _ in range(c)]
    order_times = after([user_times[k] for k in buyers], rng, soon=1.0)
    n_orders = len(buyers)
    lines = (1 + rng.geometric(1 / ITEMS_PER_ORDER, n_orders) - 1).clip(1, 20).tolist()
    chosen = iter(pick(range(n_products), sum(lines), product_pop, rng))
    quantities = iter((rng.geometric(0.6, sum(lines))).clip(1, 10).tolist())
    statuses = rng.choice(ORDER_STATUSES[0], size=n_orders, p=ORDER_STATUSES[1]).tolist()

    order_rows, items_per_order = [], []
    for k in range(n_orders):
        items = {}
        for _ in range(lines[k]):
            p, q = next(chosen), next(quantities)
            items[p] = items.get(p, 0) + q          # same product twice: one line
        total = sum((products[p]["price"] * q for p, q in items.items()), Decimal("0"))
        order_rows.append(make_order(user_ids[buyers[k]], total, statuses[k], order_times[k]))
        items_per_order.append(items)
    order_ids = insert(conn, ORDERS, order_rows, id_type=ID_TYPE)

    item_rows = [make_order_item(order_ids[k], product_ids[p], products[p]["name"], q, products[p]["price"])
                 for k, items in enumerate(items_per_order) for p, q in items.items()]
    insert(conn, ORDER_ITEMS, item_rows, id_type=ID_TYPE, want_ids=False)

    # Cart items: some users have a few products in their cart right now
    cu, cp = unique_pairs(n_users, activity, n_products, product_pop,
                          int(n_users * CART_SHARE * 2), rng)
    cart_times = after([max(user_times[u], NOW - timedelta(days=30)) for u in cu], rng, soon=1.0)
    insert(conn, CART_ITEMS, [make_cart_item(user_ids[u], product_ids[p], int(rng.integers(1, 4)), t)
                              for u, p, t in zip(cu, cp, cart_times)], id_type=ID_TYPE, want_ids=False)

    report(conn, [USERS, PRODUCTS, ORDERS, ORDER_ITEMS, CART_ITEMS])
    print()
    for table, fk, label in SKEW_CHECKS:
        skew_report(conn, table, fk, label)

    export_ids("loadtest_ids.json", {"user_id": (user_ids, activity),
                                     "product_id": (product_ids, product_pop),
                                     "order_id": (order_ids, [1 / n_orders] * n_orders)}, rng)
    conn.close()


if __name__ == "__main__":
    main()
