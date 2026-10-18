"""H01 认证适配层测试：C 的 uow.auth → D 的 AuthStore 协议映射。

不需要 MySQL：用一个内存假 C 仓储替代 data_foundation.create_store()。
"""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest

from app.services import c_auth_store
from app.services.c_auth_store import DataFoundationAuthStore, derive_csrf_token

USER_ID = "11111111-1111-1111-1111-111111111111"
USERNAME = "admin1"


class _FakeAuthRepo:
    def __init__(self, state):
        self._state = state

    def get_user_by_username(self, username):
        return self._state["users"].get(username)

    def save_session(self, data, now):
        record = {
            "id_hash": data.id_hash,
            "user_id": data.user_id,
            "expires_at": data.expires_at,
            "revoked_at": None,
        }
        self._state["sessions"][data.id_hash] = record
        return dict(record)

    def get_active_session(self, id_hash, now):
        record = self._state["sessions"].get(id_hash)
        if record is None or record["revoked_at"] is not None:
            return None
        user = next(u for u in self._state["users"].values() if u["id"] == record["user_id"])
        return {"session": record, "user": user}

    def revoke_session(self, id_hash, now):
        record = self._state["sessions"].get(id_hash)
        if record is None:
            return False
        record["revoked_at"] = now
        return True


class _FakeUow:
    def __init__(self, state):
        self.auth = _FakeAuthRepo(state)


class _FakeCStore:
    """模拟 C 的 DataStore（read/write 均为上下文管理器）。"""

    def __init__(self, state):
        self._state = state

    @contextmanager
    def read(self):
        yield _FakeUow(self._state)

    @contextmanager
    def write(self):
        yield _FakeUow(self._state)


@pytest.fixture
def c_state():
    return {
        "users": {
            USERNAME: {
                "id": USER_ID,
                "username": USERNAME,
                "display_name": "管理员",
                "password_hash": "$argon2id$fake",
                "role": "admin",
                "status": "active",
            }
        },
        "sessions": {},
    }


@pytest.fixture
def adapter(monkeypatch, c_state):
    monkeypatch.setattr(c_auth_store, "_ephemeral_secret", None)
    return DataFoundationAuthStore(store=_FakeCStore(c_state))


def test_derive_csrf_token_is_stable_and_hash_dependent(monkeypatch):
    from app.core.config import Settings

    monkeypatch.setattr("app.core.config.get_settings", lambda: Settings(csrf_secret="unit-test-secret"))

    a = derive_csrf_token("hash-a")
    assert a == derive_csrf_token("hash-a")
    assert a != derive_csrf_token("hash-b")
    assert len(a) == 32 and all(c in "0123456789abcdef" for c in a)


def test_derive_csrf_token_uses_ephemeral_secret_when_unset(monkeypatch):
    """未配置 CSRF_SECRET 时用进程内一次性密钥，不抛异常且稳定。"""
    from app.core.config import Settings

    monkeypatch.setattr(c_auth_store, "_ephemeral_secret", None)
    monkeypatch.setattr("app.core.config.get_settings", lambda: Settings(csrf_secret=""))

    first = derive_csrf_token("hash-a")
    assert first == derive_csrf_token("hash-a")


def test_adapter_maps_user_record(adapter):
    user = adapter.get_user_by_username(USERNAME)
    assert user is not None
    assert user.id == USER_ID
    assert user.role == "admin"
    assert user.status == "active"
    assert user.password_hash == "$argon2id$fake"
    assert adapter.get_user_by_username("ghost") is None


def test_adapter_session_roundtrip_and_csrf_derivation(adapter):
    expires = datetime.now(UTC) + timedelta(minutes=30)
    adapter.create_session(USER_ID, "digest-1", "ignored-value", expires)

    session = adapter.get_session("digest-1")
    assert session is not None
    assert session.username == USERNAME
    assert session.role == "admin"
    # CSRF 由会话摘要派生：与传入值无关，但可重算、稳定
    assert session.csrf_token == derive_csrf_token("digest-1")
    assert session.csrf_token != "ignored-value"
    assert session.expires_at.tzinfo is not None

    adapter.revoke_session("digest-1")
    assert adapter.get_session("digest-1") is None


def test_providers_picks_adapter_when_only_create_store_exists(monkeypatch, c_state):
    """C 没有 new_auth_store() 时，get_auth_store() 走适配层而不是直接调用工厂。"""
    import app.core.providers as providers

    class _StubPkg:
        @staticmethod
        def create_store(database_url, **kwargs):  # pragma: no cover - 仅验证被识别
            raise AssertionError("不应直接调用 create_store()")

    monkeypatch.setattr(c_auth_store, "_cached_data_store", lambda url: _FakeCStore(c_state))
    monkeypatch.setattr(providers, "_try_import", lambda name: _StubPkg if name == "data_foundation" else None)
    c_auth_store.get_data_foundation_auth_store.cache_clear()

    store = providers.get_auth_store()
    assert isinstance(store, DataFoundationAuthStore)
    assert store.get_user_by_username(USERNAME).role == "admin"
