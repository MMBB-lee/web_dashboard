"""One active process per SQLite file or MySQL schema, including migrations."""
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import text

from .common import CrawlerError, key_for


class AlreadyRunning(CrawlerError):
    pass


@contextmanager
def run_lock(store):
    if store.engine.dialect.name == "mysql":
        name = "anhui-crawler:" + key_for(store.engine.url.database)[:40]
        with store.engine.connect() as connection:
            acquired = connection.scalar(text("SELECT GET_LOCK(:name, 0)"), {"name": name})
            if acquired != 1:
                raise AlreadyRunning("Another crawler process holds this database lock")
            try:
                yield
            finally:
                connection.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": name})
    else:
        database = store.engine.url.database
        lock_path = Path(database).resolve().with_suffix(".crawler.lock") if database and database != ":memory:" else store.root / ".crawler.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as stream:
            stream.seek(0, 2)
            if stream.tell() == 0:
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            if __import__("os").name == "nt":
                import msvcrt
                acquire = lambda: msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                release = lambda: (stream.seek(0), msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1))
            else:
                import fcntl
                acquire = lambda: fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                release = lambda: fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            try:
                acquire()
            except OSError as exc:
                raise AlreadyRunning("Another crawler process holds this database lock") from exc
            try:
                yield
            finally:
                release()
