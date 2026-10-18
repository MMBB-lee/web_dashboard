from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
import json
from pathlib import Path
import re
from urllib.parse import urljoin, urlparse

from lxml import html

from .airport import parse_pdf
from .bulletin import parse_bulletin
from .topics import parse_topic
from .annual import parse_pdf as parse_annual_pdf
from .common import CrawlerError, key_for, now, safe_path, sha256, write_json
from .network import Fetcher, allowed_url


def load_registry(path=None):
    if path is None:
        path = Path(__file__).parent / "config/registry.json"
    registry = json.loads(Path(path).read_text(encoding="utf-8"))
    if registry.get("schema_version") not in {"anhui-crawler-registry-1", "anhui-crawler-registry-2"}:
        raise CrawlerError("Unsupported standalone registry version")
    return registry


def html_tree(raw):
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("gb18030")
    return html.fromstring(text)


def monthly_links(raw, url, start, end):
    output = {}
    for anchor in html_tree(raw).xpath("//a[@href]"):
        title = "".join(anchor.itertext()).strip()
        match = re.search(r"(20\d{2})年(\d{1,2})月(?:份)?华东地区机场运输生产情况", title)
        if not match:
            continue
        year, month = map(int, match.groups())
        if not 1 <= month <= 12:
            continue
        period = f"{year}-{month:02}"
        if start <= period <= end:
            target = urljoin(url, anchor.get("href"))
            output[(period, target)] = {"period": period, "url": target, "title": title}
    return list(output.values())


def airport_pdf_links(raw, url):
    tree = html_tree(raw)
    stamp = re.search(r"__\$pubtime\s*=\s*['\"]([^'\"]+)['\"]", raw.decode("utf-8", errors="replace"))
    if not stamp:
        raise CrawlerError("Official article publication timestamp not recognized")
    parsed = datetime.fromisoformat(stamp[1].replace(" ", "T"))
    if parsed.utcoffset() is not None:
        raise CrawlerError("Unexpected article timestamp format")
    published = parsed.isoformat() + "+08:00"
    links = []
    for anchor in tree.xpath("//a[@href]"):
        target = urljoin(url, anchor.get("href"))
        title = "".join(anchor.itertext()).strip()
        ranking_title = "机场" in title or re.fullmatch(r"20\d{2}年\d{1,2}月月报排序\s*\.pdf", title, re.IGNORECASE)
        if urlparse(target).path.lower().endswith(".pdf") and ranking_title and "分省" not in title:
            links.append({"url": target, "title": title, "published_at": published, "page_url": url})
    if len(links) != 1:
        raise CrawlerError("Expected one unambiguous airport-ranking PDF; attachments require review")
    return links


def validate_source(item, sources):
    source = sources.get(item["source_id"])
    if source is None:
        raise CrawlerError("Unknown source in seed manifest")
    allowed_url(item["url"], source["allowed_hosts"])
    if item.get("effective_url"):
        allowed_url(item["effective_url"], source["allowed_hosts"])
    return source


