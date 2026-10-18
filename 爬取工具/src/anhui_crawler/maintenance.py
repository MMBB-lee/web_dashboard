"""Explicit, backed-up upgrades and portable internal snapshots.

Snapshots contain originals and are private recovery material, not public exports.
Callers hold run_lock for the entire operation.
"""
from datetime import date
import json
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile, ZIP_DEFLATED

from sqlalchemy import Date, Numeric, inspect, select
from sqlalchemy.orm import Session

from .common import CrawlerError, canonical, now, safe_path, sha256, write_json
from .models import Asset, Base, Entity, Meta, Source
from .storage import SCHEMA_VERSION

NEW_TABLES = {"ac_source_state", "ac_fetch_state"}
LEGACY_TABLES = set(Base.metadata.tables) - NEW_TABLES


def schema_version(store):
    inspector = inspect(store.engine)
    tables = set(inspector.get_table_names())
    if inspector.get_view_names() or not tables <= set(Base.metadata.tables):
        raise CrawlerError("Unknown/non-crawler schema refused")
    if not tables:
        return None
    if not LEGACY_TABLES <= tables:
        raise CrawlerError("Partial legacy schema refused")
    for name in tables:
        actual = {c["name"] for c in inspector.get_columns(name)}
        if actual != set(Base.metadata.tables[name].columns.keys()):
            raise CrawlerError("Unknown crawler table columns refused")
    with store.engine.connect() as connection:
        return connection.scalar(select(Meta.value).where(Meta.key == "schema_version"))


def backup(store, destination):
    version = schema_version(store)
    if version not in {"1", SCHEMA_VERSION}:
        raise CrawlerError("Backup requires a recognized crawler database")
    destination = Path(destination)
    if destination.exists():
        raise CrawlerError("Backup destination already exists")
    tables = set(inspect(store.engine).get_table_names())
    rows = {}
    # The process lock excludes CLI writers. One transaction fixes the DB view.
    with store.engine.connect() as connection, connection.begin():
        for table in Base.metadata.sorted_tables:
            if table.name in tables:
                rows[table.name] = [dict(r) for r in connection.execute(select(table)).mappings()]
    assets = rows["ac_assets"]
    controls = {}
    for pattern in ("audit.json", "audits/*.json", "migrations/*.json", "runs/*.json"):
        for path in store.root.glob(pattern):
            relative = path.relative_to(store.root).as_posix()
            raw = safe_path(store.root, relative).read_bytes()
            controls[relative] = {"sha256": sha256(raw), "byte_size": len(raw)}
    payload = {"format": "anhui-crawler-backup-1", "created_at": now(), "schema_version": version,
               "private_originals_included": True, "tables": rows, "controls": controls}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()
    # Validate every original before creating a snapshot file.
    for asset in assets:
        path = safe_path(store.root, asset["relative_path"])
        if path.stat().st_size != asset["byte_size"] or sha256(path.read_bytes()) != asset["sha256"]:
            raise CrawlerError("Cannot back up corrupt original")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with ZipFile(destination, "x", ZIP_DEFLATED) as archive:
            archive.writestr("database.json", encoded)
            archive.writestr("database.sha256", sha256(encoded))
            for relative in controls:
                archive.write(safe_path(store.root, relative), "controls/" + relative)
            written = set()
            for asset in assets:
                name = asset["relative_path"]
                if name not in written:
                    archive.write(safe_path(store.root, name), name)
                    written.add(name)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return {"status": "backed_up", "schema_version": version, "file": destination.name,
            "sha256": sha256(destination.read_bytes()), "rows": {k: len(v) for k, v in rows.items()}}


