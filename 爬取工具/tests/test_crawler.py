from datetime import date
from decimal import Decimal
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, text

from anhui_crawler.airport import parse_pages
from anhui_crawler.common import CrawlerError, safe_path, sha256
from anhui_crawler.network import allowed_url
from anhui_crawler.pipeline import airport_pdf_links, coverage, load_registry, monthly_links
from anhui_crawler.storage import Store, validate_fact, validate_url


ENTITIES = [{"id": "airport:HFE", "type": "airport", "name": "合肥新桥", "aliases": ["合肥/新桥"], "source_ids": ["test"]}]
HEADER = "2026年8月华东机场吞吐量排名\n旅客吞吐量（人） 货邮吞吐量（吨） 起降架次（次）\n机场 本期完成 同比% 本年累计\n"
REGISTRY = {"sources": [{"id": "test", "url": "https://example.invalid/", "publisher": "synthetic test",
                         "allowed_hosts": ["example.invalid"], "adapter": "caac_monthly"}], "entities": ENTITIES}


def sample_record(value=1000):
    item = parse_pages([HEADER + f"合肥/新桥 11 {value} -1.0 888888"], "2026-08", ENTITIES)[0]
    return item


@pytest.fixture
def store(tmp_path):
    value = Store("sqlite+pysqlite:///" + str(tmp_path / "test.sqlite3"), tmp_path / "raw")
    value.initialize()
    value.seed(REGISTRY)
    yield value
    value.dispose()


def asset(store, body=b"synthetic original one", published="2026-09-01T00:00:00+08:00"):
    item = {"source_id": "test", "url": "https://example.invalid/source.pdf", "sha256": sha256(body),
            "byte_size": len(body), "media_type": "application/pdf", "retrieved_at": "2026-10-15T00:00:00+08:00",
            "published_at": published}
    return store.register_asset(item, body)[0]


def test_monthly_current_column_not_cumulative_or_rank():
    item = sample_record()
    assert item["value"] == Decimal(1000)
    assert item["value"] != 888888
    assert item["period_end"] == date(2026, 8, 31)
    assert item["locator"]["page"] == 1 and "本期完成" in item["locator"]["column"]


def test_comma_integer():
    item = parse_pages([HEADER + "合肥/新桥 11 1,234,567 -1.0 8888888"], "2026-08", ENTITIES)[0]
    assert item["value"] == 1234567


@pytest.mark.parametrize("replacement", ["-10", "1.5", "10余", "1,23", "NaN", "Infinity"])
def test_unsupported_airport_number_is_not_zero_or_verified(replacement):
    with pytest.raises(CrawlerError):
        parse_pages([HEADER + f"合肥/新桥 11 {replacement} -1.0 888888"], "2026-08", ENTITIES)


@pytest.mark.parametrize("header", [HEADER.replace("8月", "7月"), HEADER.replace("2026年", "2025年"),
                                    HEADER.replace("(人)", "(万人)").replace("（人）", "（万人）"),
                                    HEADER.replace("本期完成 同比% 本年累计", "本年累计 同比% 本期完成"),
                                    HEADER.replace("本期完成", "本年完成")])
def test_month_year_unit_and_header_changes_fail_closed(header):
    with pytest.raises(CrawlerError):
        parse_pages([header + "合肥/新桥 11 1000 -1.0 888888"], "2026-08", ENTITIES)


def test_duplicate_rows_are_quarantined():
    with pytest.raises(CrawlerError, match="Duplicate"):
        parse_pages([HEADER + "合肥/新桥 11 1000 0.0\n合肥/新桥 11 1000 0.0"], "2026-08", ENTITIES)


def test_absent_new_airport_is_not_synthesized_as_zero():
    new = {"id": "airport:bozhou", "type": "airport", "aliases": ["亳州"]}
    assert len(parse_pages([HEADER + "合肥/新桥 11 1000 0.0"], "2026-08", ENTITIES + [new])) == 1


@pytest.mark.parametrize("url", ["mysql+pymysql://u:p@localhost/team_db", "mysql+pymysql://localhost/anhui_crawler_fake/team",
                                 "postgresql://localhost/anhui_crawler", "mysql://localhost/anhui_crawler"])
def test_business_database_names_and_other_drivers_refused(url):
    with pytest.raises(CrawlerError):
        validate_url(url)


def test_business_tables_not_modified(tmp_path):
    url = "sqlite+pysqlite:///" + str(tmp_path / "foreign.sqlite3")
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE jobs (id INTEGER PRIMARY KEY)"))
        conn.execute(text("INSERT INTO jobs VALUES (7)"))
    value = Store(url, tmp_path / "assets")
    with pytest.raises(CrawlerError, match="non-crawler"):
        value.initialize()
    with engine.connect() as conn:
        assert conn.execute(text("SELECT id FROM jobs")).scalar_one() == 7
    value.dispose()
    engine.dispose()


def test_idempotent_import_and_default_conflict_quarantine(store):
    run = store.start_run("unit")
    original = asset(store)
    assert store.write_facts(run, original, [sample_record()])["inserted"] == 1
    assert store.write_facts(run, original, [sample_record()])["skipped"] == 1
    revised = asset(store, b"updated original", "2026-09-02T00:00:00+08:00")
    assert store.write_facts(run, revised, [sample_record(2000)])["quarantined"] == 1
    assert store.export_facts()[0]["value"] == "1000.0000"
    assert store.statistics()["ac_revisions"] == 0


