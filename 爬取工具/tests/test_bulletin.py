from datetime import date

import pytest

from anhui_crawler.bulletin import parse_bulletin
from anhui_crawler.common import CrawlerError, sha256, write_json
from anhui_crawler.pipeline import coverage, ingest_seed, load_registry
from anhui_crawler.storage import Store, validate_fact


def page(paragraphs=None, *, title="芜湖市2025年国民经济和社会发展统计公报", published="2026-06-29 08:38", extra=""):
    paragraphs = paragraphs or ["全年公路客运量642万人，比上年增长5.5%；铁路旅客发送量1771.95万人，增长3.6%。",
        "年末全市有旅行社146家；星级饭店21家，其中五星级1家；A级及以上旅游景点（区）39处，其中4A级景区11处。"]
    return ('<html><head><meta name="ArticleTitle" content="' + title + '"><meta name="PubDate" content="' + published
            + '"></head><body>' + extra + '<div class="wzcon j-fontContent clearfix"><p>2025年，全市经济稳中有进。</p>'
            + ''.join('<p>' + p + '</p>' for p in paragraphs) + '</div></body></html>').encode()


def parse(raw):
    return parse_bulletin(raw, "city-wuhu", load_registry()["entities"])


def test_annual_flows_and_year_end_inventory_are_distinct():
    result = parse(page())
    records = result["records"]
    assert result["published_at"] == "2026-06-29T08:38:00+08:00"
    assert [r["value"] for r in records] == [6420000, 17719500, 21, 39]
    for r in records:
        validate_fact(r)
        assert r["period_end"] == date(2025, 12, 31)
        assert r["locator"]["xpath"] and r["locator"]["paragraph"]
    assert records[0]["grain"] == "year" and records[0]["period_start"] == date(2025, 1, 1)
    assert records[1]["scope"] == "municipal_rail_not_single_station"
    assert records[1]["original_value"] == "1771.95万人"
    assert records[2]["grain"] == "day" and records[2]["period_start"] == date(2025, 12, 31)
    assert records[2]["unit"] == "家"


def test_navigation_scripts_and_tables_cannot_supply_or_duplicate_numeric_facts():
    poison = '<nav><p>全年公路客运量999万人，铁路旅客发送量999万人。</p></nav>'
    poison += '<script>全年公路客运量999万人</script>'
    raw = page(extra=poison).replace(b'</div>', ('<table><tr><td><p>全年公路客运量999万人。</p></td></tr></table>'
        '<script>全年公路客运量999万人。</script><nav><p>全年公路客运量999万人。</p></nav></div>').encode())
    assert parse(raw)["records"][0]["value"] == 6420000


@pytest.mark.parametrize("old,new", [
    ("642万人", "约642万人"), ("642万人", "642余万人"), ("642万人", "-642万人"),
    ("642万人", "6,42万人"), ("642万人", "642亿人"), ("642万人", "0.000001万人"),
    ("全年公路", "预计全年公路"), ("全年公路", "计划全年公路"),
    ("全年公路", "前三季度公路"), ("年末全市", "前三季度全市"),
    ("芜湖市2025", "合肥市2025"), ("j-fontContent", "changed-content"),
    ("芜湖市2025", "芜湖市2024"),
    ("2026-06-29", "2025-06-29"), ("PubDate", "UnknownDate"),
])
def test_changes_requiring_review_fail_closed(old, new):
    with pytest.raises((CrawlerError, ValueError)):
        parse(page().decode().replace(old, new).encode())


def test_duplicate_metric_and_duplicate_container_rejected():
    for raw in [page().replace('</div>'.encode(), '<p>全年公路客运量643万人。</p></div>'.encode()),
                page(extra='<div class="j-fontContent"></div>')]:
        with pytest.raises(CrawlerError):
            parse(raw)


def test_entry_page_without_article_metadata_stays_archive_only():
    assert parse('<html><title>2025年公报目录</title><p>全年公路客运量999万人。</p></html>'.encode()) is None


def test_city_source_mapping_required():
    with pytest.raises(CrawlerError, match="mapping"):
        parse_bulletin(page(), "city-wuhu", [])


