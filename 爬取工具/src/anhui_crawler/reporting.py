"""Evidence checks and explicit completeness gaps, never inferred zero coverage."""
from datetime import date, timedelta
import json
from pathlib import Path

from lxml import html
from sqlalchemy import select
from sqlalchemy.orm import Session

from .common import CrawlerError, now, safe_path, sha256
from .metrics import TARGETS, dictionary
from .models import Asset, FetchState, Run, SourceState
from .pipeline import coverage
from .storage import validate_fact
from .updating import instant, state

AUDIT_STATUSES = {"collected", "not_published", "not_found_in_recorded_search", "access_restricted", "no_target_metric", "scope_unconfirmed", "download_failed", "parse_failed", "pending"}
CLOSABLE = {"collected", "not_published", "not_found_in_recorded_search", "access_restricted", "no_target_metric"}


def load_audit(store, registry, path=None):
    path = Path(path) if path else store.root / "audit.json"
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "anhui-crawler-audit-1":
        raise CrawlerError("Unsupported audit format")
    entities = {e["id"]: e for e in registry["entities"]}
    entries = {}
    for entry in payload.get("entries", []):
        key = (entry["entity_id"], entry["target_metric"])
        if key in entries or key[0] not in entities or key[1] not in entities[key[0]]["target_metrics"] or entry["status"] not in AUDIT_STATUSES:
            raise CrawlerError("Invalid or duplicate audit target")
        date.fromisoformat(entry["start"]), date.fromisoformat(entry["end"])
        for evidence in entry.get("evidence", []):
            if not evidence.get("url", "").startswith("https://") or not evidence.get("outcome"):
                raise CrawlerError("Audit evidence requires public HTTPS URL and outcome")
            instant(evidence["checked_at"])
        entries[key] = entry
    return entries


def matrix(store, registry, start, end):
    rows, audits = [], load_audit(store, registry)
    facts = store.export_facts(start=date.fromisoformat(start), end=date.fromisoformat(end))
    all_facts = store.export_facts(include_candidates=True, start=date.fromisoformat(start), end=date.fromisoformat(end))
    for entity in registry["entities"]:
        for target in entity["target_metrics"]:
            names = TARGETS[target]
            matching = [f for f in facts if f["entity_id"] == entity["id"] and f["metric"] in names]
            candidates = [f for f in all_facts if f["entity_id"] == entity["id"] and f["metric"] in names and f["quality"] == "candidate"]
            audit = audits.get((entity["id"], target))
            applicable = bool(audit and audit["start"] <= start and audit["end"] >= end)
            conclusion = audit["status"] if applicable else "pending"
            evidence = audit.get("evidence", []) if applicable else []
            source_states = [state(store, sid) for sid in entity["source_ids"]]
            # A factual row is not proof that all historical sources/periods were checked.
            closed = conclusion in CLOSABLE and bool(evidence)
            if conclusion in CLOSABLE - {"collected"}:
                dates = {instant(e["checked_at"]).date() for e in evidence}
                routes = {e.get("route") for e in evidence if e.get("route")}
                closed = closed and len(dates) >= 2 and len(routes) >= 2 and bool(audit.get("next_check"))
            if conclusion == "collected":
                closed = closed and bool(matching) and bool(audit.get("discovered_documents_exhausted"))
            def blocks(source):
                details = source["details"]
                if conclusion == "access_restricted" and source["status"] != "running":
                    failures = details.get("failures", [])
                    failed_urls = {f["url"] for f in failures}
                    external = ("robots.txt", "Unexpected robots response", "HTTPS connection/verification failed",
                                "HTTP 401", "HTTP 403", "HTTP 405", "HTTP 412", "HTTP 429")
                    # An evidenced external restriction may close availability
                    # audit, but never hides a parser defect or other queued work.
                    if failures and all(f.get("reason", "").startswith(external) for f in failures) and all(
                            q["url"] in failed_urls for q in details.get("pending", [])) and not details.get("review_required"):
                        return False
                return bool(details.get("pending") or details.get("review_required") or source["status"] in {"running", "failed", "partial"})
            if any(blocks(s) for s in source_states):
                closed = False
            rows.append({"entity_id": entity["id"], "name": entity["name"], "type": entity["type"],
                "target_metric": target, "canonical_metrics": names, "standard_records": len(matching),
                "candidate_records": len(candidates), "observed_periods": sorted({(f["period_start"], f["period_end"], f["grain"], f["scope"]) for f in matching}),
                "audit_status": conclusion, "audit_closed": closed, "evidence": evidence,
                "next_check": audit.get("next_check") if applicable else None,
                "source_statuses": [{k: s[k] for k in ("source_id", "status", "next_due")} for s in source_states]})
    return {"schema_version": "anhui-crawler-matrix-1", "generated_at": now(), "window": {"start": start, "end": end},
            "entities": len(registry["entities"]), "targets": len(rows), "closed_targets": sum(r["audit_closed"] for r in rows),
            "entities_with_standard_facts": len({r["entity_id"] for r in rows if r["standard_records"]}),
            "all_targets_closed": bool(rows) and all(r["audit_closed"] for r in rows), "rows": rows}


