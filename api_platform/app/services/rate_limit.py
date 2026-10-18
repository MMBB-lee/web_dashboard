"""登录失败限速（内存实现，单进程）。

03-接口总览 §3：登录过频返回 429。同一用户名 + 客户端 IP 在时间窗内
连续失败达到上限后，窗口期内拒绝再次尝试（无论后续密码是否正确）。
生产部署多进程时可替换为共享存储实现，接口保持不变。
"""

import time
from collections import defaultdict, deque
from threading import Lock


class LoginRateLimiter:
    def __init__(self, max_failures: int, window_seconds: int) -> None:
        self._max_failures = max_failures
        self._window = window_seconds
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def _key(self, username: str, client_ip: str) -> str:
        return f"{username}|{client_ip}"

    def is_blocked(self, username: str, client_ip: str) -> bool:
        key = self._key(username, client_ip)
        now = time.monotonic()
        with self._lock:
            failures = self._failures.get(key)
            if not failures:
                return False
            while failures and now - failures[0] > self._window:
                failures.popleft()
            return len(failures) >= self._max_failures

    def record_failure(self, username: str, client_ip: str) -> None:
        key = self._key(username, client_ip)
        with self._lock:
            self._failures[key].append(time.monotonic())

    def record_success(self, username: str, client_ip: str) -> None:
        key = self._key(username, client_ip)
        with self._lock:
            self._failures.pop(key, None)


class SlidingWindowRateLimiter:
    """通用滑动窗口限速（内存实现，单进程）。

    03-接口总览 §19：/assistant/chat 需限速。按会话用户限流，
    窗口内超过上限返回 429（生产多进程可换共享存储实现）。
    """

    def __init__(self, max_requests: int, window_seconds: int) -> None:
        self._max_requests = max_requests
        self._window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def allow(self, key: str) -> bool:
        """记录一次请求并返回是否放行。"""
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > self._window:
                hits.popleft()
            if len(hits) >= self._max_requests:
                return False
            hits.append(now)
            return True


# /assistant/chat 的会话级限速器（03 契约：限速；上限可按运营调整）
chat_limiter = SlidingWindowRateLimiter(max_requests=20, window_seconds=60)
