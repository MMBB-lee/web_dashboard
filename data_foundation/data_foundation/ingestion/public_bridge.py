"""Read crawler SQLite in read-only mode; preserve all facts and promote compatible ones.

The bridge never changes an existing observation or chooses between conflicting
sources. Non-comparable periods/units and candidates remain in public_data_facts.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

import sqlalchemy as sa

from ..contracts import AssetCreate, EntityCreate, ObservationInput, SourceCreate
from ..db.base import utcnow_naive
from ..db.models import Entity, Metric, Observation, Source, SourceAsset
from ..db.public_data import public_data_facts as facts_table, public_data_imports as imports_table
from ..errors import Conflict, InvalidArgument
from .real_dataset import validate_real_dataset, package_path


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def stamp(value):
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise InvalidArgument("来源时间戳缺少时区")
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def verify_asset(root, asset):
    path = package_path(root, asset["relative_path"])
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    if h.hexdigest() != asset["sha256"] or path.stat().st_size != asset["byte_size"]:
        raise InvalidArgument("爬虫原件摘要或大小不一致", asset_id=asset["id"])
    return path


def load_records(crawler_root: Path, manifest_path: Path):
    """Offline validation, including all source bytes; no business DB connection."""
    root = crawler_root.resolve()
    db = root / "crawler.sqlite3"
    if not db.is_file():
        raise InvalidArgument("找不到爬虫SQLite数据库", path=str(db))
    records = []
    with sqlite3.connect(db.as_uri() + "?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        entities = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM ac_entities")}
        sources = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM ac_sources")}
        assets = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM ac_assets")}
        paths = {}
        for row in conn.execute("SELECT *, CAST(value AS TEXT) AS exact_value FROM ac_facts ORDER BY id"):
            raw = dict(row)
            asset = assets[raw["asset_id"]]
            if asset["id"] not in paths:
                paths[asset["id"]] = verify_asset(root, asset)
            entity, source = entities[raw["entity_id"]], sources[raw["source_id"]]
            if asset["source_id"] != source["id"]:
                raise InvalidArgument("事实和原件来源不一致")
            details = json.loads(source["details"])
            records.append(make_record("crawler", raw["id"], entity, source,
                asset, paths[asset["id"]], raw, raw["metric"], raw["scope"],
                raw["grain"], raw["period_start"], raw["period_end"], raw["exact_value"],
                raw["unit"], raw["quality"], raw["qualifier"], json.loads(raw["locator"]),
                details.get("title", source["id"])))
    manifest_path = manifest_path.resolve()
    validate_real_dataset(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    base = manifest_path.parent
    entities = {r["id"]: r for r in manifest["catalog"]["entities"]}
    sources = {r["id"]: r for r in manifest["catalog"]["sources"]}
    assets = {r["key"]: r for r in manifest["assets"]}
    supplementary = json.loads(package_path(base, manifest["supplementary_facts"]["path"]).read_text(encoding="utf-8"))["facts"]
    # Include the 83 baseline records too: everything is discoverable in one fact archive.
    for kind, rows in [("c_observation", manifest["observations"]), ("c_supplement", supplementary)]:
        for raw in rows:
            asset = assets[raw["asset_key"]]
            source = sources[raw["source_id"]]
            key = digest({k: raw.get(k) for k in ["entity_id", "metric_code", "grain", "period_start", "period_end", "asset_key", "source_locator"]})
            normalized_asset = {**asset, "id": asset["key"], "relative_path": asset["path"]}
            records.append(make_record(kind, key, entities[raw["entity_id"]], source,
                normalized_asset, package_path(base, asset["path"]), raw,
                raw["metric_code"], raw.get("scope_note", "published_original_scope"),
                raw["grain"], raw["period_start"], raw["period_end"], raw["standard_value"],
                raw["standard_unit"], raw["quality_status"], raw.get("qualifier"),
                {"locator": raw["source_locator"], "raw_excerpt": raw.get("raw_excerpt")}, asset["title"]))
    return records


def make_record(origin, key, entity, source, asset, path, raw, metric, scope,
                grain, start, end, value, unit, quality, qualifier, locator, title):
    number = Decimal(str(value))
    start_date, end_date = date.fromisoformat(start), date.fromisoformat(end)
    if not number.is_finite() or number < 0 or number >= Decimal("1e20") or number != number.quantize(Decimal("0.0001")) or end_date < start_date:
        raise InvalidArgument("导入事实数值或周期无效")
    status = "verified" if quality in {"validated", "verified"} and not qualifier else "pending_review"
    return {"id": digest([origin, key]), "origin": origin, "origin_key": key,
        "entity_key": entity["id"], "entity_name": entity["name"], "entity_type": entity["type"],
        "metric_code": metric, "scope": scope, "grain": grain, "period_start": start_date,
        "period_end": end_date, "value": number, "unit": unit, "quality_status": status,
        "qualifier": qualifier, "source_url": asset["url"], "source_title": title,
        "published_at": stamp(asset.get("published_at")),
        "provenance": {"source_id": source["id"], "source_sha256": asset["sha256"],
            "source_locator": locator, "source_published_at": asset.get("published_at"),
            "retrieved_at": asset["retrieved_at"], "original_quality": quality},
        "raw_record": json.loads(canonical(raw)),
        "_entity": entity, "_source": source, "_asset": asset, "_path": path}


METRIC_MAP = {"airport_passenger_throughput": "airport_throughput",
    "station_passenger_departures": "rail_departures",
    "airport_throughput": "airport_throughput", "rail_departures": "rail_departures",
    "scenic_visits": "scenic_visits"}


def promotion_reason(row):
    if row["quality_status"] != "verified":
        return "pending_review"
    if row["grain"] not in {"day", "week", "month", "holiday"}:
        return "original_period_preserved"
    if row["metric_code"] not in METRIC_MAP or row["unit"] != "人次":
        return "original_metric_preserved"
    if row["entity_type"] not in {"airport", "rail_station", "scenic"}:
        return "background_fact_preserved"
    return "eligible"


def preview(records):
    return {"status": "preview", "total_records": len(records),
        "origins": dict(Counter(r["origin"] for r in records)),
        "quality": dict(Counter(r["quality_status"] for r in records)),
        "promotion": dict(Counter(promotion_reason(r) for r in records)),
        "note": "预览不连接业务数据库、不复制原件；全部事实将保留原周期和质量。"}


def promote(uow, row, asset_ref):
    """Idempotent additions only. Conflicting existing values require review."""
    metric = METRIC_MAP[row["metric_code"]]
    raw = row["raw_record"]
    original_value = str(raw.get("original_value", row["value"]))
    original_unit = str(raw.get("original_unit", row["unit"]))
    factor = Decimal(str(raw.get("conversion_factor", "1")))
    if row["origin"] == "crawler":
        import re
        match = re.fullmatch(r"([\d.,]+)(万人次|万人|人次|人)", original_value)
        if match:
            original_value, original_unit = match[1].replace(",", ""), match[2]
            factor = Decimal(10000 if match[2].startswith("万") else 1)
        elif re.fullmatch(r"[\d,]+(?:\.\d+)?", original_value) and Decimal(original_value.replace(",", "")) == row["value"]:
            original_value, original_unit, factor = original_value.replace(",", ""), row["unit"], Decimal(1)
        else:
            return None, "original_unit_requires_review"
    if Decimal(original_value) * factor != row["value"]:
        return None, "original_unit_requires_review"
    entities = uow.session.scalars(sa.select(Entity).where(Entity.name == row["entity_name"], Entity.type == row["entity_type"])).all()
    if len(entities) > 1:
        return None, "ambiguous_entity"
    if entities:
        entity_id = entities[0].id
    else:
        entity_id = str(uuid5(NAMESPACE_URL, "public-entity:" + row["entity_key"]))
        region = row["_entity"].get("region_code") or json.loads(row["_entity"].get("details", "{}") if isinstance(row["_entity"].get("details", {}), str) else canonical(row["_entity"].get("details", {}))).get("region_code") or "340000"
        uow.catalog.create_entity(EntityCreate(entity_id, row["entity_name"], row["entity_type"], region))
    # Match the actual original publication, not crawler-internal IDs.
    start, end = row["period_start"], row["period_end"]
    existing_rows = uow.session.scalars(sa.select(Observation).where(
        Observation.entity_id == entity_id, Observation.metric_code == metric,
        Observation.grain == row["grain"], Observation.period_start == start,
        Observation.period_end == end)).all()
    for existing in existing_rows:
        source = uow.session.get(Source, existing.source_id)
        original_asset = uow.session.get(SourceAsset, existing.asset_id)
        same_original = original_asset and original_asset.sha256 == row["_asset"]["sha256"]
        if same_original or (source and source.url == row["source_url"]):
            if existing.value == row["value"] and existing.unit == row["unit"]:
                return existing.id, "already_in_observations"
            return None, "conflicting_existing_value"
    if existing_rows:
        return None, "alternative_source_preserved"
    source_id = str(uuid5(NAMESPACE_URL, "public-source:" + row["source_url"] + ":" + row["grain"]))
    if not uow.session.get(Source, source_id):
        source = row["_source"]
        uow.provenance.register_source(SourceCreate(id=source_id,
            publisher=source.get("publisher", "原发布单位"), title=row["source_title"][:300],
            url=row["source_url"], usage_note="公开事实；使用条件及原始范围见public_data_facts",
            update_frequency="manual", native_grain=row["grain"], verification_status="verified"))
    asset = row["_asset"]
    registered = uow.provenance.register_asset(AssetCreate(source_id=source_id,
        url=row["source_url"], title=row["source_title"][:300],
        retrieved_at=datetime.fromisoformat(asset["retrieved_at"].replace("Z", "+00:00")),
        published_at=datetime.fromisoformat(asset["published_at"].replace("Z", "+00:00")) if asset.get("published_at") else None,
        relative_path=asset_ref["relative_path"], sha256=asset_ref["sha256"], byte_size=asset_ref["byte_size"],
        media_type=asset["media_type"], usage_note="保留原件和发布范围"))
    run = uow.observations.start_run(source_id=source_id, asset_id=registered["id"], dry_run=False, reason="独立爬虫事实转换导入")
    data = ObservationInput(entity_id=entity_id, metric_code=metric, period_start=start,
        period_end=end, grain=row["grain"], value=row["value"], unit=row["unit"],
        original_value=original_value, original_unit=original_unit, conversion_factor=factor,
        source_id=source_id, asset_id=registered["id"],
        source_locator=canonical(row["provenance"]["source_locator"])[:500],
        methodology_version="public-bridge-v1", published_at=row["published_at"].replace(tzinfo=timezone.utc) if row["published_at"] else None)
    action, saved = uow.observations.add_or_revise(data, run.id)
    uow.observations.finish_run(run.id, counts={"processed":1,"imported":int(action=="imported"),"revised":int(action=="revised"),"skipped":int(action=="skipped"),"quarantined":0})
    return saved["id"], action


def apply_import(store, records):
    if not sa.inspect(store.engine).has_table("public_data_facts"):
        raise InvalidArgument("请先执行alembic upgrade head，创建public_data_facts")
    result = preview(records) | {"status": "imported", "note": "事实保存到public_data_facts；符合规则的数据关联或新增到observations。", "archive": {"inserted":0,"updated":0,"skipped":0}, "business": {}}
    business = Counter()
    import_id = str(uuid4())
    # Managed original copies are persisted before the single DB transaction.
    asset_refs = {}
    for row in records:
        sha = row["_asset"]["sha256"]
        if sha not in asset_refs:
            with row["_path"].open("rb") as handle:
                asset_refs[sha] = store.persist_file(source_id="public-import", original_name=row["_path"].name, stream=handle)
    with store.write() as uow:
        from importlib.resources import files
        from ..contracts import MetricCreate
        defaults = json.loads(files("data_foundation.defaults").joinpath("metric_dictionary.json").read_text(encoding="utf-8"))
        for item in defaults["metrics"]:
            if not uow.session.get(Metric, item["code"]):
                uow.catalog.upsert_metric(MetricCreate(**item))
        uow.session.execute(imports_table.insert().values(id=import_id, created_at=utcnow_naive(), summary={}))
        for row in records:
            # Original annual/quarter/holiday facts need a catalog identity too,
            # even when they cannot be promoted to the observation schema.
            if row['entity_type'] in {'scenic','airport','rail_station'}:
                entity = uow.session.scalar(sa.select(Entity).where(Entity.name==row['entity_name'],Entity.type==row['entity_type']))
                if entity is None:
                    details = row['_entity'].get('details',{})
                    details = json.loads(details) if isinstance(details,str) else details
                    region = row['_entity'].get('region_code') or details.get('region_code') or '340000'
                    uow.catalog.create_entity(EntityCreate(str(uuid5(NAMESPACE_URL,'public-entity:'+row['entity_key'])),row['entity_name'],row['entity_type'],region))
            public = {k:v for k,v in row.items() if not k.startswith("_")}
            public["provenance"] = {**public["provenance"], "managed_asset": asset_refs[row["_asset"]["sha256"]]}
            content_hash = digest(public)
            existing = uow.session.execute(sa.select(facts_table).where(facts_table.c.id==row["id"]).with_for_update()).mappings().first()
            if existing and existing["content_sha256"] == content_hash:
                if existing["observation_id"] is None and promotion_reason(row) == "eligible":
                    link, reason = promote(uow, row, asset_refs[row["_asset"]["sha256"]])
                    if link:
                        uow.session.execute(facts_table.update().where(facts_table.c.id==row["id"]).values(observation_id=link))
                    business[reason] += 1
                result["archive"]["skipped"] += 1
                continue
            observation_id = None
            reason = promotion_reason(row)
            if reason == "eligible":
                observation_id, reason = promote(uow, row, asset_refs[row["_asset"]["sha256"]])
            business[reason] += 1
            history = list(existing["previous_versions"]) if existing else []
            if existing:
                history.append({"content_sha256":existing["content_sha256"], "raw_record":existing["raw_record"], "provenance":existing["provenance"], "value":str(existing["value"]), "quality_status":existing["quality_status"]})
            values = public | {"previous_versions":history,"content_sha256":content_hash,
                "observation_id":observation_id,"import_id":import_id,"imported_at":utcnow_naive()}
            if existing:
                uow.session.execute(facts_table.update().where(facts_table.c.id==row["id"]).values(**values))
                result["archive"]["updated"] += 1
            else:
                uow.session.execute(facts_table.insert().values(**values))
                result["archive"]["inserted"] += 1
        result["business"] = dict(business)
        result["import_id"] = import_id
        uow.session.execute(imports_table.update().where(imports_table.c.id==import_id).values(summary=result))
    from ..geography import enrich_coordinates
    result['coordinates'] = enrich_coordinates(store, allow_network=store.engine.dialect.name != 'sqlite')
    return result
