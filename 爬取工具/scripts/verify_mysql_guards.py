"""Synthetic acceptance ONLY in a new independent anhui_crawler_*_test schema."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
import json
import os
from pathlib import Path
from threading import Event, local

from sqlalchemy import create_engine, event, inspect

from anhui_crawler.common import CrawlerError, sha256, write_json
from anhui_crawler.storage import Store, validate_url


def verify(url, data_root):
    parsed = validate_url(url)
    if parsed.drivername != "mysql+pymysql" or not parsed.database.endswith("_test"):
        raise CrawlerError("Only independent empty MySQL _test schema allowed")
    engine = create_engine(url)
    try:
        if inspect(engine).get_table_names() or inspect(engine).get_view_names():
            raise CrawlerError("Exclusive schema must be empty; nothing is dropped")
    finally:
        engine.dispose()
    store = Store(url, data_root)
    source = {"id": "synthetic", "url": "https://example.invalid", "publisher": "SYNTHETIC_ACCEPTANCE_ONLY"}
    entities = [{"id": "synthetic:entity", "name": "Synthetic airport", "type": "airport", "source_ids": ["synthetic"]}]
    store.initialize()
    store.seed({"sources": [source], "entities": entities})
    run = store.start_run("synthetic_mysql_guards")
    checks = []

    def original(raw, published):
        return store.register_asset({"source_id": "synthetic", "url": "https://example.invalid/source.pdf", "sha256": sha256(raw),
            "byte_size": len(raw), "media_type": "application/pdf", "retrieved_at": "2026-10-15T00:00:00+08:00",
            "published_at": published}, raw)[0]

    def record(value):
        return {"entity_id": "synthetic:entity", "metric": "airport_passenger_throughput", "scope": "airport_all_passengers",
            "grain": "month", "period_start": date(2026, 8, 1), "period_end": date(2026, 8, 31),
            "value": Decimal(value), "unit": "人次", "quality": "validated", "qualifier": None,
            "original_value": str(value), "locator": {"page": 1, "synthetic": True}, "parser_version": "synthetic-1"}

    try:
        first = original(b"SYNTHETIC one", "2026-09-01T00:00:00+08:00")
        locked, waiting, proceed = Event(), Event(), Event()
        thread_role = local()
        connections = {}

        def before_lock(conn, cursor, statement, parameters, context, executemany):
            if statement.startswith("SELECT ac_sources.") and "FOR UPDATE" in statement and getattr(thread_role, "name", None) == "second":
                connections["second"] = conn.connection.driver_connection.thread_id()
                waiting.set()

        def after_lock(conn, cursor, statement, parameters, context, executemany):
            if statement.startswith("SELECT ac_sources.") and "FOR UPDATE" in statement and getattr(thread_role, "name", None) == "first":
                connections["first"] = conn.connection.driver_connection.thread_id()
                locked.set()
                assert proceed.wait(10), "Race controller failed to release first writer"

        def writer(role):
            thread_role.name = role
            return store.write_facts(run, first, [record(1000)])

        event.listen(store.engine, "before_cursor_execute", before_lock)
        event.listen(store.engine, "after_cursor_execute", after_lock)
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                first_future = pool.submit(writer, "first")
                try:
                    assert locked.wait(10), "First writer did not hold the source lock"
                    second_future = pool.submit(writer, "second")
                    assert waiting.wait(10), "Second writer did not reach the held lock"
                finally:
                    proceed.set()
                results = [first_future.result(timeout=10), second_future.result(timeout=10)]
        finally:
            event.remove(store.engine, "before_cursor_execute", before_lock)
            event.remove(store.engine, "after_cursor_execute", after_lock)
        assert sum(r["inserted"] for r in results) == 1 and sum(r["skipped"] for r in results) == 1
        assert connections["first"] != connections["second"]
        checks.append({"name": "concurrent_same_source_import_is_one_insert_one_skip", "results": results,
                       "different_mysql_connections": connections, "second_waited_after_its_asset_read": True})
        newer = original(b"SYNTHETIC corrected", "2026-09-02T00:00:00+08:00")
        assert store.write_facts(run, newer, [record(2000)])["quarantined"] == 1
        assert store.export_facts()[0]["value"] == "1000.0000"
        checks.append({"name": "changed_source_quarantined_without_silent_overwrite"})
        assert store.write_facts(run, newer, [record(2000)], accept_revisions=True)["revised"] == 1
        assert store.statistics()["ac_revisions"] == 1 and store.export_facts()[0]["value"] == "2000.0000"
        checks.append({"name": "explicit_revision_preserves_previous_record"})
        assert store.write_facts(run, first, [record(1000)], accept_revisions=True)["quarantined"] == 1
        assert store.export_facts()[0]["value"] == "2000.0000"
        checks.append({"name": "older_publication_cannot_replace_newer_active_fact"})
        good = {**record(3000), "period_start": date(2026, 9, 1), "period_end": date(2026, 9, 30)}
        bad = {**good, "entity_id": "unknown"}
        try:
            store.write_facts(run, newer, [good, bad])
        except CrawlerError:
            pass
        else:
            raise AssertionError("Invalid batch accepted")
        assert len(store.export_facts()) == 1
        checks.append({"name": "invalid_batch_rolls_back_prior_valid_row"})
        final = {"status": "succeeded", "scope": "synthetic_isolated_mysql_only", "checks": checks,
                 "tables": sorted(inspect(store.engine).get_table_names()), "team_database_touched": False,
                 "records_are_not_public_statistics": True}
        store.finish_run(run, final)
        return final
    finally:
        store.dispose()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    result = verify(os.environ["CRAWLER_MYSQL_TEST_URL"], args.data_root)
    write_json(args.report, result)
    print(json.dumps({"status": result["status"], "checks": len(result["checks"])}))
