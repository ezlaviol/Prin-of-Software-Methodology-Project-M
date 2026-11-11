import os
import uuid
from passlib.context import CryptContext
import jwt
import hashlib
from datetime import datetime, timedelta
from fastapi import HTTPException
from sqlalchemy.orm import Session
from . import models
from sqlalchemy import text
from . import db as shard_db


PWD_CONTEXT = CryptContext(schemes=["bcrypt"], deprecated="auto")
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 days


def get_password_hash(password: str) -> str:
    # Hash with SHA-256 first to handle passwords > 72 bytes
    sha_hash = hashlib.sha256(password.encode()).hexdigest()
    return PWD_CONTEXT.hash(sha_hash)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    # Hash the plain password with SHA-256 before comparing
    sha_hash = hashlib.sha256(plain_password.encode()).hexdigest()
    return PWD_CONTEXT.verify(sha_hash, hashed_password)


def create_access_token(subject: str, expires_delta: timedelta | None = None) -> str:
    to_encode = {"sub": subject}
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


# Database helpers

# def create_user(db: Session, user):
#     existing = db.query(models.User).filter(models.User.email == user.email).first()
#     if existing:
#         raise ValueError("User already exists")
#     hashed = get_password_hash(user.password)
#     db_user = models.User(email=user.email, hashed_password=hashed)
#     db.add(db_user)
#     db.commit()
#     db.refresh(db_user)
#     return db_user

def create_user(_unused_db: Session, user):
    email_clean = user.email.strip().lower()
    dir_sess = shard_db.get_directory_session()
    try:
        # 1. Check global uniqueness in user_directory
        existing = dir_sess.execute(
            text("SELECT user_id FROM user_directory WHERE email = :email"),
            {"email": email_clean},
        ).first()
        if existing:
            raise ValueError("User already exists")

        # 2. Insert into user_directory to generate the authoritative UUID
        row = dir_sess.execute(
            text("INSERT INTO user_directory (email) VALUES (:email) RETURNING user_id"),
            {"email": email_clean},
        ).first()
        new_user_id = row[0] if isinstance(row[0], uuid.UUID) else uuid.UUID(str(row[0]))
        dir_sess.commit()
    except Exception:
        dir_sess.rollback()
        raise
    finally:
        dir_sess.close()

    # 3. Insert the actual user row onto the assigned shard (shard_0 or shard_1)
    user_sess = shard_db.get_shard_session(new_user_id)
    try:
        hashed = get_password_hash(user.password)
        db_user = models.User(id=new_user_id, email=email_clean, hashed_password=hashed)
        user_sess.add(db_user)
        user_sess.commit()
        user_sess.refresh(db_user)
        return db_user
    finally:
        user_sess.close()


# def authenticate_user(db: Session, email: str, password: str):
#     user = db.query(models.User).filter(models.User.email == email).first()
#     if not user:
#         return None
#     if not verify_password(password, user.hashed_password):
#         return None
#     token = create_access_token(subject=str(user.id))
#     return token

def authenticate_user(_unused_db: Session, email: str, password: str):
    email_clean = email.strip().lower()
    dir_sess = shard_db.get_directory_session()
    try:
        row = dir_sess.execute(
            text("SELECT user_id FROM user_directory WHERE email = :email"),
            {"email": email_clean},
        ).first()
        if not row:
            return None
        user_id = row[0] if isinstance(row[0], uuid.UUID) else uuid.UUID(str(row[0]))
    finally:
        dir_sess.close()

    # Query the specific shard where this user lives
    user_sess = shard_db.get_shard_session(user_id)
    try:
        user = user_sess.query(models.User).filter(models.User.id == user_id).first()
        if not user or not verify_password(password, user.hashed_password):
            return None
        return create_access_token(subject=str(user.id))
    finally:
        user_sess.close()

# helper used by HTML flow to return token string

def authenticate_user_for_html(db: Session, email: str, password: str):
    return authenticate_user(db, email, password)


def get_current_user_id(token: str) -> int:
    """Decode a JWT bearer token and return the user id (int).

    Raises HTTPException 401 for invalid or expired tokens.
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        sub = payload.get("sub")
        if sub is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        return uuid.UUID(sub)
        # return int(sub)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except (jwt.InvalidTokenError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid token")
