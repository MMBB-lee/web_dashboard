"""测试夹具：内存假数据仓储，模拟 C 的 data_foundation。

不需要 MySQL、不需要 C/E 的包即可运行全部测试。
生产代码通过 app.core.providers.get_auth_store 获取仓储，
测试用 monkeypatch 替换为假实现。
"""

import os

# 测试默认走 SqliteJobStore（无 MySQL / 无 C 包也能跑）。
# 真机/MySQL 冒烟用 ``D_JOB_STORE_BACKEND=c_store`` 显式切到 CStore。
os.environ.setdefault("D_JOB_STORE_BACKEND", "sqlite")

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.providers import SessionInfo, UserRecord
from app.core.security import PasswordService
from app.main import app


class FakeAuthStore:
    """AuthStore 契约的内存实现（仅供测试）。"""

    def __init__(self) -> None:
        self.users: dict[str, UserRecord] = {}
        self.sessions: dict[str, SessionInfo] = {}

    def add_user(self, username: str, password: str, role: str = "viewer", status: str = "active") -> UserRecord:
        record = UserRecord(
            id=f"user-{username}",
            username=username,
            display_name=username.upper(),
            role=role,
            status=status,
            password_hash=PasswordService.hash_password(password),
        )
        self.users[username] = record
        return record

    def get_user_by_username(self, username: str):
        return self.users.get(username)

    def create_session(self, user_id, session_token_hash, csrf_token, expires_at):
        user = next(u for u in self.users.values() if u.id == user_id)
        self.sessions[session_token_hash] = SessionInfo(
            user_id=user_id,
            username=user.username,
            display_name=user.display_name,
            role=user.role,
            csrf_token=csrf_token,
            expires_at=expires_at,
        )

    def get_session(self, session_token_hash):
        info = self.sessions.get(session_token_hash)
        if info is None:
            return None
        if info.expires_at < datetime.now(timezone.utc):
            return None
        return info

    def revoke_session(self, session_token_hash):
        self.sessions.pop(session_token_hash, None)


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """每个测试前后清空登录限速状态，避免跨测试污染。"""
    from app.services import auth_service

    auth_service._rate_limiter._failures.clear()
    yield
    auth_service._rate_limiter._failures.clear()


@pytest.fixture
def fake_store(monkeypatch):
    store = FakeAuthStore()
    store.add_user("admin1", "admin-pass-123", role="admin")
    store.add_user("viewer1", "viewer-pass-123", role="viewer")
    monkeypatch.setattr("app.core.providers.get_auth_store", lambda: store)
    # auth_service 与 auth 路由在模块顶层 from-import，需要一并替换
    monkeypatch.setattr("app.services.auth_service.get_auth_store", lambda: store)
    monkeypatch.setattr("app.api.v1.auth.get_auth_store", lambda: store)
    return store


@pytest.fixture
def client(fake_store):
    return TestClient(app)


def _login(client: TestClient, username: str, password: str):
    return client.post("/api/v1/auth/login", json={"username": username, "password": password})
