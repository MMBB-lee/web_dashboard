"""密码与会话令牌安全基元。

- 密码：Argon2id 自适应哈希（argon2-cffi 默认算法），只存哈希，原文不写日志。
- 会话令牌：随机 256 位 URL 安全令牌；服务端只存其 SHA-256 哈希（sessions.id_hash）。
- CSRF 令牌：随机令牌，随会话保存并返回给前端放入 X-CSRF-Token。
参照 OWASP Password Storage / Session Management。
"""

import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

_ph = PasswordHasher()  # argon2-cffi 默认即 Argon2id


class PasswordService:
    """密码哈希与校验。"""

    @staticmethod
    def hash_password(plain: str) -> str:
        return _ph.hash(plain)

    @staticmethod
    def verify_password(password_hash: str, plain: str) -> bool:
        try:
            return _ph.verify(password_hash, plain)
        except (VerifyMismatchError, InvalidHashError):
            return False

    @staticmethod
    def needs_rehash(password_hash: str) -> bool:
        return _ph.check_needs_rehash(password_hash)


def new_session_token() -> str:
    """生成随机会话令牌（写入 Cookie 的原文）。"""
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    """会话令牌的哈希，服务端只保存该值。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_csrf_token() -> str:
    """生成 CSRF 令牌。"""
    return secrets.token_urlsafe(32)