def test_explicit_revision_retains_history_and_stale_replay_refused(store):
    run = store.start_run("unit")
    original = asset(store)
    store.write_facts(run, original, [sample_record()])
    revised = asset(store, b"updated original", "2026-09-02T00:00:00+08:00")
    assert store.write_facts(run, revised, [sample_record(2000)], accept_revisions=True)["revised"] == 1
    assert store.statistics()["ac_revisions"] == 1
    assert store.export_facts()[0]["value"] == "2000.0000"
    assert store.write_facts(run, original, [sample_record()], accept_revisions=True)["quarantined"] == 1
    assert store.export_facts()[0]["value"] == "2000.0000"


def test_failed_fact_batch_rolls_back_prior_record(store):
    run = store.start_run("unit")
    original = asset(store)
    invalid = {**sample_record(2000), "entity_id": "unknown"}
    with pytest.raises(CrawlerError, match="mapping"):
        store.write_facts(run, original, [sample_record(), invalid])
    assert store.export_facts() == []


def test_existing_corrupt_immutable_file_not_overwritten(store):
    relative = store.put_bytes(b"real bytes", "text/html")
    path = safe_path(store.root, relative)
    path.write_bytes(b"changed")
    with pytest.raises(CrawlerError, match="corrupt"):
        store.put_bytes(b"real bytes", "text/html")
    assert path.read_bytes() == b"changed"


@pytest.mark.parametrize("relative", ["../out", "/out", "a/../out", "a//out", "a\\out", "C:/out", "a/\0out"])
def test_file_path_escape_refused(tmp_path, relative):
    with pytest.raises(CrawlerError):
        safe_path(tmp_path, relative)


@pytest.mark.parametrize("url", ["http://example.invalid/", "https://elsewhere.invalid/file", "https://u:secret@example.invalid/",
                                 "https://example.invalid:8443/", "https://example.invalid/file#fragment"])
def test_redirect_host_scheme_and_credentials_refused(url):
    with pytest.raises(CrawlerError):
        allowed_url(url, {"example.invalid"})


def test_qualified_numbers_not_exported_as_validated():
    record = {**sample_record(), "qualifier": "约"}
    with pytest.raises(CrawlerError, match="Approximate"):
        validate_fact(record)


def test_annual_value_cannot_be_monthly_or_approximate(store):
    record = {**sample_record(), "period_end": date(2026, 12, 31)}
    with pytest.raises(CrawlerError, match="complete calendar month"):
        validate_fact(record)


def test_discovery_title_not_hardcoded_to_2026_and_annual_not_monthly():
    raw = ('<html><a href="a.html">2027年1月份华东地区机场运输生产情况</a>'
           '<a href="annual.html">2026年华东地区运输生产情况</a></html>').encode()
    result = monthly_links(raw, "https://example.invalid/stats/", "2023-01", "2027-02")
    assert [r["period"] for r in result] == ["2027-01"]


def test_pdf_selection_excludes_airline_and_provincial_summary():
    raw = ("<html><script>__$pubtime='2026-09-24 14:33:00';</script>"
           '<a href="a.pdf">2026年8月华东机场吞吐量排名.pdf</a>'
           '<a href="b.pdf">航空公司生产情况.pdf</a><a href="c.pdf">机场分省表.pdf</a></html>').encode()
    result = airport_pdf_links(raw, "https://example.invalid/article.html")
    assert len(result) == 1 and result[0]["url"] == "https://example.invalid/a.pdf"
    assert result[0]["published_at"] == "2026-09-24T14:33:00+08:00"


def test_coverage_preserves_missing_december_and_does_not_fill_zero(store):
    run = store.start_run("unit")
    store.write_facts(run, asset(store), [sample_record()])
    report = coverage(store, {"entities": ENTITIES}, "2026-01", "2026-12")
    item = report["airports"][0]
    assert item["months"] == 1 and item["longest_contiguous_months"] == 1
    assert item["missing"][-1]["period"] == "2026-12"
    assert len(item["before_first_observed"]) == 7


def test_packaged_registry_has_44_entities_and_audited_sources():
    registry = load_registry()
    assert len(registry["entities"]) == 44 and len(registry["sources"]) == 34
    assert any(s['id'] == 'rail_station-huangshan-north-ahnews' for s in registry['sources'])
    assert sum(e["type"] == "airport" for e in registry["entities"]) == 8


def test_cli_ignores_team_database_environment_and_no_team_packages(tmp_path):
    env = {**os.environ, "DATABASE_URL": "mysql+pymysql://u:DO_NOT_DISCLOSE@invalid/team_db",
           "TEST_DATABASE_URL": "mysql+pymysql://u:DO_NOT_DISCLOSE@invalid/team_test"}
    env.pop("CRAWLER_DATABASE_URL", None)
    result = subprocess.run([sys.executable, "-I", "-m", "anhui_crawler", "--data-root", str(tmp_path / "independent"), "init"],
                            env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["database_dialect"] == "sqlite"
    assert "DO_NOT_DISCLOSE" not in result.stdout + result.stderr
