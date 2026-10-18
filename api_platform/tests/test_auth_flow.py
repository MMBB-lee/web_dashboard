"""登录 / 会话 / 退出 / 权限 / 限速全流程测试（验收检查 §10.1—10.2）。"""

from fastapi.testclient import TestClient

PASSWORD = "viewer-pass-123"


def _login(client, username="viewer1", password=PASSWORD):
    return client.post("/api/v1/auth/login", json={"username": username, "password": password})


def test_login_success_sets_cookie_and_csrf(client: TestClient):
    resp = _login(client)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["user"]["username"] == "viewer1"
    assert data["user"]["role"] == "viewer"
    assert data["csrf_token"]
    assert data["expires_at"]
    set_cookie = resp.headers["set-cookie"]
    assert "HttpOnly" in set_cookie
    assert "samesite=strict" in set_cookie.lower()
    assert "path=/" in set_cookie.lower()


def test_login_wrong_password_unified_401(client: TestClient):
    resp = _login(client, password="wrong-password-1")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


def test_login_nonexistent_user_same_401(client: TestClient):
    """凭据错误统一 401，不区分用户名是否存在。"""
    resp = _login(client, username="ghost-user", password="whatever-123")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


def test_login_bad_origin_rejected(client: TestClient):
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": "viewer1", "password": PASSWORD},
        headers={"Origin": "http://evil.example.com"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "origin_not_allowed"


def test_login_rate_limited_after_failures(client: TestClient):
    for _ in range(5):
        resp = _login(client, password="wrong-password-1")
        assert resp.status_code == 401
    resp = _login(client)  # 第 6 次即使密码正确也 429
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "rate_limited"


def test_me_requires_session(client: TestClient):
    resp = client.get("/api/v1/auth/me")
    assert resp.status_code == 401


def test_me_with_session(client: TestClient):
    _login(client)
    resp = client.get("/api/v1/auth/me")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["user"]["username"] == "viewer1"
    assert data["csrf_token"]


def test_logout_requires_csrf(client: TestClient):
    _login(client)
    resp = client.post("/api/v1/auth/logout")
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "csrf_required"


def test_logout_revokes_session(client: TestClient):
    login_resp = _login(client)
    csrf = login_resp.json()["data"]["csrf_token"]

    resp = client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
    assert resp.status_code == 200
    assert resp.json()["data"]["logged_out"] is True

    # 退出后原会话不可用（验收检查 1）
    resp = client.get("/api/v1/auth/me")
    assert resp.status_code == 401


def test_protected_endpoints_return_401_before_dependency(client: TestClient):
    """数据接口未登录一律 401（优先于 503 依赖未接入）。"""
    paths = [
        "/api/v1/catalog/entities",
        "/api/v1/dashboard/overview?region_code=340100&grain=week",
        "/api/v1/events",
        "/api/v1/videos",
    ]
    for path in paths:
        resp = client.get(path)
        assert resp.status_code == 401, path


def test_viewer_forbidden_on_admin_writes(client: TestClient):
    """viewer 调用管理员接口返回 403（验收检查 2）。"""
    _login(client)
    resp = client.post("/api/v1/events", json={})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"


def test_admin_gets_503_when_data_package_missing(client: TestClient):
    """admin 登录后，数据接口因 C 的包未接入返回 503 dependency_not_available。"""
    _login(client, username="admin1", password="admin-pass-123")
    resp = client.get("/api/v1/catalog/entities")
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "dependency_not_available"
