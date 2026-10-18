"""密码与会话令牌安全基元测试。"""

from app.core.security import (
    PasswordService,
    hash_session_token,
    new_csrf_token,
    new_session_token,
)


def test_password_hash_and_verify():
    hashed = PasswordService.hash_password("correct-horse-42")
    assert hashed != "correct-horse-42"
    assert hashed.startswith("$argon2id$")  # Argon2id 算法（OWASP 推荐）
    assert PasswordService.verify_password(hashed, "correct-horse-42")
    assert not PasswordService.verify_password(hashed, "wrong-password")


def test_password_hash_salted():
    """同一密码两次哈希结果不同（随机盐）。"""
    h1 = PasswordService.hash_password("same-password")
    h2 = PasswordService.hash_password("same-password")
    assert h1 != h2


def test_invalid_hash_rejected():
    assert not PasswordService.verify_password("not-a-hash", "whatever")


def test_session_tokens_unique_and_hashed():
    t1, t2 = new_session_token(), new_session_token()
    assert t1 != t2
    # 哈希不可逆推原文；同一令牌哈希稳定
    assert hash_session_token(t1) == hash_session_token(t1)
    assert hash_session_token(t1) != hash_session_token(t2)
    assert t1 not in hash_session_token(t1)


def test_csrf_tokens_unique():
    assert new_csrf_token() != new_csrf_token()
