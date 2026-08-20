"""Tests for admin management endpoints: /api/admin/*

Covers:
- GET  /api/admin/users          (list users)
- POST /api/admin/users          (create user)
- PUT  /api/admin/users/{id}     (update user)
- PUT  /api/admin/users/{id}/password  (reset password)
- POST /api/admin/users/{id}/container/sync|pause|resume
- DELETE /api/admin/users/{id}/container
- POST /api/admin/containers/sync
- GET  /api/admin/usage/summary
- GET  /api/admin/usage/history
- GET  /api/admin/audit
"""

from conftest import (
    ADMIN_USERNAME,
    admin_token,
    api_url,
    auth_headers,
    json_request,
    unique_email,
    unique_username,
)


def _token() -> str:
    return admin_token()


# ---------------------------------------------------------------------------
# User CRUD
# ---------------------------------------------------------------------------

def test_list_users():
    result = json_request(
        api_url("/api/admin/users"),
        headers=auth_headers(_token()),
    )
    assert "items" in result
    assert "total" in result
    assert "page" in result
    assert "page_size" in result
    assert result["page"] == 1
    assert isinstance(result["items"], list)


def test_list_users_with_search():
    result = json_request(
        api_url(f"/api/admin/users?search={ADMIN_USERNAME}"),
        headers=auth_headers(_token()),
    )
    assert "items" in result
    assert result["total"] >= 1
    usernames = [u["username"] for u in result["items"]]
    # All returned usernames should contain the search term (case-insensitive)
    assert all(ADMIN_USERNAME.lower() in u.lower() for u in usernames)


def test_list_users_pagination():
    result = json_request(
        api_url("/api/admin/users?page=1&page_size=5"),
        headers=auth_headers(_token()),
    )
    assert result["page_size"] == 5
    assert len(result["items"]) <= 5


def test_create_user():
    username = unique_username("admincr")
    result = json_request(
        api_url("/api/admin/users"),
        method="POST",
        payload={
            "username": username,
            "email": unique_email(username),
            "password": "test12345678",
            "role": "user",
            "quota_tier": "free",
            "runtime_mode": "dedicated",
        },
        headers=auth_headers(_token()),
    )
    assert result["ok"] is True
    assert "user_id" in result

    # Verify the user appears in list
    users = json_request(
        api_url(f"/api/admin/users?search={username}"),
        headers=auth_headers(_token()),
    )
    assert users["total"] >= 1


def test_create_user_missing_fields():
    try:
        json_request(
            api_url("/api/admin/users"),
            method="POST",
            payload={"username": "", "email": "", "password": "short"},
            headers=auth_headers(_token()),
        )
        assert False, "Expected 400"
    except RuntimeError as exc:
        assert "400" in str(exc)


def test_create_user_duplicate_username():
    username = unique_username("dupcheck")
    json_request(
        api_url("/api/admin/users"),
        method="POST",
        payload={
            "username": username,
            "email": unique_email(username),
            "password": "test12345678",
        },
        headers=auth_headers(_token()),
    )
    try:
        json_request(
            api_url("/api/admin/users"),
            method="POST",
            payload={
                "username": username,
                "email": f"other_{unique_email(username)}",
                "password": "test12345678",
            },
            headers=auth_headers(_token()),
        )
        assert False, "Expected 409"
    except RuntimeError as exc:
        assert any(str(code) in str(exc) for code in ("409", "400"))


def test_update_user():
    username = unique_username("upduser")
    create = json_request(
        api_url("/api/admin/users"),
        method="POST",
        payload={
            "username": username,
            "email": unique_email(username),
            "password": "test12345678",
            "quota_tier": "free",
        },
        headers=auth_headers(_token()),
    )
    user_id = create["user_id"]

    # Update quota_tier
    result = json_request(
        api_url(f"/api/admin/users/{user_id}"),
        method="PUT",
        payload={"quota_tier": "pro"},
        headers=auth_headers(_token()),
    )
    assert result["ok"] is True

    # Verify via users list
    users = json_request(
        api_url(f"/api/admin/users?search={username}"),
        headers=auth_headers(_token()),
    )
    updated = users["items"][0]
    assert updated["quota_tier"] == "pro"


def test_update_nonexistent_user():
    try:
        json_request(
            api_url("/api/admin/users/nonexistent-id"),
            method="PUT",
            payload={"quota_tier": "pro"},
            headers=auth_headers(_token()),
        )
        assert False, "Expected 404"
    except RuntimeError as exc:
        assert "404" in str(exc)


def test_reset_user_password():
    username = unique_username("pwdreset")
    create = json_request(
        api_url("/api/admin/users"),
        method="POST",
        payload={
            "username": username,
            "email": unique_email(username),
            "password": "test12345678",
        },
        headers=auth_headers(_token()),
    )
    user_id = create["user_id"]

    result = json_request(
        api_url(f"/api/admin/users/{user_id}/password"),
        method="PUT",
        payload={"new_password": "newtestpass999"},
        headers=auth_headers(_token()),
    )
    assert "message" in result
    assert "updated" in result["message"].lower() or "Password" in result["message"]


# ---------------------------------------------------------------------------
# Container management
# ---------------------------------------------------------------------------

def test_sync_all_containers():
    result = json_request(
        api_url("/api/admin/containers/sync"),
        method="POST",
        headers=auth_headers(_token()),
    )
    assert "updated" in result
    assert "message" in result


def test_container_operations_on_nonexistent_user():
    """Container endpoints on non-existent user should return 404."""
    try:
        json_request(
            api_url("/api/admin/users/nonexistent-id/container/sync"),
            method="POST",
            headers=auth_headers(_token()),
        )
        assert False, "Expected 404"
    except RuntimeError as exc:
        assert "404" in str(exc)


# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------

def test_usage_summary():
    result = json_request(
        api_url("/api/admin/usage/summary"),
        headers=auth_headers(_token()),
    )
    assert "total_tokens_today" in result
    assert "total_users" in result
    assert "active_containers" in result


def test_usage_history():
    result = json_request(
        api_url("/api/admin/usage/history?days=7"),
        headers=auth_headers(_token()),
    )
    assert "daily" in result
    assert "by_model" in result
    assert isinstance(result["daily"], list)
    assert isinstance(result["by_model"], list)


def test_usage_history_with_user_filter():
    result = json_request(
        api_url("/api/admin/usage/history?days=7&user_id=admin"),
        headers=auth_headers(_token()),
    )
    assert "daily" in result


# ---------------------------------------------------------------------------
# Audit logs
# ---------------------------------------------------------------------------

def test_audit_logs():
    result = json_request(
        api_url("/api/admin/audit"),
        headers=auth_headers(_token()),
    )
    assert "items" in result
    assert "total" in result
    assert "page" in result
    assert isinstance(result["items"], list)


def test_audit_logs_filtered():
    result = json_request(
        api_url("/api/admin/audit?action=login&page_size=5"),
        headers=auth_headers(_token()),
    )
    assert "items" in result
    for item in result["items"]:
        assert item["action"] == "login"


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------

def test_admin_endpoints_require_admin():
    """Non-admin users should get 403 from admin endpoints."""
    username = unique_username("regular")
    reg = json_request(
        api_url("/api/auth/register"),
        method="POST",
        payload={
            "username": username,
            "email": unique_email(username),
            "password": "test123456",
        },
    )
    user_token = reg["access_token"]

    try:
        json_request(
            api_url("/api/admin/users"),
            headers=auth_headers(user_token),
        )
        assert False, "Expected 403 for non-admin"
    except RuntimeError as exc:
        assert "403" in str(exc) or "401" in str(exc)