def ingest_seed(store, registry, manifest_path, *, accept_revisions=False, progress=None):
    manifest_path = Path(manifest_path).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "anhui-crawler-seed-1":
        raise CrawlerError("Use the independent crawler's seed manifest format")
    sources = {s["id"]: s for s in registry["sources"]}
    run_id = store.start_run("seed_backfill")
    report = {"run_id": run_id, "started_at": now(), "status": "running", "mode": "independent_crawler",
        "team_database_touched": False, "assets_processed": 0, "assets_failed": 0,
        "records": {"inserted": 0, "skipped": 0, "revised": 0, "quarantined": 0},
        "samples_archived_without_numeric_claim": 0, "items": []}
    try:
        for item in manifest["assets"]:
            result = {"source_id": item["source_id"], "source_url": item["url"], "period": item.get("period")}
            try:
                source = validate_source(item, sources)
                path = safe_path(manifest_path.parent, item["path"])
                if not path.is_file() or path.stat().st_size > 10_000_000:
                    raise CrawlerError("Seed must reference a regular, size-bounded file")
                raw = path.read_bytes()
                if len(raw) > 10_000_000:
                    raise CrawlerError("Seed asset exceeds the parser size limit")
                # Validate seed bytes before using article metadata to enrich provenance.
                if sha256(raw) != item["sha256"] or len(raw) != item["byte_size"]:
                    raise CrawlerError("Seed asset SHA/size mismatch")
                bulletin = (parse_bulletin(raw, source["id"], registry["entities"]) or
                            parse_topic(raw, source["id"], registry["entities"])) if item["media_type"] == "text/html" else None
                if item["media_type"] == "application/pdf":
                    bulletin = parse_annual_pdf(path, source["id"], registry["entities"], item["url"])
                if bulletin:
                    if item.get("published_at") and datetime.fromisoformat(item["published_at"]) != datetime.fromisoformat(bulletin["published_at"]):
                        raise CrawlerError("Seed and official bulletin publication timestamps disagree")
                    item = {**item, "published_at": bulletin["published_at"]}
                asset_id, path = store.register_asset(item, raw)
                result.update(asset_id=asset_id, sha256=item["sha256"])
                if source["adapter"] == "caac_monthly" and item.get("period"):
                    if not raw.startswith(b"%PDF-") or item["media_type"] != "application/pdf":
                        raise CrawlerError("Expected a real PDF, not an error page")
                    records = parse_pdf(path, item["period"], registry["entities"])
                    counts = store.write_facts(run_id, asset_id, records, accept_revisions=accept_revisions)
                    for name, value in counts.items():
                        report["records"][name] += value
                    result.update(status="quarantined" if counts["quarantined"] else "validated", counts=counts)
                elif bulletin:
                    counts = store.write_facts(run_id, asset_id, bulletin["records"], accept_revisions=accept_revisions)
                    for name, value in counts.items():
                        report["records"][name] += value
                    result.update(status="quarantined" if counts["quarantined"] else "validated", counts=counts,
                                  parser_version=bulletin["records"][0]["parser_version"])
                else:
                    # Unreviewed sources and entry pages remain evidence, not numeric facts.
                    report["samples_archived_without_numeric_claim"] += 1
                    result.update(status="archived_for_adapter_review", numeric_rows_claimed=0)
                report["assets_processed"] += 1
            except (CrawlerError, OSError, ValueError, KeyError) as exc:
                report["assets_failed"] += 1
                result.update(status="quarantined", error_type=type(exc).__name__)
                if isinstance(exc, CrawlerError):
                    result["reason"] = str(exc)
                store.issue(run_id, item.get("source_id"), "asset_or_parse_failed", result)
            report["items"].append(result)
            if progress:
                progress(result)
        report["status"] = ("partial" if report["assets_failed"] or report["records"]["quarantined"] else "succeeded")
        if not report["assets_processed"]:
            report["status"] = "failed"
    except Exception:
        report["status"] = "failed"
        raise
    finally:
        report["finished_at"] = now()
        store.finish_run(run_id, report)
    return report