def verify(store, registry, *, start="2023-01-01", end=None):
    end = end or instant().astimezone(__import__("datetime").timezone(timedelta(hours=8))).date().isoformat()
    errors, verified, pdf_cache = [], 0, {}
    with Session(store.engine) as session:
        assets = list(session.scalars(select(Asset)))
        asset_map = {a.id: a for a in assets}
        requests = list(session.scalars(select(FetchState)))
        running = list(session.scalars(select(Run).where(Run.status == "running")))
        for asset in assets:
            try:
                raw = safe_path(store.root, asset.relative_path).read_bytes()
                if sha256(raw) != asset.sha256 or len(raw) != asset.byte_size:
                    raise CrawlerError("Original SHA/size mismatch")
                verified += 1
            except (CrawlerError, OSError):
                errors.append({"asset_id": asset.id, "code": "original_integrity_failed"})
        for request in requests:
            if request.asset_id and request.asset_id not in asset_map:
                errors.append({"request_id": request.id, "code": "missing_cached_asset"})
    checked_facts = 0
    for fact in store.export_facts(include_candidates=True):
        try:
            record = {**fact, "period_start": date.fromisoformat(fact["period_start"]), "period_end": date.fromisoformat(fact["period_end"])}
            validate_fact(record)
            asset = asset_map[fact["asset_id"]]
            raw = safe_path(store.root, asset.relative_path).read_bytes()
            locator = fact["locator"]
            if asset.media_type == "text/html":
                try:
                    tree = html.fromstring(raw.decode("utf-8"))
                except UnicodeDecodeError:
                    tree = html.fromstring(raw.decode("gb18030"))
                selected = tree.xpath(locator["xpath"])
                from .bulletin import compact
                if len(selected) != 1 or compact(selected[0].text_content()) != compact(locator["paragraph"]) or locator["matched_text"] not in compact(selected[0].text_content()):
                    raise CrawlerError("HTML locator mismatch")
            elif asset.media_type == "application/pdf":
                from .airport import parse_pdf
                if fact["metric"] == "airport_passenger_throughput":
                    # Reparse once per asset/period, compared below through cached rows.
                    cache = pdf_cache.setdefault((asset.id, fact["period_start"]), None)
                    if cache is None:
                        cache = parse_pdf(safe_path(store.root, asset.relative_path), fact["period_start"][:7], registry["entities"])
                        pdf_cache[(asset.id, fact["period_start"])] = cache
                    expected = next(r for r in cache if r["entity_id"] == fact["entity_id"])
                    from decimal import Decimal
                    if expected["value"] != Decimal(fact["value"]) or expected["locator"] != locator:
                        raise CrawlerError("PDF value/locator mismatch")
                elif fact['metric'] == 'scenic_mountain_entries':
                    from .annual import parse_pdf as parse_annual
                    from decimal import Decimal
                    key = ('annual', asset.id)
                    if key not in pdf_cache:
                        pdf_cache[key] = parse_annual(safe_path(store.root, asset.relative_path), asset.source_id, registry['entities'], asset.url)
                    expected = next(r for r in pdf_cache[key]['records'] if r['period_start'].isoformat() == fact['period_start'])
                    if expected['value'] != Decimal(fact['value']) or expected['locator'] != locator:
                        raise CrawlerError('Annual PDF value/locator mismatch')
                else:
                    raise CrawlerError("No provenance verifier for PDF metric")
            if not fact["source_published_at"]:
                raise CrawlerError("Missing source publication timestamp")
            checked_facts += 1
        except (CrawlerError, KeyError, ValueError, OSError, StopIteration):
            errors.append({"fact_id": fact["id"], "code": "fact_or_locator_check_failed"})
    report_matrix = matrix(store, registry, start, end)
    return {"status": "passed" if not errors and not running else "failed", "verified_at": now(),
            "originals_verified": verified, "facts_verified": checked_facts, "active_runs": len(running),
            "errors": errors, "counts": store.statistics(), "data_acceptance_complete": report_matrix["all_targets_closed"],
            "matrix": report_matrix, "metric_dictionary": dictionary(), "schema_integrity_is_not_full_data_acceptance": True}


