"""健康检查与统一错误格式测试（验收检查 §10）。"""

from fastapi.testclient import TestClient


def test_health_live_public(client: TestClient):
    resp = client.get("/api/v1/health/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_error_envelope_format(client: TestClient):
    """统一错误格式：{"error": {code, message, details}, "request_id"}。"""
    resp = client.get("/api/v1/catalog/entities")
    assert resp.status_code == 401
    body = resp.json()
    assert set(body) == {"error", "request_id"}
    assert set(body["error"]) == {"code", "message", "details"}
    assert body["error"]["code"] == "unauthorized"
    assert resp.headers.get("X-Request-ID") == body["request_id"]


def test_validation_error_is_400(client: TestClient):
    """参数错误返回 400 而非默认 422（03-接口总览 §1）。"""
    resp = client.post("/api/v1/auth/login", json={"username": "ab", "password": "short"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "bad_request"
