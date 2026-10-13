"""
Moving work from Python into the database, with SQLAlchemy (FastAPI apps).

Each example runs a "Python way" and a "database way" side by side and
reports how many SQL queries each sent and how long it took.

Needs the social schema seeded by seed_social.py, and db_objects.sql for
the view and function examples.

Run:
  export DATABASE_URL='postgresql+psycopg://USER:PASSWORD@HOST:5432/postgres'
  python sqlalchemy_examples.py
"""
import os
import time
from datetime import datetime

from sqlalchemy import (DateTime, ForeignKey, Integer, String, Table, Text, create_engine,
                        event, exists, func, select, text, update)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, joinedload, mapped_column, relationship


# ------------------------------------------------------------------ models

class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320))
    messages: Mapped[list["Message"]] = relationship(back_populates="user")
    friendships: Mapped[list["Friendship"]] = relationship(foreign_keys="Friendship.user_id")


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    user: Mapped[User] = relationship(back_populates="messages")
    likes: Mapped[list["Like"]] = relationship()


class Friendship(Base):
    __tablename__ = "friendships"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    friend_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    friend: Mapped[User] = relationship(foreign_keys=[friend_id])


class Like(Base):
    __tablename__ = "likes"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


# ------------------------------------------------------------------ measuring

engine = create_engine(os.environ["DATABASE_URL"], connect_args={"prepare_threshold": None})
queries = {"n": 0}


@event.listens_for(engine, "before_cursor_execute")
def count_query(*args):
    queries["n"] += 1


def measure(label, fn):
    """Run fn in a fresh session; report query count and time."""
    with Session(engine) as session:
        queries["n"] = 0
        start = time.perf_counter()
        result = fn(session)
        ms = (time.perf_counter() - start) * 1000
    print(f"   {label:<16} {queries['n']:>6} queries {ms:>9.1f} ms   -> {result}")
    return result


def show_sql(stmt):
    """Print the SQL that SQLAlchemy generates for a statement."""
    sql = str(stmt.compile(engine, compile_kwargs={"literal_binds": True}))
    print("   SQL: " + sql.replace("\n", "\n        "))


def title(t):
    print(f"\n=== {t}")


# ------------------------------------------------------------------ examples

