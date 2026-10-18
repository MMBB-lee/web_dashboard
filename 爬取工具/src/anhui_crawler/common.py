from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath


class CrawlerError(ValueError):
    """Controlled message; never include a database URL or credentials."""


def now():
    return datetime.now(timezone.utc).isoformat()


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def key_for(*values):
    return sha256(canonical(values).encode("utf-8"))


def safe_path(root: Path, relative: str):
    parts = PurePosixPath(relative)
    if (not relative or "\0" in relative or "\\" in relative or parts.is_absolute()
            or parts.as_posix() != relative or any(p in {".", ".."} or ":" in p for p in parts.parts)):
        raise CrawlerError("Unsafe relative data path")
    root = root.resolve()
    target = root
    for part in parts.parts:
        target = target / part
        if target.is_symlink():
            raise CrawlerError("Symlink in managed data path")
    if not target.resolve().is_relative_to(root):
        raise CrawlerError("Data path escapes its root")
    return target


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
