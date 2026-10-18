from __future__ import annotations

import calendar
from datetime import date, datetime
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import tempfile
from uuid import uuid4

from sqlalchemy import create_engine, event, inspect, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from .common import CrawlerError, canonical, key_for, now, safe_path, sha256
from .models import Asset, Base, Entity, Fact, Issue, Meta, Revision, Run, Source

SCHEMA_VERSION = "2"


def validate_url(url):
    parsed = make_url(url)
    if parsed.drivername.startswith("sqlite"):
        if parsed.drivername not in {"sqlite", "sqlite+pysqlite"}:
            raise CrawlerError("Unsupported SQLite driver")
    elif parsed.drivername != "mysql+pymysql" or not re.fullmatch(r"anhui_crawler(?:_[A-Za-z0-9_]+)?", parsed.database or ""):
        raise CrawlerError("Use an independent mysql+pymysql schema named anhui_crawler or anhui_crawler_*; business schemas refused")
    return parsed


def validate_fact(record):
    start, end, grain = record["period_start"], record["period_end"], record["grain"]
    if not isinstance(start, date) or not isinstance(end, date) or end < start:
        raise CrawlerError("Invalid statistical period")
    if grain == "month" and (start.day != 1 or end != date(start.year, start.month, calendar.monthrange(start.year, start.month)[1])):
        raise CrawlerError("Monthly facts require a complete calendar month")
    if grain == "day" and start != end:
        raise CrawlerError("Daily period must be one date")
    if grain == "year" and (start != date(start.year, 1, 1) or end != date(start.year, 12, 31)):
        raise CrawlerError("Annual facts require the original full-year period")
    if grain == "quarter" and (start.month not in {1, 4, 7, 10} or start.day != 1 or
            end != date(start.year, start.month + 2, calendar.monthrange(start.year, start.month + 2)[1])):
        raise CrawlerError("Quarterly facts require the original complete quarter")
    if grain not in {"day", "month", "year", "quarter", "holiday", "cumulative"}:
        raise CrawlerError("Unsupported statistical grain")
    value = Decimal(str(record["value"]))
    if not value.is_finite() or value < 0 or value >= Decimal("1e20") or value != value.quantize(Decimal("0.0001")):
        raise CrawlerError("Invalid numeric value or unsupported precision")
    if record["quality"] not in {"validated", "candidate"} or record.get("qualifier") and record["quality"] != "candidate":
        raise CrawlerError("Approximate/qualified facts cannot be marked validated")
    if record["metric"] == "airport_passenger_throughput" and (
            grain != "month" or record["scope"] != "airport_all_passengers"
            or record["unit"] != "人次" or value != int(value)):
        raise CrawlerError("Invalid monthly airport passenger definition")
    if record["metric"] in {"passenger_transport_volume", "rail_passenger_departures", "star_rated_hotels", "a_rated_scenic_sites"}:
        flow = record["metric"] in {"passenger_transport_volume", "rail_passenger_departures"}
        scopes = ({"municipal_rail_not_single_station"} if record["metric"] == "rail_passenger_departures" else
                  {"municipal_road_transport", "municipal_water_transport", "municipal_road_and_water_excluding_rail"})
        valid = (record["entity_id"].startswith("city:") and value == int(value) and
                 ((flow and grain == "year" and record["scope"] in scopes and record["unit"] == "人次") or
                  (not flow and grain == "day" and start.month == 12 and start.day == 31 and
                   record["scope"] == "municipal_year_end_inventory" and
                   record["unit"] in ({"家"} if record["metric"] == "star_rated_hotels" else {"个", "处"}))))
        if not valid:
            raise CrawlerError("Invalid reviewed city metric period, entity, scope or unit")
    from .metrics import validate_metric
    validate_metric(record, value)
    if not record["locator"] or not record["parser_version"] or not record["original_value"]:
        raise CrawlerError("Source location, original value and parser version are required")
    return value


def snapshot_fact(fact):
    return {field: (getattr(fact, field).isoformat() if isinstance(getattr(fact, field), date)
                   else str(getattr(fact, field)) if field == "value" else getattr(fact, field))
            for field in ("id", "entity_id", "source_id", "asset_id", "metric", "scope", "grain",
                          "period_start", "period_end", "value", "unit", "quality", "qualifier",
                          "original_value", "locator", "parser_version", "revision_no")}


