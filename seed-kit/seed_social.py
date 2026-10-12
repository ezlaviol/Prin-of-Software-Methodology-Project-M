"""
Seed a social app: users, posts, friendships, likes.

Adapt it to your schema by editing the CONFIG section only:
  * table names and id types
  * the make_* functions, which decide the columns of each row

Run:
  export DATABASE_URL='postgresql://USER:PASSWORD@HOST:5432/postgres'
  python seed_social.py --scale 1.0 --reset
"""
import argparse

from seedlib import (TextPool, after, connect, counts_per_parent, export_ids, insert,
                     make_rng, popularity, report, reset, signup_times, skew_report,
                     unique_pairs)

# =============================== CONFIG ===============================

# "int" for Integer / serial ids, "uuid" for UUID ids.
ID_TYPE = "int"

USERS, POSTS, FRIENDSHIPS, LIKES = "users", "messages", "friendships", "likes"

# Row counts at --scale 1.0. Use your estimate from the lecture 1 exercise.
N_USERS = 20_000
POSTS_PER_USER = 10          # average; the skew below makes it very uneven
FRIENDS_PER_USER = 10
LIKES_PER_USER = 30

# Skew: 0 = everyone equal, ~1 = typical social data.
ACTIVITY_SKEW = 0.8          # how unevenly users post and like
POPULARITY_SKEW = 0.9        # how unevenly posts attract likes

# If your app stores a friendship as two rows (A->B and B->A), set True.
FRIENDSHIPS_BOTH_DIRECTIONS = True

# Tip: create one account through your app, copy its password hash from the
# database, and paste it here. Every seeded user can then log in with that
# password, which load tests need. Hashing each password separately is slow.
PASSWORD_HASH = "$2b$12$IYG8dbwBfr52yD0u7u6NMuFRhYuZAXlsf8bOAV37RA9zWpoL0Q/sm"


def make_user(i, created_at, name):
    return {"email": f"user{i}@example.com", "hashed_password": PASSWORD_HASH,
            "created_at": created_at}               # no display_name column

def make_post(author_id, created_at, body):
    return {"user_id": author_id, "body": body,
            "created_at": created_at, "updated_at": created_at}

def make_like(user_id, post_id, created_at):
    return {"message_id": post_id, "user_id": user_id, "created_at": created_at}

def make_friendship(user_id, friend_id, created_at):
    return {"user_id": user_id, "friend_id": friend_id, "created_at": created_at}

# Distribution checks printed at the end: (table, foreign key column, label).
SKEW_CHECKS = [(POSTS, "user_id", "messages per user"),
               (LIKES, "message_id", "likes per message"),
               (FRIENDSHIPS, "user_id", "friends per user")]

# ============================ END CONFIG ==============================


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale", type=float, default=1.0, help="multiply all row counts")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--reset", action="store_true", help="empty the tables first")
    args = ap.parse_args()

    rng = make_rng(args.seed)
    conn = connect()
    tables = [LIKES, FRIENDSHIPS, POSTS, USERS]
    if args.reset:
        reset(conn, tables)

    from faker import Faker
    fake = Faker()
    names = TextPool(fake.name, 5000, rng)
    bodies = TextPool(lambda: fake.paragraph(nb_sentences=int(rng.integers(1, 5))), 5000, rng)

    n_users = int(N_USERS * args.scale)
    print(f"Seeding about {n_users:,} users and their activity...")

    # Users
    start = conn.execute(f'SELECT count(*) FROM "{USERS}"').fetchone()[0] + 1
    user_times = signup_times(n_users, rng)
    user_ids = insert(conn, USERS, [make_user(start + k, t, nm) for k, (t, nm)
                                    in enumerate(zip(user_times, names.take(n_users)))], id_type=ID_TYPE)
    activity = popularity(n_users, ACTIVITY_SKEW, rng)

    # Posts: a few users write most of them
    per_user = counts_per_parent(activity, POSTS_PER_USER, rng)
    authors = [k for k, c in enumerate(per_user) for _ in range(c)]
    post_times = after([user_times[k] for k in authors], rng)
    post_rows = [make_post(user_ids[k], t, b) for k, t, b in
                 zip(authors, post_times, bodies.take(len(authors)))]
    post_ids = insert(conn, POSTS, post_rows, id_type=ID_TYPE)

    # Friendships: popular users have many friends
    a, b = unique_pairs(n_users, activity, n_users, activity,
                        int(n_users * FRIENDS_PER_USER / (2 if FRIENDSHIPS_BOTH_DIRECTIONS else 1)),
                        rng, allow_same=False)
    if FRIENDSHIPS_BOTH_DIRECTIONS:
        pairs = {(min(x, y), max(x, y)) for x, y in zip(a, b)}
        a = [x for x, y in pairs] + [y for x, y in pairs]
        b = [y for x, y in pairs] + [x for x, y in pairs]
    f_times = after([max(user_times[x], user_times[y]) for x, y in zip(a, b)], rng)
    insert(conn, FRIENDSHIPS, [make_friendship(user_ids[x], user_ids[y], t)
                               for x, y, t in zip(a, b, f_times)], id_type=ID_TYPE, want_ids=False)

    # Likes: active users like popular posts, each pair at most once
    post_pop = popularity(len(post_ids), POPULARITY_SKEW, rng)
    lu, lp = unique_pairs(n_users, activity, len(post_ids), post_pop,
                          int(n_users * LIKES_PER_USER), rng)
    l_times = after([max(user_times[u], post_times[p]) for u, p in zip(lu, lp)], rng)
    insert(conn, LIKES, [make_like(user_ids[u], post_ids[p], t)
                         for u, p, t in zip(lu, lp, l_times)], id_type=ID_TYPE, want_ids=False)

    report(conn, [USERS, POSTS, FRIENDSHIPS, LIKES])
    print()
    for table, fk, label in SKEW_CHECKS:
        skew_report(conn, table, fk, label)

    export_ids("loadtest_ids.json", {"user_id": (user_ids, activity),
                                     "post_id": (post_ids, post_pop)}, rng)
    conn.close()


if __name__ == "__main__":
    main()