def restore(store, snapshot):
    if inspect(store.engine).get_table_names() or inspect(store.engine).get_view_names():
        raise CrawlerError("Restore requires an empty independent database")
    if any(store.root.iterdir()):
        # SQLite may have opened an empty database and the lock lives beside it.
        database = store.engine.url.database
        allowed = {Path(database).resolve()} if database and store.engine.dialect.name == "sqlite" else set()
        if database and store.engine.dialect.name == "sqlite":
            allowed.add(Path(database).resolve().with_suffix(".crawler.lock"))
        allowed.add((store.root / ".crawler.lock").resolve())
        if any(p.resolve() not in allowed for p in store.root.iterdir()):
            raise CrawlerError("Restore requires an empty managed data directory")
    with ZipFile(snapshot) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise CrawlerError("Duplicate backup entries refused")
        if any(i.file_size > 100_000_000 for i in archive.infolist()) or sum(i.file_size for i in archive.infolist()) > 2_000_000_000:
            raise CrawlerError("Backup exceeds recovery limits")
        encoded = archive.read("database.json")
        if sha256(encoded) != archive.read("database.sha256").decode():
            raise CrawlerError("Backup database digest mismatch")
        payload = json.loads(encoded)
        if payload.get("format") != "anhui-crawler-backup-1" or payload.get("schema_version") not in {"1", SCHEMA_VERSION}:
            raise CrawlerError("Unsupported backup format")
        expected = set(payload["tables"])
        if (not LEGACY_TABLES <= expected <= set(Base.metadata.tables) or
                payload["schema_version"] == SCHEMA_VERSION and expected != set(Base.metadata.tables)):
            raise CrawlerError("Unexpected backup table set")
        # Verify all bytes/paths before inserting rows or materializing originals.
        originals = {}
        for a in payload["tables"]["ac_assets"]:
            name = a["relative_path"]
            safe_path(store.root, name)
            if not name.startswith("raw/"):
                raise CrawlerError("Original outside raw directory")
            raw = archive.read(name)
            if len(raw) != a["byte_size"] or sha256(raw) != a["sha256"]:
                raise CrawlerError("Backup original digest mismatch")
            originals[name] = raw
        controls = {}
        for relative, descriptor in payload.get("controls", {}).items():
            safe_path(store.root, relative)
            if relative != "audit.json" and not (relative.split('/')[0] in {"audits", "migrations", "runs"} and relative.endswith('.json') and len(relative.split('/')) == 2):
                raise CrawlerError("Unexpected recovery control path")
            raw = archive.read("controls/" + relative)
            if len(raw) != descriptor["byte_size"] or sha256(raw) != descriptor["sha256"]:
                raise CrawlerError("Recovery control digest mismatch")
            controls[relative] = raw
        if set(names) != {"database.json", "database.sha256", *originals, *("controls/" + name for name in controls)}:
            raise CrawlerError("Unexpected backup files")
    for table in Base.metadata.sorted_tables:
        if table.name in expected:
            table.create(store.engine)
    from decimal import Decimal
    with store.engine.begin() as connection:
        for table in Base.metadata.sorted_tables:
            rows = payload["tables"].get(table.name, [])
            for row in rows:
                if set(row) != set(table.columns.keys()):
                    raise CrawlerError("Backup row columns differ")
                for column in table.columns:
                    if row[column.name] is not None and isinstance(column.type, Date):
                        row[column.name] = date.fromisoformat(row[column.name])
                    elif row[column.name] is not None and isinstance(column.type, Numeric):
                        row[column.name] = Decimal(row[column.name])
            if rows:
                connection.execute(table.insert(), rows)
    for relative, raw in {**originals, **controls}.items():
        target = safe_path(store.root, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    return {"status": "restored", "schema_version": payload["schema_version"],
            "originals": len(originals), "explicit_migrate_required": payload["schema_version"] != SCHEMA_VERSION}


def migrate(store, registry, backup_dir):
    version = schema_version(store)
    if version not in {"1", SCHEMA_VERSION}:
        raise CrawlerError("Only recognized existing crawler databases can migrate")
    # Validate IDs and disallow implicit removal/retyping of established identities.
    sources = {s["id"]: s for s in registry["sources"]}
    entities = {e["id"]: e for e in registry["entities"]}
    if len(sources) != len(registry["sources"]) or len(entities) != len(registry["entities"]):
        raise CrawlerError("Duplicate registry identities")
    if any(not set(e["source_ids"]) <= sources.keys() for e in entities.values()):
        raise CrawlerError("Unknown registry source reference")
    with Session(store.engine) as session:
        old_sources = list(session.scalars(select(Source)))
        old_entities = list(session.scalars(select(Entity)))
        if any(s.id not in sources for s in old_sources) or any(e.id not in entities or entities[e.id]["type"] != e.type for e in old_entities):
            raise CrawlerError("Migration cannot remove/retype existing identities")
        changed = version != SCHEMA_VERSION or any(canonical(s.details) != canonical(sources[s.id]) for s in old_sources) or any(canonical(e.details) != canonical(entities[e.id]) for e in old_entities)
        changed = changed or len(sources) != len(old_sources) or len(entities) != len(old_entities)
    if not changed:
        return {"status": "up_to_date", "schema_version": SCHEMA_VERSION}
    stamp = now().replace(":", "").replace("+", "_")
    receipt = backup(store, Path(backup_dir) / f"before-upgrade-{stamp}-{uuid4().hex[:8]}.zip")
    # MySQL DDL may commit; version remains 1 until all new tables exist. A rerun
    # explicitly resumes a partial upgrade, while initialize continues refusing it.
    for table in Base.metadata.sorted_tables:
        if table.name in NEW_TABLES:
            table.create(store.engine, checkfirst=True)
    with Session(store.engine) as session, session.begin():
        for s in old_sources:
            item = sources[s.id]
            row = session.get(Source, s.id)
            row.url, row.publisher, row.details = item["url"], item["publisher"], item
        for e in old_entities:
            item = entities[e.id]
            row = session.get(Entity, e.id)
            row.name, row.details = item["name"], item
        session.get(Meta, "schema_version").value = SCHEMA_VERSION
        history = session.get(Meta, "migration_history")
        entry = {"at": now(), "from": version, "to": SCHEMA_VERSION, "backup_sha256": receipt["sha256"],
                 "registry_sha256": sha256(canonical(registry).encode()), "previous_report": history.value if history else None}
        # The original meta value is VARCHAR(255); persist full history as a managed report.
        report = store.root / "migrations" / f"{uuid4().hex}.json"
        write_json(report, entry)
        if history is None:
            session.add(Meta(key="migration_history", value=report.relative_to(store.root).as_posix()))
        else:
            history.value = report.relative_to(store.root).as_posix()
    store.seed(registry)
    return {"status": "migrated", "from": version, "schema_version": SCHEMA_VERSION, "backup": receipt}