class Store:
    def __init__(self, url, data_root):
        parsed = validate_url(url)
        self.root = Path(data_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        if parsed.drivername.startswith("sqlite") and parsed.database not in {None, ":memory:"}:
            Path(parsed.database).resolve().parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(url, pool_pre_ping=True)
        if self.engine.dialect.name == "sqlite":
            @event.listens_for(self.engine, "connect")
            def enforce_foreign_keys(connection, _):
                connection.execute("PRAGMA foreign_keys=ON")

    def initialize(self):
        tables = set(inspect(self.engine).get_table_names())
        expected = set(Base.metadata.tables)
        if not tables <= expected or inspect(self.engine).get_view_names():
            raise CrawlerError("Existing non-crawler schema refused; no team tables are modified")
        if tables and tables != expected:
            raise CrawlerError("Partial/unknown crawler schema; explicit migration/repair required")
        if not tables:
            Base.metadata.create_all(self.engine)
            with Session(self.engine) as session, session.begin():
                session.add(Meta(key="schema_version", value=SCHEMA_VERSION))
        with Session(self.engine) as session:
            row = session.get(Meta, "schema_version")
            if row is None or row.value != SCHEMA_VERSION:
                raise CrawlerError("Unsupported independent crawler schema version")

    def seed(self, registry):
        ids = [e["id"] for e in registry["entities"]]
        sources = [s["id"] for s in registry["sources"]]
        if len(ids) != len(set(ids)) or len(sources) != len(set(sources)):
            raise CrawlerError("Duplicate registry identities")
        if any(not set(e["source_ids"]) <= set(sources) for e in registry["entities"]):
            raise CrawlerError("Entity references an unknown source")
        with Session(self.engine) as session, session.begin():
            for item in registry["sources"]:
                row = session.get(Source, item["id"])
                if row is None:
                    session.add(Source(id=item["id"], url=item["url"], publisher=item["publisher"], details=item))
                elif canonical(row.details) != canonical(item):
                    raise CrawlerError("Existing source registry differs; explicit registry migration required")
            for item in registry["entities"]:
                row = session.get(Entity, item["id"])
                if row is None:
                    session.add(Entity(id=item["id"], name=item["name"], type=item["type"], details=item))
                elif canonical(row.details) != canonical(item):
                    raise CrawlerError("Existing entity registry differs; explicit registry migration required")

    def start_run(self, kind):
        run_id = str(uuid4())
        with Session(self.engine) as session, session.begin():
            session.add(Run(id=run_id, kind=kind, status="running", started_at=now(), summary={}))
        return run_id

    def finish_run(self, run_id, summary):
        with Session(self.engine) as session, session.begin():
            row = session.get(Run, run_id)
            row.status, row.finished_at, row.summary = summary["status"], now(), summary

    def issue(self, run_id, source_id, code, details):
        with Session(self.engine) as session, session.begin():
            session.add(Issue(id=str(uuid4()), run_id=run_id, source_id=source_id,
                              code=code, details=details, created_at=now()))

    def put_bytes(self, raw, media_type):
        digest = sha256(raw)
        suffix = {"application/pdf": ".pdf", "text/html": ".html"}.get(media_type, ".bin")
        relative = f"raw/{digest[:2]}/{digest}/body{suffix}"
        destination = safe_path(self.root, relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if destination.is_file() and sha256(destination.read_bytes()) == digest:
                return relative
            raise CrawlerError("Existing immutable asset is corrupt; refused to overwrite it")
        staging = safe_path(self.root, ".staging")
        staging.mkdir(exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=staging, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
        return relative

    def register_asset(self, item, raw):
        if sha256(raw) != item["sha256"] or len(raw) != item["byte_size"]:
            raise CrawlerError("Input asset integrity check failed")
        # Filesystem I/O is outside SQL transactions.
        relative = self.put_bytes(raw, item["media_type"])
        asset_id = key_for(item["source_id"], item["url"], item["sha256"])
        for field in ("retrieved_at", "published_at"):
            if item.get(field) and datetime.fromisoformat(item[field]).utcoffset() is None:
                raise CrawlerError("Source timestamps require explicit timezones")
        with Session(self.engine) as session, session.begin():
            if session.get(Source, item["source_id"]) is None:
                raise CrawlerError("Asset source is not registered")
            existing = session.get(Asset, asset_id)
            if existing is None:
                session.add(Asset(id=asset_id, source_id=item["source_id"], url=item["url"],
                    sha256=item["sha256"], byte_size=len(raw), relative_path=relative,
                    media_type=item["media_type"], retrieved_at=item["retrieved_at"],
                    published_at=item.get("published_at"),
                    details={k:v for k,v in item.items() if k not in {"path", "relative_path"}}))
            elif existing.published_at is None and item.get("published_at"):
                # Discovery first saves bytes, then obtains the date from the
                # official article. Fill missing provenance, never replace bytes.
                existing.published_at = item["published_at"]
                existing.details = {**existing.details, **{k:v for k,v in item.items() if k not in {"path", "relative_path"}}}
        return asset_id, safe_path(self.root, relative)

    def write_facts(self, run_id, asset_id, records, *, accept_revisions=False):
        values = [validate_fact(r) for r in records]
        counts = {"inserted": 0, "skipped": 0, "revised": 0, "quarantined": 0}
        with Session(self.engine) as session, session.begin():
            asset = session.get(Asset, asset_id)
            if asset is None:
                raise CrawlerError("Unknown asset")
            # Serializes same-source writes on MySQL. A failed batch rolls back together.
            session.scalar(select(Source).where(Source.id == asset.source_id).with_for_update())
            for record, value in zip(records, values):
                entity = session.get(Entity, record["entity_id"])
                if entity is None or asset.source_id not in entity.details["source_ids"]:
                    raise CrawlerError("Fact entity/source mapping is not registered")
                if record["metric"] == "airport_passenger_throughput" and entity.type != "airport":
                    raise CrawlerError("Airport values cannot be assigned to cities or stations")
                fact_id = key_for(record["entity_id"], record["metric"], record["scope"], record["grain"],
                                  record["period_start"].isoformat(), record["period_end"].isoformat(), asset.source_id)
                # MySQL REPEATABLE READ may have a snapshot from reading Asset
                # before waiting for the Source lock. Facts need a current read.
                old = session.scalar(select(Fact).where(Fact.id == fact_id).with_for_update()
                                     .execution_options(populate_existing=True))
                if old is None:
                    session.add(Fact(id=fact_id, source_id=asset.source_id, asset_id=asset_id,
                                     **{**record, "value": value}, revision_no=1))
                    counts["inserted"] += 1
                elif (old.value, old.unit, old.quality, old.qualifier) == (value, record["unit"], record["quality"], record.get("qualifier")):
                    counts["skipped"] += 1
                else:
                    previous = snapshot_fact(old)
                    replacement = {**record, "value": str(value), "asset_id": asset_id}
                    replacement = json.loads(json.dumps(replacement, default=lambda x: x.isoformat()))
                    previous_asset = session.scalar(select(Asset).where(Asset.id == old.asset_id).with_for_update()
                                                    .execution_options(populate_existing=True))
                    stale = (previous_asset.published_at and asset.published_at and
                             datetime.fromisoformat(asset.published_at) < datetime.fromisoformat(previous_asset.published_at))
                    if not accept_revisions or stale:
                        session.add(Issue(id=str(uuid4()), run_id=run_id, source_id=asset.source_id,
                            code="stale_revision" if stale else "conflicting_value_requires_review",
                            details={"fact_id": fact_id, "previous": previous, "candidate": replacement}, created_at=now()))
                        counts["quarantined"] += 1
                        continue
                    revision_id = key_for(fact_id, old.revision_no, asset_id, replacement)
                    session.add(Revision(id=revision_id, fact_id=fact_id, run_id=run_id,
                        previous=previous, replacement=replacement,
                        reason="explicitly accepted source revision", created_at=now()))
                    for name, field_value in record.items():
                        setattr(old, name, field_value)
                    old.value, old.asset_id, old.revision_no = value, asset_id, old.revision_no + 1
                    counts["revised"] += 1
                session.flush()
        return counts

    def export_facts(self, *, include_candidates=False, start=None, end=None):
        if start and end and start > end:
            raise CrawlerError("Fact filter start must not follow end")
        with Session(self.engine) as session:
            statement = select(Fact).order_by(Fact.entity_id, Fact.period_start, Fact.id)
            if not include_candidates:
                statement = statement.where(Fact.quality == "validated", Fact.qualifier.is_(None))
            if start:
                statement = statement.where(Fact.period_end >= start)
            if end:
                statement = statement.where(Fact.period_start <= end)
            output = []
            for row in session.scalars(statement):
                asset = session.get(Asset, row.asset_id)
                item = snapshot_fact(row)
                item.update(source_url=asset.url, source_sha256=asset.sha256,
                            source_published_at=asset.published_at, retrieved_at=asset.retrieved_at)
                output.append(item)
            # SQLite binary and MySQL's default case-insensitive collations order
            # mixed-case entity IDs differently. Public exports must be identical.
            output.sort(key=lambda r: (r["entity_id"], r["period_start"], r["id"]))
            return output

    def statistics(self):
        from sqlalchemy import func
        with Session(self.engine) as session:
            return {table.name: session.scalar(select(func.count()).select_from(table)) for table in Base.metadata.sorted_tables}

    def source_asset_counts(self):
        from sqlalchemy import func
        with Session(self.engine) as session:
            return dict(session.execute(select(Asset.source_id, func.count()).group_by(Asset.source_id)).all())

    def dispose(self):
        self.engine.dispose()
