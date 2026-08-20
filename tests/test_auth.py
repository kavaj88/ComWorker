"""Tests for authentication endpoints: /api/auth/*

Covers:
- POST /api/auth/register
- POST /api/auth/login
- POST /api/auth/refresh
- GET  /api/auth/me
- POST /api/auth/api-token
- PUT  /api/auth/change-password
"""

from conftest import (
    ADMIN_PASSWORD,
    ADMIN_USERNAME,
    admin_token,
    api_url,
    auth_headers,
    json_request,
    unique_email,
    unique_username,
)


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------

def test_register_dedicated_user():
    username = unique_username("regded")
    result = json_request(
        api_url("/api/auth/register"),
        method="POST",
        payload={
            "username": username,
            "email": unique_email(username),
            "password": "test123456",
            "runtime_mode": "dedicated",
        },
    )
    assert "access_token" in result
    assert "refresh_token" in result
    assert result["token_type"] == "bearer"
    assert result["username"] == username
    assert result["role"] == "user"
    assert "user_id" in result


def test_register_shared_user():
    username = unique_username("regshared")
    result = json_request(
        api_url("/api/auth/register"),
        method="POST",
        payload={
            "username": username,
            "email": unique_email(username),
            "password": "test123456",
            "runtime_mode": "shared",
        },
    )
    assert "access_token" in result
    assert result["username"] == username


def test_register_defaults_to_dedicated():
    username = unique_username("regdef")
    result = json_request(
        api_url("/api/auth/register"),
        method="POST",
        payload={
            "username": username,
            "email": unique_email(username),
            "password": "test123456",
        },
    )
    assert "access_token" in result


def test_register_duplicate_username_fails():
    username = unique_username("dupuser")
    json_request(
        api_url("/api/auth/register"),
        method="POST",
        payload={
            "username": username,
            "email": unique_email(username),
            "password": "test123456",
        },
    )
    try:
        json_request(
            api_url("/api/auth/register"),
            method="POST",
            payload={
                "username": username,
                "email": f"another_{unique_email(username)}",
                "password": "test123456",
            },
        )
        assert False, "Expected error for duplicate username"
    except RuntimeError as exc:
        assert "400" in str(exc) or "账号已存在" in str(exc)


def test_register_duplicate_email_fails():
    email = unique_email(unique_username("dupemail"))
    json_request(
        api_url("/api/auth/register"),
        method="POST",
        payload={"username": unique_username("u1"), "email": email, "password": "test123456"},
    )
    try:
        json_request(
            api_url("/api/auth/register"),
            method="POST",
            payload={"username": unique_username("u2"), "email": email, "password": "test123456"},
        )
        assert False, "Expected error for duplicate email"
    except RuntimeError as exc:
        assert "400" in str(exc) or "邮箱已被注册" in str(exc)


def test_register_any_runtime_mode_accepted():
    """runtime_mode field is accepted but no longer validated — all users are dedicated."""
    username = unique_username("anymode")
    result = json_request(
        api_url("/api/auth/register"),
        method="POST",
        payload={
            "username": username,
            "email": unique_email(username),
            "password": "test123456",
            "runtime_mode": "whatever_ignored",
        },
    )
    assert "access_token" in result


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

def test_login_admin():
    result = json_request(
        api_url("/api/auth/login"),
        method="POST",
        payload={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
    )
    assert "access_token" in result
    assert "refresh_token" in result
    assert result["role"] == "admin"


def test_login_wrong_password():
    try:
        json_request(
            api_url("/api/auth/login"),
            method="POST",
            payload={"username": ADMIN_USERNAME, "password": "wrong_password"},
        )
        assert False, "Expected 401"
    except RuntimeError as exc:
        assert "401" in str(exc)


def test_login_nonexistent_user():
    try:
        json_request(
            api_url("/api/auth/login"),
            method="POST",
            payload={"username": "nonexistent_user_zzz", "password": "x"},
        )
        assert False, "Expected 401"
    except RuntimeError as exc:
        assert "401" in str(exc)


# ---------------------------------------------------------------------------
# Token Refresh
# ---------------------------------------------------------------------------

def test_refresh_token():
    """Login -> refresh -> get new tokens."""
    login_resp = json_request(
        api_url("/api/auth/login"),
        method="POST",
        payload={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
    )
    refresh_result = json_request(
        api_url("/api/auth/refresh"),
        method="POST",
        payload={"refresh_token": login_resp["refresh_token"]},
    )
    assert "access_token" in refresh_result
    assert "refresh_token" in refresh_result


def test_refresh_invalid_token_fails():
    try:
        json_request(
            api_url("/api/auth/refresh"),
            method="POST",
            payload={"refresh_token": "invalid_token"},
        )
        assert False, "Expected 401"
    except RuntimeError as exc:
        assert "401" in str(exc)


# ---------------------------------------------------------------------------
# Me
# ---------------------------------------------------------------------------

def test_get_me():
    token = admin_token()
    result = json_request(api_url("/api/auth/me"), headers=auth_headers(token))
    assert result["username"] == ADMIN_USERNAME
    assert result["role"] == "admin"
    assert "id" in result
    assert "email" in result
    assert "runtime_mode" in result
    assert "quota_tier" in result
    assert "is_active" in result


def test_me_unauthorized():
    try:
        json_request(api_url("/api/auth/me"))
        assert False, "Expected 401/403"
    except RuntimeError as exc:
        assert any(str(code) in str(exc) for code in ("401", "403"))


# ---------------------------------------------------------------------------
# API Token
# ---------------------------------------------------------------------------

def test_generate_api_token():
    token = admin_token()
    result = json_request(
        api_url("/api/auth/api-token"),
        method="POST",
        headers=auth_headers(token),
    )
    assert "api_token" in result
    assert result["expires_in_days"] == 365


# ---------------------------------------------------------------------------
# Change Password
# ---------------------------------------------------------------------------

def test_change_password():
    username = unique_username("chpwd")
    password = "original123"
    reg = json_request(
        api_url("/api/auth/register"),
        method="POST",
        payload={
            "username": username,
            "email": unique_email(username),
            "password": password,
        },
    )
    user_token = reg["access_token"]

    # Change password
    result = json_request(
        api_url("/api/auth/change-password"),
        method="PUT",
        payload={"old_password": password, "new_password": "newpass456"},
        headers=auth_headers(user_token),
    )
    assert result["message"] == "密码修改成功"

    # Old password should fail
    try:
        json_request(
            api_url("/api/auth/login"),
            method="POST",
            payload={"username": username, "password": password},
        )
        assert False, "Expected login failure with old password"
    except RuntimeError as exc:
        assert "401" in str(exc)

    # New password should work
    login_result = json_request(
        api_url("/api/auth/login"),
        method="POST",
        payload={"username": username, "password": "newpass456"},
    )
    assert "access_token" in login_result


def test_change_password_wrong_old():
    token = admin_token()
    try:
        json_request(
            api_url("/api/auth/change-password"),
            method="PUT",
            payload={"old_password": "wrong_old_password", "new_password": "irrelevant123"},
            headers=auth_headers(token),
        )
        assert False, "Expected 400 for wrong old password"
    except RuntimeError as exc:
        assert "400" in str(exc)


def test_change_password_too_short():
    token = admin_token()
    try:
        json_request(
            api_url("/api/auth/change-password"),
            method="PUT",
            payload={"old_password": ADMIN_PASSWORD, "new_password": "ab"},
            headers=auth_headers(token),
        )
        assert False, "Expected 400 for short password"
    except RuntimeError as exc:
        assert "400" in str(exc)
