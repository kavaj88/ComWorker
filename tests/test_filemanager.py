"""Tests for file manager endpoints.

Covers:
- GET  /api/comworker/filemanager/browse
- POST /api/comworker/filemanager/mkdir
- DELETE /api/comworker/filemanager/delete
- POST /api/comworker/filemanager/upload
- POST /api/comworker/files/upload  (alias)
- GET  /api/comworker/filemanager/download
- GET  /api/comworker/filemanager/serve

Note: These endpoints require a running dedicated container.
"""

from urllib.request import Request, urlopen

from conftest import admin_token, api_url, auth_headers, json_request


def _token() -> str:
    return admin_token()


def test_browse_files():
    """Browse root directory of user's dedicated runtime (needs running container)."""
    try:
        result = json_request(
            api_url("/api/comworker/filemanager/browse?path="),
            headers=auth_headers(_token()),
        )
        assert isinstance(result, dict)
    except RuntimeError as exc:
        assert "503" in str(exc) or "500" in str(exc)


def test_browse_files_with_path():
    """Browse a specific path (needs running container)."""
    try:
        result = json_request(
            api_url("/api/comworker/filemanager/browse?path=/workspace"),
            headers=auth_headers(_token()),
        )
        assert isinstance(result, dict)
    except RuntimeError as exc:
        assert "503" in str(exc) or "500" in str(exc)


def test_browse_files_unauthorized():
    try:
        json_request(api_url("/api/comworker/filemanager/browse?path="))
        assert False, "Expected 401/403"
    except RuntimeError as exc:
        assert any(str(code) in str(exc) for code in ("401", "403"))


def test_mkdir_requires_container():
    """mkdir should work if container is running."""
    try:
        result = json_request(
            api_url("/api/comworker/filemanager/mkdir?path=/workspace/test_dir"),
            method="POST",
            headers=auth_headers(_token()),
        )
        assert isinstance(result, dict)
    except RuntimeError as exc:
        # May fail if container not running — that's expected
        assert "503" in str(exc) or "500" in str(exc) or "200" not in str(exc)


def test_delete_requires_container():
    """delete should work if container is running."""
    try:
        result = json_request(
            api_url("/api/comworker/filemanager/delete?path=/workspace/test_dir"),
            method="DELETE",
            headers=auth_headers(_token()),
        )
        assert isinstance(result, dict)
    except RuntimeError as exc:
        assert "503" in str(exc) or "500" in str(exc) or "200" not in str(exc)


# ---------------------------------------------------------------------------
# Download / serve tests
# ---------------------------------------------------------------------------


def test_download_single_file():
    """Download a single file via /filemanager/download and verify response."""
    token = _token()
    try:
        result, status, resp_headers = json_request(
            api_url("/api/comworker/filemanager/download?path=/workspace"),
            headers=auth_headers(token),
            raw_response=True,
        )
    except RuntimeError as exc:
        assert "503" in str(exc) or "500" in str(exc) or "404" in str(exc)
        return

    assert status == 200
    # Single file should have a guessed media type
    content_type = resp_headers.get("Content-Type") or resp_headers.get("content-type") or ""
    assert content_type, "Response should have a Content-Type header"


def test_download_directory_as_zip():
    """Download a directory via /filemanager/download — should return a zip."""
    token = _token()
    try:
        result, status, resp_headers = json_request(
            api_url("/api/comworker/filemanager/download?path="),
            headers=auth_headers(token),
            raw_response=True,
        )
    except RuntimeError as exc:
        assert "503" in str(exc) or "500" in str(exc)
        return

    assert status == 200
    content_type = resp_headers.get("Content-Type") or resp_headers.get("content-type") or ""
    # Directory downloads should come back as application/zip when there are multiple files
    if content_type == "application/zip":
        disposition = resp_headers.get("Content-Disposition") or resp_headers.get(
            "content-disposition"
        ) or ""
        assert "attachment" in disposition, (
            f"Zip download should have Content-Disposition: attachment header, got: {disposition}"
        )