def main():
    with Session(engine) as s:
        # A typical user with 10-20 friends, and the busiest user.
        me = s.scalar(text("SELECT user_id FROM friendships GROUP BY user_id "
                           "HAVING count(*) BETWEEN 10 AND 20 ORDER BY user_id LIMIT 1"))
        top_user = s.scalar(text("SELECT user_id FROM messages GROUP BY user_id "
                                   "ORDER BY count(*) DESC LIMIT 1"))
    print(f"Example user: {me}; busiest user: {top_user}")

    # 1. Related rows: N+1 queries vs. one JOIN -------------------------
    title("1. Newest 20 messages with their users' emails")

    def python_way(s):
        messages = s.scalars(select(Message).order_by(Message.created_at.desc()).limit(20)).all()
        return len({p.user.email for p in messages})       # each .user is a query

    def db_way(s):
        stmt = select(Message).options(joinedload(Message.user)).order_by(Message.created_at.desc()).limit(20)
        messages = s.scalars(stmt).all()
        return len({p.user.email for p in messages})

    measure("lazy loading", python_way)
    measure("joinedload", db_way)

    # 2. Aggregation: count in Python vs. COUNT ... GROUP BY -----------
    title("2. Like counts for the busiest user's messages")

    def python_way(s):
        messages = s.scalars(select(Message).where(Message.user_id == top_user)).all()
        return sum(len(p.likes) for p in messages)            # loads every like row

    stmt = (select(Message.id, func.count(Like.id).label("likes"))
            .outerjoin(Like, Like.message_id == Message.id)
            .where(Message.user_id == top_user)
            .group_by(Message.id))

    def db_way(s):
        return sum(n for _, n in s.execute(stmt))

    measure("Python loop", python_way)
    measure("GROUP BY", db_way)
    show_sql(stmt)

    # 3. Multi-table join: a feed of friends' messages ---------------------
    title("3. Feed: 20 newest messages by my friends, with user email")

    def python_way(s):
        user = s.get(User, me)
        messages = [p for f in user.friendships for p in f.friend.messages]   # every friend, every message
        messages.sort(key=lambda p: p.created_at, reverse=True)
        return [(p.id, p.user.email) for p in messages[:20]][0]

    feed_stmt = (select(Message.id, User.email)
                 .join(Friendship, Friendship.friend_id == Message.user_id)
                 .join(User, User.id == Message.user_id)
                 .where(Friendship.user_id == me)
                 .order_by(Message.created_at.desc())
                 .limit(20))

    def db_way(s):
        return tuple(s.execute(feed_stmt).first())

    measure("Python loops", python_way)
    measure("one JOIN", db_way)
    show_sql(feed_stmt)

    # 4. EXISTS: users who have never messageed ----------------------------
    title("4. How many users have never messaged?")

    def python_way(s):
        users = set(s.scalars(select(Message.user_id)))    # every message's user id
        return sum(1 for uid in s.scalars(select(User.id)) if uid not in users)

    never = select(func.count()).select_from(User).where(~exists().where(Message.user_id == User.id))

    def db_way(s):
        return s.scalar(never)

    measure("Python sets", python_way)
    measure("NOT EXISTS", db_way)
    show_sql(never)

    # 5. Window function over a view: top 3 messages per user ------------
    title("5. Each of my friends' 3 most-liked messages (view + window function)")
    message_stats = Table("message_stats", Base.metadata, autoload_with=engine)   # a view, read like a table

    ranked = (select(message_stats.c.user_id, message_stats.c.message_id, message_stats.c.like_count,
                     func.row_number().over(partition_by=message_stats.c.user_id,
                                            order_by=message_stats.c.like_count.desc()).label("rank"))
              .where(message_stats.c.user_id.in_(select(Friendship.friend_id).where(Friendship.user_id == me)))
              .subquery())
    top3 = select(ranked).where(ranked.c.rank <= 3).order_by(ranked.c.user_id, ranked.c.rank)

    def view_way(s):
        return len(s.execute(top3).all())

    # The same result without the view: filter to my friends' messages first,
    # then count likes and rank only those.
    likes = func.count(Like.id)
    ranked2 = (select(Message.user_id, Message.id.label("message_id"), likes.label("like_count"),
                      func.row_number().over(partition_by=Message.user_id,
                                             order_by=likes.desc()).label("rank"))
               .join(Friendship, Friendship.friend_id == Message.user_id)
               .outerjoin(Like, Like.message_id == Message.id)
               .where(Friendship.user_id == me)
               .group_by(Message.user_id, Message.id)
               .subquery())
    top3_direct = select(ranked2).where(ranked2.c.rank <= 3).order_by(ranked2.c.user_id, ranked2.c.rank)

    def direct_way(s):
        return len(s.execute(top3_direct).all())

    measure("via the view", view_way)
    measure("filter first", direct_way)
    show_sql(top3_direct)

    # 6. Bulk update: load-modify-save vs. one UPDATE -------------------
    title("6. Mark all of the busiest user's messages as edited")

    def python_way(s):
        messages = s.scalars(select(Message).where(Message.user_id == top_user)).all()
        for p in messages:
            p.updated_at = datetime.now().astimezone()
        s.commit()      # one UPDATE per message (sent as a batch, but still one per row)
        return len(messages)

    bulk = update(Message).where(Message.user_id == top_user).values(updated_at=func.now())

    def db_way(s):
        n = s.execute(bulk).rowcount
        s.commit()
        return n

    measure("load and save", python_way)
    measure("one UPDATE", db_way)
    show_sql(bulk)

    # 7. Calling database functions -------------------------------------
    title("7. Calling functions defined in the database (db_objects.sql)")

    feed = func.feed(me, 5).table_valued("message_id", "user_email", "body", "created_at", "like_count")

    def call_feed(s):
        return [row.message_id for row in s.execute(select(feed.c.message_id, feed.c.like_count))]

    def call_like(s):
        message_id = s.scalar(select(Message.id).order_by(Message.id).limit(1))
        n = s.scalar(select(func.like_message(me, message_id)))   # any function name works: func.<name>
        s.commit()
        return n

    measure("feed()", call_feed)
    measure("like_message()", call_like)

    # 8. Raw SQL, safely parameterized ----------------------------------
    title("8. Raw SQL with bound parameters (never build SQL with f-strings)")
    raw = text("SELECT count(*) FROM likes l JOIN messages p ON p.id = l.message_id "
               "WHERE p.user_id = :user AND l.created_at > now() - make_interval(days => :days)")

    def raw_way(s):
        return s.scalar(raw, {"user": top_user, "days": 30})

    measure("text()", raw_way)


if __name__ == "__main__":
    main()