def discover_caac(store, registry, *, start, end, pages=1, max_reports=2, fetcher=None, progress=None):
    for value in (start, end):
        if not re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", value):
            raise CrawlerError("Use YYYY-MM start/end")
    if start > end or not 1 <= pages <= 20 or not 1 <= max_reports <= 50:
        raise CrawlerError("Invalid discovery range or request budget")
    source = next(s for s in registry["sources"] if s["id"] == "caac-east-monthly")
    fetcher = fetcher or Fetcher(source["allowed_hosts"])
    run_id = store.start_run("caac_https_discovery")
    report = {"run_id": run_id, "status": "running", "references": [], "failures": [], "robots": [],
              "bounded_pages": pages, "max_reports": max_reports, "team_database_touched": False}
    assets, references = [], {}

    def obtain(url, media=None):
        response = fetcher.fetch(url)
        actual = "application/pdf" if response.body.startswith(b"%PDF-") else "text/html"
        if media == "application/pdf" and actual != media:
            raise CrawlerError("Official PDF URL returned non-PDF content")
        item = {"source_id": source["id"], "url": url, "effective_url": response.effective_url,
                "sha256": sha256(response.body), "byte_size": len(response.body), "media_type": actual,
                "retrieved_at": response.retrieved_at, "published_at": None}
        asset_id, path = store.register_asset(item, response.body)
        if progress:
            progress({"source_id": source["id"], "url": url, "status": "fetched", "asset_id": asset_id})
        return item, response.body, path

    try:
        for number in range(pages):
            url = source["url"] if number == 0 else urljoin(source["url"], f"index_{number}.html")
            try:
                _, raw, _ = obtain(url)
                for ref in monthly_links(raw, url, start, end):
                    allowed_url(ref["url"], source["allowed_hosts"])
                    references[(ref["period"], ref["url"])] = ref
            except CrawlerError as exc:
                report["failures"].append({"url": url, "reason": str(exc)})
        if not references and not report["failures"]:
            report["failures"].append({"url": source["url"], "reason": "No monthly references found in bounded directory pages; not a zero/no-new-data claim"})
        for ref in sorted(references.values(), key=lambda r: (r["period"], r["url"]), reverse=True)[:max_reports]:
            try:
                _, raw, _ = obtain(ref["url"])
                link = airport_pdf_links(raw, ref["url"])[0]
                item, _, path = obtain(link["url"], "application/pdf")
                item.update(link, period=ref["period"])
                item["path"] = path.relative_to(store.root).as_posix()
                assets.append(item)
                report["references"].append({**ref, "pdf_url": link["url"], "sha256": item["sha256"]})
            except CrawlerError as exc:
                report["failures"].append({"url": ref["url"], "period": ref["period"], "reason": str(exc)})
        report["robots"] = [entry[1] for entry in fetcher.robots.values()]
        report["status"] = "partial" if report["failures"] and assets else "succeeded" if assets else "failed"
        for failure in report["failures"]:
            store.issue(run_id, source["id"], "discovery_or_fetch_failed", failure)
        seed = store.root / "manifests" / f"discovered-{run_id}.json"
        # Paths in a seed are relative to its own root, not cwd or a team checkout.
        for item in assets:
            original = safe_path(store.root, item["path"])
            raw = original.read_bytes()
            target = seed.parent / "inputs" / item["sha256"] / "body.pdf"
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and sha256(target.read_bytes()) != item["sha256"]:
                raise CrawlerError("Immutable discovered input is corrupt")
            if not target.exists():
                target.write_bytes(raw)
            item["path"] = target.relative_to(seed.parent).as_posix()
        write_json(seed, {"schema_version": "anhui-crawler-seed-1", "assets": assets})
        report["seed_manifest"] = seed.relative_to(store.root).as_posix()
        return report, seed
    except Exception:
        report["status"] = "failed"
        raise
    finally:
        store.finish_run(run_id, report)


def month_range(start, end):
    year, month = map(int, start.split("-"))
    while f"{year}-{month:02}" <= end:
        yield f"{year}-{month:02}"
        month += 1
        if month == 13:
            year, month = year + 1, 1


def coverage(store, registry, start, end):
    if not all(re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", p) for p in (start, end)) or start > end:
        raise CrawlerError("Invalid coverage window")
    grouped = defaultdict(set)
    other = defaultdict(list)
    asset_counts = store.source_asset_counts()
    for record in store.export_facts():
        if record["metric"] == "airport_passenger_throughput":
            grouped[record["entity_id"]].add(record["period_start"][:7])
        elif record["period_start"][:7] <= end and record["period_end"][:7] >= start:
            other[record["entity_id"]].append({k: record[k] for k in
                ("metric", "scope", "grain", "period_start", "period_end", "unit", "source_id")})
    airports = []
    for entity in registry["entities"]:
        if entity["type"] != "airport":
            continue
        present = sorted(m for m in grouped[entity["id"]] if start <= m <= end)
        first = present[0] if present else None
        before, missing = [], []
        longest = current = 0
        for month in month_range(start, end):
            if first and month < first:
                before.append({"period": month, "reason": "before_first_observed_public_record_not_assumed_zero_or_closed"})
            elif month not in present:
                missing.append({"period": month, "reason": "no_validated_monthly_original_in_current_collection"})
            current = current + 1 if month in present else 0
            longest = max(current, longest)
        airports.append({"entity_id": entity["id"], "name": entity["name"], "months": len(present),
            "first_observed_in_window": first, "latest_observed": present[-1] if present else None,
            "longest_contiguous_months": longest, "missing": missing, "before_first_observed": before,
            "actual_opening_date_verified": False})
    return {"window": {"start": start, "end": end}, "airports": airports,
            "other_entities": [{"entity_id": e["id"], "type": e["type"],
                "originals_archived_from_candidate_sources": sum(asset_counts.get(sid, 0) for sid in e["source_ids"]),
                "validated_records_in_window": len(other[e["id"]]), "observed_metrics_and_periods": other[e["id"]],
                "status": "partial_native_grain_facts_not_complete" if other[e["id"]] else
                    "source_samples_or_adapter_pending_not_zero"} for e in registry["entities"] if e["type"] != "airport"],
            "completeness_claim": "validated_available_public_records_only_not_all_province_data"}
