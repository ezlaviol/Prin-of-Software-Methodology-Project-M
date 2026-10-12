"""
Seed an event sign-up app: users, categories, events, registrations, feedback.

Written for a Django schema, but it loads the tables directly with SQL, so
it works the same for any framework. Django names tables <app>_<model>
(e.g. events_event) and foreign key columns <FieldName>_id (e.g. OrganizerID_id).
Check the exact names in the Supabase Table Editor, then edit CONFIG.

Run:
  export DATABASE_URL='postgresql://USER:PASSWORD@HOST:5432/postgres'
  python seed_events.py --scale 1.0 --reset
"""
import argparse
from datetime import timedelta

from seedlib import (NOW, TextPool, after, connect, counts_per_parent, export_ids, insert,
                     make_rng, pick, popularity, report, reset, signup_times, skew_report,
                     unique_pairs)

# =============================== CONFIG ===============================

ID_TYPE = "int"

USERS, CATEGORIES_T, EVENTS, REGISTRATIONS, FEEDBACK = (
    "events_users", "events_category", "events_event", "events_registration", "events_feedback")
# Primary key column of each table that other tables reference.
USER_PK, CATEGORY_PK, EVENT_PK = "UserID", "CategoryID", "EventID"

N_USERS = 20_000
ORGANIZER_SHARE = 0.05       # share of users who organize events
EVENTS_PER_ORGANIZER = 5     # average; a few organizers run most events
REGISTRATIONS_PER_USER = 8   # average
FEEDBACK_SHARE = 0.3         # share of attendees of past events who leave feedback

ACTIVITY_SKEW = 0.8          # how unevenly users register / organize
POPULARITY_SKEW = 0.9        # how unevenly events attract registrations

CATEGORIES = ["Music", "Sports", "Food", "Tech", "Arts", "Outdoors", "Community",
              "Education", "Health", "Business", "Family", "Nightlife"]
RATINGS = ([1, 2, 3, 4, 5], [0.05, 0.07, 0.15, 0.33, 0.40])

# Django: a password hash starting with "!" means "no usable password".
# To let seeded users log in, create one user through the app and paste
# its hash here (it starts with pbkdf2_sha256$...).
PASSWORD_HASH = "!seeded-user"


def make_user(i, joined, first, last):
    # Columns that Django's AbstractUser adds, plus the custom UserID key.
    return {"password": PASSWORD_HASH, "last_login": None, "is_superuser": False,
            "username": f"user{i}", "first_name": first, "last_name": last,
            "email": f"user{i}@example.com", "is_staff": False, "is_active": True,
            "date_joined": joined}


def make_category(name):
    return {"Name": name}


def make_event(organizer_id, category_id, title, description, location, when, active):
    return {"OrganizerID_id": organizer_id, "CategoryID_id": category_id, "Title": title,
            "Description": description, "Location": location, "DateTime": when,
            "EventStatus": active}


def make_registration(user_id, event_id, active, registered_at):
    return {"UserID_id": user_id, "EventID_id": event_id,
            "RegistrationStatus": active, "RegistrationDate": registered_at}


def make_feedback(user_id, event_id, rating, comments, date):
    return {"UserID_id": user_id, "EventID_id": event_id, "Rating": rating,
            "Comments": comments, "Date": date}


SKEW_CHECKS = [(EVENTS, "OrganizerID_id", "events per organizer"),
               (REGISTRATIONS, "EventID_id", "registrations per event"),
               (REGISTRATIONS, "UserID_id", "registrations per user")]

