"""Bounded, allowlisted HTTPS requests with robots and same-source redirects."""
from __future__ import annotations

from dataclasses import dataclass
import email.utils
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener
from urllib.robotparser import RobotFileParser
import ssl

import certifi

from .common import CrawlerError, now

USER_AGENT = "AnhuiPublicDataCrawler/0.1 (official-public-statistics; low-rate research)"
MAX_BYTES = 10_000_000
REQUEST_CLOCK = {}


def allowed_url(url, hosts):
    parsed = urlparse(url)
    if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in {None, 443}
            or parsed.hostname not in hosts or parsed.fragment):
        raise CrawlerError("URL/redirect outside configured HTTPS host allowlist")
    return url


class RestrictedRedirect(HTTPRedirectHandler):
    max_redirections = 5

    def __init__(self, hosts, policy_check=None):
        self.hosts = hosts
        self.policy_check = policy_check

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        allowed_url(newurl, self.hosts)
        if urlparse(newurl).hostname != urlparse(request.full_url).hostname:
            raise CrawlerError("Cross-host redirect needs a separately checked source")
        if self.policy_check:
            self.policy_check(newurl)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


@dataclass
class Response:
    url: str
    effective_url: str
    body: bytes
    content_type: str
    status: int
    retrieved_at: str
    etag: str | None = None
    last_modified: str | None = None


class DeferredRequest(CrawlerError):
    def __init__(self, message, retry_at):
        super().__init__(message)
        self.retry_at = retry_at


class Fetcher:
    def __init__(self, hosts, *, interval=2.0, timeout=20, attempts=2):
        self.hosts = frozenset(hosts)
        self.interval, self.timeout, self.attempts = max(2.0, interval), min(30, timeout), min(3, max(1, attempts))
        self.last_request, self.robots = REQUEST_CLOCK, {}
        context = ssl.create_default_context()
        # Add the standard public CA bundle for Python installations lacking OS
        # issuer certificates; never disable hostname/certificate verification.
        context.load_verify_locations(certifi.where())
        self.opener = build_opener(RestrictedRedirect(self.hosts, self._check_redirect_policy),
                                  HTTPSHandler(context=context))

    def _check_redirect_policy(self, url):
        parsed = urlparse(url)
        policy = self.robots.get(f"https://{parsed.netloc}")
        if policy and not policy[0].can_fetch(USER_AGENT, url):
            raise CrawlerError("robots.txt disallows redirected URL")

    def _request(self, url):
        allowed_url(url, self.hosts)
        host = urlparse(url).hostname
        delay = self.interval - (time.monotonic() - self.last_request.get(host, 0))
        if delay > 0:
            time.sleep(delay)
        self.last_request[host] = time.monotonic()
        headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/pdf,text/plain"}
        headers.update(getattr(self, "conditional_headers", {}))
        request = Request(url, headers=headers)
        with self.opener.open(request, timeout=self.timeout) as response:
            allowed_url(response.url, self.hosts)
            size = response.headers.get("Content-Length")
            if size and int(size) > MAX_BYTES:
                raise CrawlerError("Response exceeds configured size limit")
            body = response.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise CrawlerError("Response exceeds configured size limit")
            return Response(url, response.url, body, response.headers.get_content_type(), response.status, now(),
                            response.headers.get("ETag"), response.headers.get("Last-Modified"))

    def _robot_policy(self, url):
        parsed = urlparse(url)
        origin = f"https://{parsed.netloc}"
        if origin not in self.robots:
            robot_url = origin + "/robots.txt"
            try:
                response = self._request(robot_url)
            except HTTPError as exc:
                if exc.code != 404:
                    raise CrawlerError("robots.txt unavailable or restricted; automatic crawl refused") from exc
                content = "User-agent: *\nAllow: /"
                evidence = {"url": robot_url, "http_status": 404, "policy": "no_robots_file"}
            except (URLError, TimeoutError, OSError) as exc:
                raise CrawlerError("robots.txt check failed; no bypass attempted") from exc
            else:
                content = response.body.decode("utf-8", errors="replace")
                if "<html" in content.lower():
                    raise CrawlerError("Unexpected robots response; needs review")
                noncomments = [s for s in content.splitlines() if s.strip() and not s.lstrip().startswith("#")]
                if noncomments and "user-agent:" not in content.lower():
                    raise CrawlerError("Invalid nonempty robots response; needs review")
                evidence = {"url": robot_url, "http_status": response.status, "policy": "parsed"}
            robot = RobotFileParser(robot_url)
            robot.parse(content.splitlines())
            delay = robot.crawl_delay(USER_AGENT) or robot.crawl_delay("*") or 0
            if delay > 30:
                raise CrawlerError("robots crawl delay exceeds synchronous request budget; needs separate scheduling")
            self.interval = max(self.interval, delay)
            self.robots[origin] = robot, evidence
        return self.robots[origin]

    def fetch(self, url, *, etag=None, last_modified=None):
        allowed_url(url, self.hosts)
        self.conditional_headers = {}
        robot, _ = self._robot_policy(url)
        if not robot.can_fetch(USER_AGENT, url):
            raise CrawlerError("robots.txt disallows this URL")
        self.conditional_headers = {}
        if etag:
            self.conditional_headers["If-None-Match"] = etag
        if last_modified:
            self.conditional_headers["If-Modified-Since"] = last_modified
        for attempt in range(self.attempts):
            try:
                return self._request(url)
            except HTTPError as exc:
                if exc.code == 304:
                    return Response(url, url, b"", "", 304, now(), exc.headers.get("ETag"), exc.headers.get("Last-Modified"))
                if exc.code not in {429, 500, 502, 503, 504}:
                    raise CrawlerError(f"HTTP {exc.code}; request stopped without bypass") from exc
                delay = 2 ** (attempt + 1)
                retry = exc.headers.get("Retry-After")
                if retry:
                    if retry.isdigit():
                        delay = max(delay, int(retry))
                    else:
                        try:
                            delay = max(delay, email.utils.parsedate_to_datetime(retry).timestamp() - time.time())
                        except (ValueError, TypeError, AttributeError):
                            raise CrawlerError("Invalid Retry-After requires review") from exc
                if delay > 30:
                    from datetime import datetime, timedelta, timezone
                    retry_at = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
                    raise DeferredRequest("Retry-After requires deferred scheduling; no rapid retry", retry_at) from exc
                if attempt + 1 == self.attempts:
                    from datetime import datetime, timedelta, timezone
                    raise DeferredRequest(f"HTTP {exc.code}; retry budget exhausted",
                        (datetime.now(timezone.utc) + timedelta(seconds=max(delay, 3600))).isoformat()) from exc
                time.sleep(delay)
            except (URLError, TimeoutError, OSError) as exc:
                # TLS errors and connectivity errors are recorded, not evaded.
                raise CrawlerError("HTTPS connection/verification failed; request stopped") from exc
        raise CrawlerError("Request budget exhausted")