@pytest.mark.parametrize("change", [{"grain": "month", "period_end": date(2025, 1, 31)},
    {"entity_id": "rail:wuhu"}, {"scope": "single_station"}, {"unit": "人公里"}, {"value": "1.1"}])
def test_storage_enforces_city_metric_definition(change):
    with pytest.raises(CrawlerError):
        validate_fact({**parse(page())["records"][1], **change})


def test_aggregate_requires_scope_footnote():
    raw = page(["全年旅客运输量1565万人次，增长8.8倍。"], title="宣城市2025年国民经济和社会发展统计公报").replace(
        b'class="wzcon j-fontContent clearfix"', b'id="zoom"')
    with pytest.raises(CrawlerError, match="footnote"):
        parse_bulletin(raw, "city-xuancheng", load_registry()["entities"])
    raw = raw.replace(b'</div>', '<p>[3]旅客运输周转量为公路和水运合计，不含铁路。</p></div>'.encode())
    record = parse_bulletin(raw, "city-xuancheng", load_registry()["entities"])["records"][0]
    assert record["scope"] == "municipal_road_and_water_excluding_rail" and record["value"] == 15650000


def test_bulletin_storage_and_coverage_not_monthly_or_single_station(tmp_path):
    registry = load_registry()
    store = Store("sqlite+pysqlite:///" + str(tmp_path / "test.sqlite3"), tmp_path / "raw")
    try:
        store.initialize()
        store.seed(registry)
        raw = page()
        parsed = parse(raw)
        asset = store.register_asset({"source_id": "city-wuhu", "url": "https://www.wuhu.gov.cn/synthetic-test.html",
            "sha256": sha256(raw), "byte_size": len(raw), "media_type": "text/html",
            "retrieved_at": "2026-10-15T00:00:00+08:00", "published_at": parsed["published_at"]}, raw)[0]
        run = store.start_run("test")
        assert store.write_facts(run, asset, parsed["records"])["inserted"] == 4
        assert store.write_facts(run, asset, parsed["records"])["skipped"] == 4
        report = coverage(store, registry, "2025-01", "2025-12")
        city = next(e for e in report["other_entities"] if e["entity_id"] == "city:wuhu")
        assert city["validated_records_in_window"] == 4
        station = next(e for e in report["other_entities"] if e["type"] == "rail_station")
        assert station["validated_records_in_window"] == 0
        assert all(r["grain"] != "month" for r in city["observed_metrics_and_periods"])
        assert next(e for e in coverage(store, registry, "2026-01", "2026-08")["other_entities"]
                    if e["entity_id"] == "city:wuhu")["validated_records_in_window"] == 0
    finally:
        store.dispose()


@pytest.mark.parametrize("fault", [None, "sha", "published", "missing_metric"])
def test_pipeline_enrichment_and_no_partial_city_write(tmp_path, fault):
    registry = load_registry()
    raw = page()
    if fault == "missing_metric":
        raw = raw.decode().replace("铁路旅客发送量", "铁路货物发送量").encode()
    (tmp_path / "original.html").write_bytes(raw)
    item = {"source_id": "city-wuhu", "url": "https://www.wuhu.gov.cn/synthetic-test.html",
            "path": "original.html", "sha256": "0" * 64 if fault == "sha" else sha256(raw),
            "byte_size": len(raw), "media_type": "text/html", "retrieved_at": "2026-10-15T00:00:00+08:00",
            "published_at": "2026-06-30T08:38:00+08:00" if fault == "published" else None}
    manifest = tmp_path / "seed.json"
    write_json(manifest, {"schema_version": "anhui-crawler-seed-1", "assets": [item]})
    store = Store("sqlite+pysqlite:///" + str(tmp_path / "pipeline.sqlite3"), tmp_path / "raw")
    try:
        store.initialize()
        store.seed(registry)
        report = ingest_seed(store, registry, manifest)
        if fault:
            assert report["assets_failed"] == 1 and report["status"] == "failed"
            assert store.export_facts() == []
        else:
            assert report["records"]["inserted"] == 4
            assert all(r["source_published_at"] == "2026-06-29T08:38:00+08:00" for r in store.export_facts())
            assert ingest_seed(store, registry, manifest)["records"]["skipped"] == 4
    finally:
        store.dispose()
