"""Verify the reviewed nine-city batch against portable originals and an own DB.

Use CRAWLER_DATABASE_URL only for a separately created anhui_crawler* schema.
No team imports, schema changes, source edits, or original overwrites.
"""
import argparse
from decimal import Decimal
import json
import os
from pathlib import Path
import sys

from lxml import html
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from anhui_crawler import __version__
from anhui_crawler.bulletin import compact
from anhui_crawler.common import now, sha256, write_json
from anhui_crawler.models import Asset
from anhui_crawler.pipeline import coverage, ingest_seed, load_registry
from anhui_crawler.storage import Store

# Independently checked against the archived bulletin paragraphs; not generated
# from parser results. Keys keep city rail totals distinct from individual stations.
EXPECTED = {
    ("city:anqing", "passenger_transport_volume", "municipal_road_transport"): "10110000",
    ("city:anqing", "star_rated_hotels", "municipal_year_end_inventory"): "19",
    ("city:anqing", "a_rated_scenic_sites", "municipal_year_end_inventory"): "73",
    ("city:chizhou", "a_rated_scenic_sites", "municipal_year_end_inventory"): "44",
    ("city:huaibei", "passenger_transport_volume", "municipal_road_transport"): "1810000",
    ("city:huainan", "passenger_transport_volume", "municipal_road_transport"): "1590000",
    ("city:huainan", "star_rated_hotels", "municipal_year_end_inventory"): "8",
    ("city:luan", "passenger_transport_volume", "municipal_road_transport"): "8220000",
    ("city:luan", "passenger_transport_volume", "municipal_water_transport"): "263000",
    ("city:suzhou", "passenger_transport_volume", "municipal_road_transport"): "3413000",
    ("city:tongling", "passenger_transport_volume", "municipal_road_transport"): "1120000",
    ("city:tongling", "rail_passenger_departures", "municipal_rail_not_single_station"): "3130000",
    ("city:wuhu", "passenger_transport_volume", "municipal_road_transport"): "6420000",
    ("city:wuhu", "rail_passenger_departures", "municipal_rail_not_single_station"): "17719500",
    ("city:wuhu", "star_rated_hotels", "municipal_year_end_inventory"): "21",
    ("city:wuhu", "a_rated_scenic_sites", "municipal_year_end_inventory"): "39",
    ("city:xuancheng", "passenger_transport_volume", "municipal_road_and_water_excluding_rail"): "15650000",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("Use a new/empty verification directory")
    args.output.mkdir(parents=True, exist_ok=True)
    registry = load_registry()
    store = Store(os.environ.get("CRAWLER_DATABASE_URL") or "sqlite+pysqlite:///" + str(args.data_root.resolve() / "crawler.sqlite3"), args.data_root)
    try:
        store.initialize()
        store.seed(registry)
        before = store.export_facts()
        before_airports = {r["id"]: r for r in before if r["metric"] == "airport_passenger_throughput"}
        assert len(before_airports) in {0, 260}
        for phase in ("first", "repeat"):
            result = ingest_seed(store, registry, args.manifest)
            write_json(args.output / (phase + ".json"), result)
            assert result["status"] == "succeeded" and result["assets_processed"] == 76
            assert result["assets_failed"] == 0 and result["records"]["quarantined"] == 0
            assert result["records"]["revised"] == 0
            assert result["records"]["inserted"] == (277 - len(before) if phase == "first" else 0)
            assert result["records"]["skipped"] == (len(before) if phase == "first" else 277)
            assert result["samples_archived_without_numeric_claim"] == 26
        facts = store.export_facts()
        cities = [r for r in facts if r["entity_id"].startswith("city:")]
        airports = [r for r in facts if r["metric"] == "airport_passenger_throughput"]
        assert len(facts) == 277 and len(cities) == 17 and len(airports) == 260
        assert {(r["entity_id"], r["metric"], r["scope"]): Decimal(r["value"]) for r in cities} == {
            k: Decimal(v) for k, v in EXPECTED.items()}
        assert all(next(r for r in airports if r["id"] == key) == value for key, value in before_airports.items())
        assert all(r["period_end"] == "2025-12-31" and r["grain"] in {"day", "year"} for r in cities)
        with Session(store.engine) as session:
            assets = list(session.scalars(select(Asset)))
            asset_map = {a.id: a for a in assets}
            for asset in assets:
                raw = (store.root / asset.relative_path).read_bytes()
                assert sha256(raw) == asset.sha256 and len(raw) == asset.byte_size
            for record in cities:
                asset = asset_map[record["asset_id"]]
                tree = html.fromstring((store.root / asset.relative_path).read_bytes().decode("utf-8"))
                original = tree.xpath(record["locator"]["xpath"])
                assert len(original) == 1
                assert compact(original[0].text_content()) == compact(record["locator"]["paragraph"])
                assert record["locator"]["matched_text"] in compact(original[0].text_content())
                assert record["source_published_at"] and record["source_published_at"].startswith("2026-")
        assert len(inspect(store.engine).get_table_names()) == 8
        version = None
        if store.engine.dialect.name == "mysql":
            with store.engine.connect() as conn:
                version = conn.scalar(text("SELECT VERSION()"))
        write_json(args.output / "city-facts.json", {"records": cities})
        write_json(args.output / "coverage.json", coverage(store, registry, "2023-01", "2026-08"))
        report = {"status": "passed", "verified_at": now(), "crawler_version": __version__,
            "python": sys.version.split()[0], "database_dialect": store.engine.dialect.name,
            "mysql_version": version, "schema_version": "1", "counts": store.statistics(),
            "previous_airport_records_unchanged": len(before_airports), "monthly_airport_records": 260,
            "new_city_records": 17, "city_entities": 9, "annual_flows": sum(r["grain"] == "year" for r in cities),
            "year_end_inventory": sum(r["grain"] == "day" for r in cities),
            "all_managed_originals_sha_verified": len(assets), "city_original_xpath_checks": len(cities),
            "repeat_inserted": 0, "repeat_skipped": 277, "team_database_touched": False,
            "network_requests_this_verification": 0, "complete_province_claim": False}
        write_json(args.output / "summary.json", report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        store.dispose()


if __name__ == "__main__":
    main()
