"""Read-only browsing of capture runs; never expose arbitrary server files."""
from datetime import datetime
from pathlib import Path, PurePosixPath
from urllib.parse import urlencode

from app.core.errors import AppError
from app.services import counting_captures as captures

MEDIA_TYPES = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.webp': 'image/webp'}


def _not_found():
    return AppError(404, 'not_found', '检测图片或文件夹不存在，请刷新图片目录')


def _safe_file(path: Path, base: Path) -> bool:
    try:
        return not path.is_symlink() and path.is_file() and path.resolve().is_relative_to(base.resolve())
    except OSError:
        return False


def _image_files(folder: Path) -> list[Path]:
    return sorted((p for p in folder.iterdir() if p.suffix.lower() in MEDIA_TYPES and _safe_file(p, captures.root())),
                  key=lambda p: p.name.casefold())


def _folder(value: str) -> Path:
    relative = PurePosixPath(value)
    if (not value or '\\' in value or ':' in value or '\x00' in value or relative.is_absolute()
            or len(relative.parts) not in (1, 2) or any(p.startswith('.') for p in relative.parts)):
        raise _not_found()
    base = captures.root().resolve()
    path = base.joinpath(*relative.parts)
    try:
        if any(base.joinpath(*relative.parts[:i]).is_symlink() for i in range(1, len(relative.parts) + 1)):
            raise _not_found()
        if not path.is_dir() or not path.resolve().is_relative_to(base):
            raise _not_found()
    except OSError:
        raise _not_found()
    return path


def folders() -> list[dict]:
    base = captures.root()
    if not base.is_dir():
        return []
    items = []
    for outer in base.iterdir():
        if (outer.is_symlink() or not outer.is_dir() or outer.name.startswith('.')
                or not outer.resolve().is_relative_to(base.resolve())):
            continue
        candidates = [outer]
        candidates.extend(p for p in outer.iterdir() if p.is_dir() and not p.is_symlink() and not p.name.startswith('.'))
        for folder in candidates:
            if not folder.resolve().is_relative_to(base.resolve()):
                continue
            images = _image_files(folder)
            metadata = captures.read_metadata(folder / 'analysis.json')
            if not images and not metadata and not (folder / 'captures.json').is_file():
                continue
            relative = folder.relative_to(base)
            legacy = len(relative.parts) == 2
            modified = datetime.fromtimestamp(folder.stat().st_mtime, captures.CHINA_TIME).isoformat(timespec='seconds')
            items.append(dict(id=relative.as_posix(), name=folder.name if not legacy else '历史记录 · ' + modified[:19].replace('T', ' '),
                              image_count=len(images), created_at=metadata.get('created_at', modified),
                              video_id=metadata.get('video_id', relative.parts[0] if legacy else None),
                              analysis_id=metadata.get('analysis_id', folder.name if legacy else None), legacy=legacy))
    return sorted(items, key=lambda item: (item['created_at'], item['id']), reverse=True)


def images(folder: str) -> list[dict]:
    path = _folder(folder)
    return [dict(name=p.name, byte_size=p.stat().st_size,
                 url='/api/v1/videos/capture-image?' + urlencode(dict(folder=folder, filename=p.name)))
            for p in _image_files(path)]


def image(folder: str, filename: str) -> Path:
    if (not filename or '/' in filename or '\\' in filename or ':' in filename or '\x00' in filename
            or filename.startswith('.') or Path(filename).suffix.lower() not in MEDIA_TYPES):
        raise _not_found()
    path = _folder(folder) / filename
    if not _safe_file(path, captures.root()):
        raise _not_found()
    return path