# ============================ END CONFIG ==============================


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale", type=float, default=1.0, help="multiply all row counts")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--reset", action="store_true", help="empty the tables first")
    args = ap.parse_args()

    rng = make_rng(args.seed)
    conn = connect()
    if args.reset:
        reset(conn, [FEEDBACK, REGISTRATIONS, EVENTS, CATEGORIES_T, USERS])

    from faker import Faker
    fake = Faker()
    first_names = TextPool(fake.first_name, 2000, rng)
    last_names = TextPool(fake.last_name, 2000, rng)
    titles = TextPool(lambda: fake.catch_phrase()[:100], 3000, rng)
    descriptions = TextPool(lambda: fake.paragraph(nb_sentences=3)[:500], 3000, rng)
    places = TextPool(lambda: f"{fake.company()}, {fake.city()}"[:100], 1000, rng)
    comments = TextPool(lambda: fake.sentence(nb_words=12)[:500], 2000, rng)

    n_users = int(N_USERS * args.scale)
    print(f"Seeding about {n_users:,} users and their events...")

    # Users
    start = conn.execute(f'SELECT count(*) FROM "{USERS}"').fetchone()[0] + 1
    joined = signup_times(n_users, rng)
    user_ids = insert(conn, USERS, [make_user(start + k, t, f, l) for k, (t, f, l) in
                                    enumerate(zip(joined, first_names.take(n_users),
                                                  last_names.take(n_users)))],
                      id_col=USER_PK, id_type=ID_TYPE)

    # Categories (unique names; skipped if they already exist)
    existing = conn.execute(f'SELECT count(*) FROM "{CATEGORIES_T}"').fetchone()[0]
    if existing == 0:
        insert(conn, CATEGORIES_T, [make_category(c) for c in CATEGORIES],
               id_col=CATEGORY_PK, id_type=ID_TYPE, want_ids=False)
    category_ids = [r[0] for r in conn.execute(f'SELECT "{CATEGORY_PK}" FROM "{CATEGORIES_T}" ORDER BY 1')]
    category_w = popularity(len(category_ids), 0.8, rng)

    # Events: a few organizers run most events; dates from a year ago to 6 months ahead
    activity = popularity(n_users, ACTIVITY_SKEW, rng)
    organizers = pick(range(n_users), int(n_users * ORGANIZER_SHARE), activity, rng)
    organizers = sorted(set(organizers))
    org_w = popularity(len(organizers), ACTIVITY_SKEW, rng)
    hosts = [organizers[k] for k, c in enumerate(counts_per_parent(org_w, EVENTS_PER_ORGANIZER, rng))
             for _ in range(c)]
    n_events = len(hosts)
    when = [max(NOW + timedelta(days=float(d)), joined[h] + timedelta(days=1))   # not before the host joined
            for d, h in zip(rng.uniform(-365, 180, n_events).tolist(), hosts)]
    event_ids = insert(conn, EVENTS, [
        make_event(user_ids[h], c, t, d, p, w, bool(a)) for h, c, t, d, p, w, a in zip(
            hosts, pick(category_ids, n_events, category_w, rng), titles.take(n_events),
            descriptions.take(n_events), places.take(n_events), when,
            (rng.random(n_events) > 0.05).tolist())],
        id_col=EVENT_PK, id_type=ID_TYPE)

    # Registrations: popular events fill up; each user registers once per event
    event_pop = popularity(n_events, POPULARITY_SKEW, rng)
    target = int(n_users * REGISTRATIONS_PER_USER)
    ru, re_ = unique_pairs(n_users, activity, n_events, event_pop, int(target * 1.5), rng)
    # A user can only register for an event that had not happened before they joined.
    ok = [(u, e) for u, e in zip(ru, re_) if joined[u] < min(when[e], NOW)][:target]
    ru, re_ = [u for u, _ in ok], [e for _, e in ok]
    reg_times = []
    for u, e, x in zip(ru, re_, rng.random(len(ru)).tolist()):
        lo = joined[u]
        hi = max(min(when[e], NOW), lo)                  # before the event, not in the future
        reg_times.append(lo + (hi - lo) * x)
    reg_active = (rng.random(len(ru)) > 0.08).tolist()
    insert(conn, REGISTRATIONS, [make_registration(user_ids[u], event_ids[e], a, t)
                                 for u, e, a, t in zip(ru, re_, reg_active, reg_times)],
           id_type=ID_TYPE, want_ids=False)

    # Feedback: some attendees of past events rate them, mostly positively
    past = [(u, e) for u, e, a in zip(ru, re_, reg_active) if a and when[e] < NOW]
    keep = rng.random(len(past)) < FEEDBACK_SHARE
    past = [pe for pe, k in zip(past, keep.tolist()) if k]
    ratings = rng.choice(RATINGS[0], size=len(past), p=RATINGS[1]).tolist()
    fb_times = after([when[e] for _, e in past], rng)
    insert(conn, FEEDBACK, [make_feedback(user_ids[u], event_ids[e], r, c, t)
                            for (u, e), r, c, t in zip(past, ratings, comments.take(len(past)), fb_times)],
           id_type=ID_TYPE, want_ids=False)

    report(conn, [USERS, CATEGORIES_T, EVENTS, REGISTRATIONS, FEEDBACK])
    print()
    for table, fk, label in SKEW_CHECKS:
        skew_report(conn, table, fk, label)

    export_ids("loadtest_ids.json", {"user_id": (user_ids, activity),
                                     "event_id": (event_ids, event_pop)}, rng)
    conn.close()


if __name__ == "__main__":
    main()