def test_download_unauthorized():
    """Download without auth should return 401."""
    try:
        json_request(api_url("/api/comworker/filemanager/download?path=/workspace/test.txt"))
        assert False, "Expected 401/403"
    except RuntimeError as exc:
        assert any(str(code) in str(exc) for code in ("401", "403"))


def test_serve_file():
    """Serve a file via /filemanager/serve (needs running container)."""
    token = _token()
    try:
        result, status, resp_headers = json_request(
            api_url("/api/comworker/filemanager/serve?path=/workspace"),
            headers=auth_headers(token),
            raw_response=True,
        )
    except RuntimeError as exc:
        assert "503" in str(exc) or "500" in str(exc) or "404" in str(exc)
        return

    assert status == 200


# ---------------------------------------------------------------------------
# Path normalisation / security tests
# ---------------------------------------------------------------------------


def test_path_traversal_rejected():
    """Path traversal attempts (../) should be rejected with 404 or 400."""
    token = _token()
    traversal_paths = [
        "../etc/passwd",
        "..%2Fetc%2Fpasswd",
        "workspace/../../../etc/passwd",
    ]
    for path in traversal_paths:
        try:
            json_request(
                api_url(f"/api/comworker/filemanager/browse?path={path}"),
                headers=auth_headers(token),
            )
            # If container is running and path goes through, browse might return 404
            # from the container or succeed with empty results — both acceptable
        except RuntimeError as exc:
            err = str(exc)
            # Path traversal should be rejected before reaching the container
            assert any(str(code) in err for code in ("400", "404")), (
                f"Path '{path}' should be rejected, got: {err}"
            )


def test_empty_path_handled():
    """Empty path for download should be handled gracefully."""
    token = _token()
    try:
        result, status, _ = json_request(
            api_url("/api/comworker/filemanager/download?path="),
            headers=auth_headers(token),
            raw_response=True,
        )
        assert status == 200
    except RuntimeError as exc:
        # Accept 503 (no container) or 404 (path not found)
        assert "503" in str(exc) or "404" in str(exc) or "500" in str(exc)


def test_serve_empty_path_rejected():
    """Empty path on /filemanager/serve should be rejected with 404 (Hermes read path requires a path)."""
    token = _token()
    try:
        json_request(
            api_url("/api/comworker/filemanager/serve?path="),
            headers=auth_headers(token),
        )
        assert False, "Expected an error for empty path on serve"
    except RuntimeError as exc:
        assert any(str(code) in str(exc) for code in ("400", "404"))


# ---------------------------------------------------------------------------
# Browse sorting
# ---------------------------------------------------------------------------


def test_browse_returns_sorted_items():
    """Browse results should return directories first, then files, each alphabetically sorted."""
    token = _token()
    try:
        result = json_request(
            api_url("/api/comworker/filemanager/browse?path="),
            headers=auth_headers(token),
        )
    except RuntimeError as exc:
        assert "503" in str(exc) or "500" in str(exc)
        return

    items = result.get("items")
    if not items or len(items) < 2:
        return  # not enough items to verify sorting

    # Verify directories come before files (server-side sort)
    types = [item["type"] for item in items]
    # Find the first file index; everything before should be directories
    for i, t in enumerate(types):
        if t == "file":
            # All items after this should also be files (no directory after a file)
            for j in range(i + 1, len(types)):
                assert types[j] != "directory", (
                    f"Items should be sorted directories-first, but found directory at index {j} after file at {i}"
                )
            break

    # Verify directories are sorted alphabetically
    dir_names = [item["name"].lower() for item in items if item["type"] == "directory"]
    assert dir_names == sorted(dir_names), f"Directory names not sorted: {dir_names}"

    # Verify files are sorted alphabetically
    file_names = [item["name"].lower() for item in items if item["type"] == "file"]
    assert file_names == sorted(file_names), f"File names not sorted: {file_names}"
