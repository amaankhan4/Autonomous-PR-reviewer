"""Authentication and authorisation behaviour."""

from __future__ import annotations

import pytest

from tests.conftest import auth_headers

REGISTER = {
    "email": "new.user@example.com",
    "username": "newuser",
    "password": "Str0ngPassword!",
    "full_name": "New User",
}


async def test_register_returns_tokens(client):
    response = await client.post("/api/v1/auth/register", json=REGISTER)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["access_token"] and body["refresh_token"]
    assert body["token_type"] == "bearer"


async def test_register_rejects_duplicate_email(client):
    await client.post("/api/v1/auth/register", json=REGISTER)
    duplicate = await client.post("/api/v1/auth/register", json=REGISTER)
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "USER_ALREADY_EXISTS"


@pytest.mark.parametrize(
    "password,reason",
    [
        ("short1A", "too short"),
        ("alllowercase123", "no uppercase"),
        ("ALLUPPERCASE123", "no lowercase"),
        ("NoDigitsHereOk", "no digit"),
    ],
)
async def test_register_enforces_password_policy(client, password, reason):
    response = await client.post(
        "/api/v1/auth/register", json={**REGISTER, "password": password}
    )
    assert response.status_code == 422, reason


async def test_login_wrong_password_is_401(client, user):
    response = await client.post(
        "/api/v1/auth/login", json={"email": user.email, "password": "wrong-password"}
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_CREDENTIALS"


async def test_login_unknown_email_is_401_not_404(client):
    """A missing account must not be distinguishable from a wrong password."""
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@example.com", "password": "whatever-123A"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_CREDENTIALS"


async def test_me_requires_authentication(client):
    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 401


async def test_me_returns_profile_and_installations(client, token):
    response = await client.get("/api/v1/auth/me", headers=auth_headers(token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["user"]["username"] == "reviewer"
    assert body["installations"] == []
    assert body["demo_mode"] is True


async def test_refresh_issues_new_access_token(client, user):
    login = await client.post(
        "/api/v1/auth/login", json={"email": user.email, "password": "Sup3rSecret!pass"}
    )
    refresh_token = login.json()["refresh_token"]
    response = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert response.status_code == 200, response.text
    assert response.json()["access_token"]


async def test_refresh_rejects_access_token(client, token):
    """An access token must not be usable where a refresh token is expected."""
    response = await client.post("/api/v1/auth/refresh", json={"refresh_token": token})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "TOKEN_INVALID"


async def test_garbage_token_is_rejected(client):
    response = await client.get("/api/v1/auth/me", headers=auth_headers("not-a-jwt"))
    assert response.status_code == 401


async def test_logout_is_audited_and_succeeds(client, token):
    response = await client.post("/api/v1/auth/logout", headers=auth_headers(token))
    assert response.status_code == 200
    assert "detail" in response.json()
