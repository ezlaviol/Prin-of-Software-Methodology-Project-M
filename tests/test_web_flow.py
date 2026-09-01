"""Tests for web UI auth flow (cookie-based login/register/feed)."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.db import get_db
from app import models

TEST_DB_URL = "sqlite:///./test_webflow.db"
engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture()
def client():
    models.Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)
    models.Base.metadata.drop_all(bind=engine)


_EMAIL = "webflow@example.com"
_PWD = "testpassword123"  # noqa: S105


def test_register_redirects_to_feed(client):
    resp = client.post("/register", data={"email": _EMAIL, "password": _PWD}, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/feed"
    assert "access_token" in resp.cookies


def test_login_sets_cookie_and_redirects_to_feed(client):
    client.post("/api/register", json={"email": _EMAIL, "password": _PWD})
    resp = client.post("/login", data={"email": _EMAIL, "password": _PWD}, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/feed"
    assert "access_token" in resp.cookies


def test_feed_requires_auth_redirects_to_login(client):
    resp = client.get("/feed", follow_redirects=False)
    assert resp.status_code == 302
    assert "/login" in resp.headers["location"]


def test_feed_accessible_with_cookie(client):
    resp = client.post("/register", data={"email": _EMAIL, "password": _PWD}, follow_redirects=False)
    cookie = resp.cookies.get("access_token")
    assert cookie is not None

    client.cookies.set("access_token", cookie)
    resp2 = client.get("/feed")
    assert resp2.status_code == 200
    assert "Feed" in resp2.text


def test_create_post_via_form(client):
    resp = client.post("/register", data={"email": _EMAIL, "password": _PWD}, follow_redirects=False)
    cookie = resp.cookies.get("access_token")

    client.cookies.set("access_token", cookie)
    resp2 = client.post("/posts", data={"body": "Hello world"}, follow_redirects=False)
    assert resp2.status_code == 303
    assert resp2.headers["location"] == "/feed"


def test_logout_clears_cookie(client):
    # Register to get a cookie, then logout should clear it
    client.post("/register", data={"email": _EMAIL, "password": _PWD}, follow_redirects=False)
    resp = client.get("/logout", follow_redirects=False)
    assert resp.status_code == 303
    assert "/login" in resp.headers["location"]
    # Cookie should be cleared (expired/absent) in the response
    set_cookie = resp.headers.get("set-cookie", "")
    assert "access_token" not in resp.cookies or set_cookie == "" or "max-age=0" in set_cookie.lower() or "expires" in set_cookie.lower()


def test_like_post_via_feed_form_and_prevent_duplicate(client):
    resp = client.post("/register", data={"email": _EMAIL, "password": _PWD}, follow_redirects=False)
    cookie = resp.cookies.get("access_token")
    client.cookies.set("access_token", cookie)

    client.post("/posts", data={"body": "Likeable post"})
    msg_id = client.get("/api/messages").json()[0]["id"]

    like_resp = client.post(f"/posts/{msg_id}/like", follow_redirects=False)
    assert like_resp.status_code == 303
    assert "success=Post+liked" in like_resp.headers["location"]

    feed_resp = client.get("/feed")
    assert feed_resp.status_code == 200
    assert "Likeable post" in feed_resp.text
    assert client.get("/api/messages").json()[0]["like_count"] == 1

    duplicate_resp = client.post(f"/posts/{msg_id}/like", follow_redirects=False)
    assert duplicate_resp.status_code == 303
    assert "error=You+already+liked+this+post" in duplicate_resp.headers["location"]


def test_add_friend_via_feed_form_and_prevent_invalid_duplicates(client):
    user1_email = "user1@example.com"
    user2_email = "user2@example.com"

    resp = client.post("/register", data={"email": user1_email, "password": _PWD}, follow_redirects=False)
    cookie = resp.cookies.get("access_token")
    client.cookies.set("access_token", cookie)
    client.post("/api/register", json={"email": user2_email, "password": _PWD})

    users = client.get("/api/users").json()
    user2_id = next(u["id"] for u in users if u["email"] == user2_email)
    user1_id = next(u["id"] for u in users if u["email"] == user1_email)

    add_resp = client.post("/friends/add", data={"friend_id": str(user2_id)}, follow_redirects=False)
    assert add_resp.status_code == 303
    assert "success=Friend+added" in add_resp.headers["location"]

    feed_resp = client.get("/feed")
    assert feed_resp.status_code == 200
    assert user2_email in feed_resp.text

    duplicate_resp = client.post("/friends/add", data={"friend_id": str(user2_id)}, follow_redirects=False)
    assert duplicate_resp.status_code == 303
    assert "error=You+are+already+friends" in duplicate_resp.headers["location"]

    self_resp = client.post("/friends/add", data={"friend_id": str(user1_id)}, follow_redirects=False)
    assert self_resp.status_code == 303
    assert "error=Cannot+add+yourself+as+a+friend" in self_resp.headers["location"]
