# import os
# from sqlalchemy import create_engine
# from sqlalchemy.orm import sessionmaker

# DATABASE_URL = os.environ.get("DATABASE_URL")

# if not DATABASE_URL:
#     # Fallback to SQLite for local development only.
#     # NOTE: Do NOT use SQLite in production. Set DATABASE_URL to a Postgres URL (e.g. Supabase).
#     DATABASE_URL = "sqlite:///./dev.db"

# # SQLAlchemy engine
# engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {})
# SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# # Dependency
# def get_db():
#     db = SessionLocal()
#     try:
#         yield db
#     finally:
#         db.close()

import os
import hashlib
import uuid
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

load_dotenv()

def _fix_url(url: str | None) -> str | None:
    if url and url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url

SHARD_0_URL = _fix_url(os.environ.get("SHARD_0_URL") or os.environ.get("DATABASE_URL"))
SHARD_1_URL = _fix_url(os.environ.get("SHARD_1_URL") or SHARD_0_URL)
DIRECTORY_URL = _fix_url(os.environ.get("DIRECTORY_URL") or SHARD_0_URL)

ENGINES = {
    "shard_0": create_engine(SHARD_0_URL, pool_pre_ping=True),
    "shard_1": create_engine(SHARD_1_URL, pool_pre_ping=True),
    "directory": create_engine(DIRECTORY_URL, pool_pre_ping=True),
}

# Keep default `engine` pointing to shard_0 so startup hooks don't crash
engine = ENGINES["shard_0"]

SESSIONS = {
    name: sessionmaker(autocommit=False, autoflush=False, bind=eng)
    for name, eng in ENGINES.items()
}

def shard_bucket(user_id: uuid.UUID) -> int:
    """Matches PostgreSQL: (('x' || substr(md5(u::text), 1, 8))::bit(32)::bigint & x'7fffffff'::bigint) % 64"""
    digest = hashlib.md5(str(user_id).encode("utf-8")).hexdigest()[:8]
    val = int(digest, 16) & 0x7FFFFFFF
    return val % 64

def get_shard_name(user_id: uuid.UUID) -> str:
    return "shard_0" if shard_bucket(user_id) < 32 else "shard_1"

def get_shard_session(user_id: uuid.UUID):
    return SESSIONS[get_shard_name(user_id)]()

def get_directory_session():
    return SESSIONS["directory"]()

def get_all_shard_sessions():
    return [SESSIONS["shard_0"](), SESSIONS["shard_1"]()]

# Legacy fallback dependency
def get_db():
    db = SESSIONS["shard_0"]()
    try:
        yield db
    finally:
        db.close()
