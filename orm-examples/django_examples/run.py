"""
Moving work from Python into the database, with the Django ORM.

The same examples as sqlalchemy_examples.py. Each runs a "Python way" and a
"database way" and reports the number of SQL queries and the time taken.

Run from this folder:
  export DATABASE_URL='postgresql://USER:PASSWORD@HOST:5432/postgres'
  python run.py
"""
import os
import time
from urllib.parse import urlparse

import django
from django.conf import settings

url = urlparse(os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://"))
settings.configure(
    INSTALLED_APPS=["djdemo"],
    DATABASES={"default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": url.path.lstrip("/"), "USER": url.username, "PASSWORD": url.password,
        "HOST": url.hostname, "PORT": url.port or 5432,
    }},
    USE_TZ=True,
)
django.setup()

from django.db import connection                                    # noqa: E402
from django.db.models import Count, Exists, F, OuterRef, Window     # noqa: E402
from django.db.models.functions import Now, RowNumber               # noqa: E402
from django.utils import timezone                                   # noqa: E402

from djdemo.models import Friendship, Post, PostStats, User         # noqa: E402


def measure(label, fn):
    count = {"n": 0}

    def counter(execute, sql, params, many, context):
        count["n"] += 1
        return execute(sql, params, many, context)

    with connection.execute_wrapper(counter):
        start = time.perf_counter()
        result = fn()
        ms = (time.perf_counter() - start) * 1000
    print(f"   {label:<16} {count['n']:>6} queries {ms:>9.1f} ms   -> {result}")


def show_sql(qs):
    print("   SQL: " + str(qs.query))


def title(t):
    print(f"\n=== {t}")


def main():
    with connection.cursor() as c:
        c.execute("SELECT user_id FROM friendships GROUP BY user_id "
                  "HAVING count(*) BETWEEN 10 AND 20 ORDER BY user_id LIMIT 1")
        me = c.fetchone()[0]
        c.execute("SELECT author_id FROM posts GROUP BY author_id ORDER BY count(*) DESC LIMIT 1")
        top_author = c.fetchone()[0]
    print(f"Example user: {me}; busiest author: {top_author}")

    # 1. Related rows: N+1 vs. select_related (JOIN) ---------------------
    title("1. Newest 20 posts with their authors' emails")
    measure("lazy loading", lambda: len({p.author.email for p in Post.objects.order_by("-created_at")[:20]}))
    measure("select_related", lambda: len({p.author.email for p in
                                           Post.objects.select_related("author").order_by("-created_at")[:20]}))

    # 2. Aggregation: count in Python vs. annotate(Count) ----------------
    title("2. Like counts for the busiest author's posts")
    counted = Post.objects.filter(author_id=top_author).annotate(n=Count("likes"))
    measure("Python loop", lambda: sum(p.likes.count() for p in Post.objects.filter(author_id=top_author)))
    measure("annotate", lambda: sum(p.n for p in counted))
    show_sql(counted.values("id", "n"))

    # 3. Multi-table join: follow relations with __ ----------------------
    title("3. Feed: 20 newest posts by my friends, with author email")
    feed = (Post.objects.filter(author__friend_of__user_id=me)      # posts -> users -> friendships
            .select_related("author").order_by("-created_at")[:20])

    def python_way():
        posts = [p for f in Friendship.objects.filter(user_id=me) for p in f.friend.posts.all()]
        posts.sort(key=lambda p: p.created_at, reverse=True)
        return (posts[0].id, posts[0].author.email)

    measure("Python loops", python_way)
    measure("one JOIN", lambda: next((p.id, p.author.email) for p in feed))
    show_sql(feed)

    # 4. EXISTS ----------------------------------------------------------
    title("4. How many users have never posted?")
    never = User.objects.filter(~Exists(Post.objects.filter(author=OuterRef("pk"))))

    def python_way():
        authors = set(Post.objects.values_list("author_id", flat=True))
        return sum(1 for uid in User.objects.values_list("id", flat=True) if uid not in authors)

    measure("Python sets", python_way)
    measure("NOT EXISTS", never.count)
    show_sql(never)

    # 5. Window function: top 3 posts per friend --------------------------
    title("5. Each of my friends' 3 most-liked posts (window function)")
    top3 = (Post.objects.filter(author__friend_of__user_id=me)
            .annotate(like_count=Count("likes"))
            .annotate(rank=Window(RowNumber(), partition_by=F("author_id"), order_by=F("like_count").desc()))
            .filter(rank__lte=3))                                   # Django 4.2+
    measure("window", lambda: len(list(top3)))
    show_sql(top3)

    # 5b. A view, read through an unmanaged model
    title("5b. Reading the post_stats view through a model")
    measure("view", lambda: [s.like_count for s in
                             PostStats.objects.filter(author_id=top_author).order_by("-like_count")[:3]])

    # 6. Bulk update: save() per row vs. queryset.update() ----------------
    title("6. Mark all of the busiest author's posts as edited")

    def python_way():
        posts = list(Post.objects.filter(author_id=top_author))
        for p in posts:
            p.updated_at = timezone.now()
            p.save(update_fields=["updated_at"])                    # one UPDATE per post
        return len(posts)

    measure("save() per row", python_way)
    measure("update()", lambda: Post.objects.filter(author_id=top_author).update(updated_at=Now()))

    # 7. Calling database functions --------------------------------------
    title("7. Calling functions defined in the database (db_objects.sql)")

    def call_feed():
        # raw() maps the rows onto Post objects; the query must return the primary key as id.
        rows = Post.objects.raw("SELECT post_id AS id, body, created_at FROM feed(%s, %s)", [me, 5])
        return [p.id for p in rows]

    def call_like():
        post_id = Post.objects.order_by("id").values_list("id", flat=True).first()
        with connection.cursor() as c:
            c.execute("SELECT like_post(%s, %s)", [me, post_id])
            return c.fetchone()[0]

    measure("feed()", call_feed)
    measure("like_post()", call_like)

    # 8. Raw SQL, safely parameterized -----------------------------------
    title("8. Raw SQL with parameters (never build SQL with f-strings)")

    def raw_way():
        with connection.cursor() as c:
            c.execute("SELECT count(*) FROM likes l JOIN posts p ON p.id = l.post_id "
                      "WHERE p.author_id = %s AND l.created_at > now() - make_interval(days => %s)",
                      [top_author, 30])
            return c.fetchone()[0]

    measure("cursor", raw_way)


if __name__ == "__main__":
    main()
