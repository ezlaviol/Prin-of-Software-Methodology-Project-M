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
    with TestClient(app, follow_redirects=False) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)
    models.Base.metadata.drop_all(bind=engine)


_EMAIL = "webflow@example.com"
_PWD = "testpassword123"  # noqa: S105


def test_register_redirects_to_feed(client):
    resp = client.post("/register", data={"email": _EMAIL, "password": _PWD})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/feed"
    assert "access_token" in resp.cookies


def test_login_sets_cookie_and_redirects_to_feed(client):
    client.post("/api/register", json={"email": _EMAIL, "password": _PWD})
    resp = client.post("/login", data={"email": _EMAIL, "password": _PWD})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/feed"
    assert "access_token" in resp.cookies


def test_feed_requires_auth_redirects_to_login(client):
    resp = client.get("/feed")
    assert resp.status_code == 302
    assert "/login" in resp.headers["location"]


def test_feed_accessible_with_cookie(client):
    resp = client.post("/register", data={"email": _EMAIL, "password": _PWD})
    cookie = resp.cookies.get("access_token")
    assert cookie is not None

    client.cookies.set("access_token", cookie)
    resp2 = client.get("/feed")
    assert resp2.status_code == 200
    assert "Feed" in resp2.text


def test_create_post_via_form(client):
    resp = client.post("/register", data={"email": _EMAIL, "password": _PWD})
    cookie = resp.cookies.get("access_token")

    client.cookies.set("access_token", cookie)
    resp2 = client.post("/posts", data={"body": "Hello world"})
    assert resp2.status_code == 303
    assert resp2.headers["location"] == "/feed"


def test_logout_clears_cookie(client):
    # Register to get a cookie, then logout should clear it
    client.post("/register", data={"email": _EMAIL, "password": _PWD})
    resp = client.get("/logout")
    assert resp.status_code == 303
    assert "/login" in resp.headers["location"]
    # Cookie should be cleared (expired/absent) in the response
    set_cookie = resp.headers.get("set-cookie", "")
    assert "access_token" not in resp.cookies or set_cookie == "" or "max-age=0" in set_cookie.lower() or "expires" in set_cookie.lower()
