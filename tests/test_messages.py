"""Tests for POST /api/messages authentication."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.db import get_db
from app import models

TEST_DB_URL = "sqlite:///./test.db"
engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def setup_db():
    models.Base.metadata.create_all(bind=engine)
    yield
    models.Base.metadata.drop_all(bind=engine)


app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)

_PWD = "testpassword123"  # noqa: S105


def _register_and_login(email="test@example.com"):
    client.post("/api/register", json={"email": email, "password": _PWD})
    resp = client.post("/api/login", json={"email": email, "password": _PWD})
    return resp.json()["access_token"]


def _create_message(token, body="hello from test"):
    resp = client.post(
        "/api/messages",
        json={"body": body},
        headers={"Authorization": "Bearer " + token},
    )
    return resp.json()["id"]


def test_create_message_success():
    token = _register_and_login()
    resp = client.post(
        "/api/messages",
        json={"body": "hello from test"},
        headers={"Authorization": "Bearer " + token},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["body"] == "hello from test"
    assert "user_id" in data


def test_create_message_missing_token():
    resp = client.post("/api/messages", json={"body": "no token"})
    assert resp.status_code == 401


def test_create_message_invalid_token():
    resp = client.post(
        "/api/messages",
        json={"body": "bad token"},
        headers={"Authorization": "******"},
    )
    assert resp.status_code == 401


def test_owner_can_edit_own_message():
    token = _register_and_login()
    msg_id = _create_message(token, body="original")

    resp = client.patch(
        f"/api/messages/{msg_id}",
        json={"body": "updated"},
        headers={"Authorization": "Bearer " + token},
    )

    assert resp.status_code == 200
    assert resp.json()["body"] == "updated"


def test_non_owner_cannot_edit_someone_elses_message():
    owner_token = _register_and_login("owner@example.com")
    other_token = _register_and_login("other@example.com")
    msg_id = _create_message(owner_token, body="original")

    resp = client.patch(
        f"/api/messages/{msg_id}",
        json={"body": "hijacked"},
        headers={"Authorization": "Bearer " + other_token},
    )

    assert resp.status_code == 403


def test_anonymous_user_cannot_edit_message():
    token = _register_and_login()
    msg_id = _create_message(token, body="original")

    resp = client.patch(f"/api/messages/{msg_id}", json={"body": "updated"})

    assert resp.status_code == 401
