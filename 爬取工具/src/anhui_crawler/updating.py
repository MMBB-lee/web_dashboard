"""Persistent bounded discovery, conditional retrieval, and due-source updates."""
from datetime import date, datetime, timedelta, timezone
import json
import re
from urllib.parse import urljoin, urlparse, urldefrag

from sqlalchemy import select
from sqlalchemy.orm import Session

from .airport import parse_pdf
from .annual import parse_pdf as parse_annual_pdf
from .bulletin import parse_bulletin
from .common import CrawlerError, key_for, now, safe_path, sha256, write_json
from .models import Asset, FetchState, Run, SourceState
from .network import DeferredRequest, Fetcher, Response, allowed_url
from .pipeline import airport_pdf_links, html_tree, monthly_links
from .topics import parse_topic


def instant(value=None):
    moment = datetime.fromisoformat(value) if value else datetime.now(timezone.utc)
    if moment.utcoffset() is None:
        raise CrawlerError("Scheduling timestamps require an explicit timezone")
    return moment.astimezone(timezone.utc)


def next_check(source, at):
    # Frequency concerns publication checks; statistical grain is never changed.
    holidays = source.get("holiday_windows", [])
    for window in holidays:
        if date.fromisoformat(window["start"]) <= at.date() <= date.fromisoformat(window["end"]) + timedelta(days=14):
            return (at + timedelta(days=1)).isoformat()
    days = 7
    if source["id"].startswith("city-") and at.month > 6:
        days = 30
    return (at + timedelta(days=days)).isoformat()


def state(store, source_id):
    with Session(store.engine) as session:
        row = session.get(SourceState, source_id)
        return {"source_id": source_id, "status": row.status, "last_checked": row.last_checked,
                "last_success": row.last_success, "next_due": row.next_due, "details": row.details} if row else {
                    "source_id": source_id, "status": "pending", "last_checked": None,
                    "last_success": None, "next_due": None, "details": {}}


def save_state(store, value):
    with Session(store.engine) as session, session.begin():
        row = session.get(SourceState, value["source_id"])
        if row is None:
            session.add(SourceState(**value))
        else:
            for k, v in value.items():
                setattr(row, k, v)


def cached_fetch(store, source, url, fetcher):
    allowed_url(url, source["allowed_hosts"])
    identity = key_for(source["id"], url)
    with Session(store.engine) as session:
        row = session.get(FetchState, identity)
        cached = {"etag": row.etag, "last_modified": row.last_modified, "asset_id": row.asset_id,
                  "retry_at": row.retry_at} if row else {}
        asset = session.get(Asset, cached["asset_id"]) if cached.get("asset_id") else None
        old = {"path": asset.relative_path, "sha256": asset.sha256, "size": asset.byte_size,
               "media_type": asset.media_type, "published_at": asset.published_at} if asset else None
    if cached.get("retry_at") and instant(cached["retry_at"]) > instant():
        raise DeferredRequest("Source Retry-After has not elapsed", cached["retry_at"])
    valid_cache = False
    if old:
        try:
            raw = safe_path(store.root, old["path"]).read_bytes()
            valid_cache = len(raw) == old["size"] and sha256(raw) == old["sha256"]
        except OSError:
            pass
    try:
        response = fetcher.fetch(url, etag=cached.get("etag") if valid_cache else None,
                                 last_modified=cached.get("last_modified") if valid_cache else None)
        if response.status == 304:
            if not valid_cache:
                raise CrawlerError("304 without a verified cached original")
            response = Response(url, response.effective_url, raw, old["media_type"], 304, response.retrieved_at,
                                response.etag or cached.get("etag"), response.last_modified or cached.get("last_modified"))
        elif response.status != 200:
            raise CrawlerError("Unexpected document response status")
        media = "application/pdf" if response.body.startswith(b"%PDF-") else response.content_type
        if response.content_type == "application/pdf" and media != "application/pdf":
            raise CrawlerError("PDF response is not a PDF")
        item = {"source_id": source["id"], "url": url, "effective_url": response.effective_url,
                "sha256": sha256(response.body), "byte_size": len(response.body), "media_type": media,
                "retrieved_at": response.retrieved_at, "published_at": old["published_at"] if response.status == 304 else None}
        asset_id, path = store.register_asset(item, response.body)
        fields = {"id": identity, "source_id": source["id"], "url": url, "asset_id": asset_id,
                  "etag": response.etag, "last_modified": response.last_modified,
                  "checked_at": now(), "retry_at": None, "status": "fetched",
                  "details": {"http_status": response.status, "sha256": item["sha256"]}}
    except CrawlerError as exc:
        with Session(store.engine) as session, session.begin():
            row = session.get(FetchState, identity)
            if row is None:
                row = FetchState(id=identity, source_id=source["id"], url=url, checked_at=now(), status="failed", details={})
                session.add(row)
            row.checked_at, row.status = now(), "failed"
            row.retry_at = getattr(exc, "retry_at", None)
            row.details = {**row.details, "error": str(exc)}
        raise
    with Session(store.engine) as session, session.begin():
        row = session.get(FetchState, identity)
        if row is None:
            session.add(FetchState(**fields))
        else:
            for k, v in fields.items():
                setattr(row, k, v)
    return item, asset_id, path, response


def discover_links(raw, url, source, start, end, entities=None):
    """Only explicit same-host topical links and next-page links; never all links."""
    if source["id"] == "caac-east-monthly":
        refs = monthly_links(raw, url, start[:7], end[:7])
        documents = [{"url": r["url"], "kind": "airport_article", "period": r["period"]} for r in refs]
    else:
        documents = []
    pages = []
    for a in html_tree(raw).xpath("//a[@href]"):
        title = "".join(a.itertext()).strip()
        if len(title) > 160:
            # Broken navigation anchors may contain the rest of the page.
            continue
        target = urldefrag(urljoin(url, a.get("href")))[0]
        try:
            allowed_url(target, source["allowed_hosts"])
        except (CrawlerError, ValueError):
            continue
        if urlparse(target).hostname != urlparse(url).hostname or target == url:
            continue
        if title in {"下一页", "下页", ">", "Next", "next"}:
            pages.append({"url": target, "kind": "page"})
        elif source["id"] != "caac-east-monthly" and any(word in title for word in ("统计公报", "接待游客", "旅客发送", "客流", "假期", "文旅市场")):
            if entities:
                associated = [e for e in entities if source["id"] in e["source_ids"]]
                terms = [e["name"].replace("风景区主景区", "").replace("景区", "") for e in associated]
                if not any(term in title for term in terms):
                    continue
            years = re.findall(r"20\d{2}", title)
            if years and not any(int(start[:4]) <= int(year) <= int(end[:4]) for year in years):
                continue
            documents.append({"url": target, "kind": "document", "title": title})
    return documents + pages


def crawl_source(store, registry, source, *, start, end, max_pages=20, max_documents=50, fetcher=None):
    if not 1 <= max_pages <= 20 or not 1 <= max_documents <= 50 or start > end:
        raise CrawlerError("Invalid discovery window/budget")
    date.fromisoformat(start), date.fromisoformat(end)
    current = state(store, source["id"])
    old = current["details"]
    window = {"start": start, "end": end}
    previous_window = old.get('window', {})
    resuming = (previous_window.get('start') == start and previous_window.get('end', end) <= end
                and bool(old.get('pending')))
    if resuming:
        queue, completed = list(old["pending"]), set(old.get("completed", []))
        if previous_window.get('end') != end:
            # A default daily cutoff advances at midnight. Carry unfinished work,
            # and revisit entry pages to discover newly published documents.
            entries = [{'url': url, 'kind': 'page' if source['id'] == 'caac-east-monthly' else 'document'}
                       for url in source.get('discovery_urls', [source['url']])]
            entry_urls = {entry['url'] for entry in entries}
            queue = entries + [task for task in queue if task['url'] not in entry_urls]
            completed -= entry_urls
    else:
        queue = [{"url": url, "kind": "page" if source["id"] == "caac-east-monthly" else "document"}
                 for url in source.get("discovery_urls", [source["url"]])]
        # Revisit previously known originals as well as the current entry points.
        with Session(store.engine) as session:
            for row in session.scalars(select(FetchState).where(FetchState.source_id == source["id"])):
                task_kind = row.details.get("task", {}).get("kind")
                revisitable = task_kind in {"page", "airport_article", "airport_pdf"} or row.details.get("numeric_rows", 0) > 0
                if revisitable and row.url not in {q["url"] for q in queue}:
                    queue.append({"url": row.url, **{k: v for k, v in row.details.get("task", {"kind": "document"}).items() if k != "url"}})
        completed = set()
    run_id = store.start_run("incremental_source")
    report = {"run_id": run_id, "source_id": source["id"], "status": "running", "started_at": now(),
              "window": window, "items": [], "records": {k: 0 for k in ("inserted", "skipped", "revised", "quarantined")},
              "discovery_complete_in_configured_entries": False, "public_search_audit_complete": False,
              "resumed": resuming, "robots": []}
    fetcher = fetcher or Fetcher(source["allowed_hosts"])
    pages = documents = 0
    failures, review = [], list(old.get("review_required", [])) if resuming else []

    def checkpoint(status="running"):
        current.update(last_checked=now(), status=status,
            details={"window": window, "pending": queue + [f["task"] for f in failures if f["task"] not in queue], "completed": sorted(completed),
                     "failures": failures, "review_required": review, "last_run_id": run_id})
        save_state(store, current)

    checkpoint()
    try:
        while queue:
            task = queue[0]
            if task["url"] in completed:
                queue.pop(0)
                continue
            if (task["kind"] == "page" and pages >= max_pages) or (task["kind"] != "page" and documents >= max_documents):
                break
            pages += task["kind"] == "page"
            documents += task["kind"] != "page"
            item_report = {"url": task["url"], "kind": task["kind"]}
            review_before = len(review)
            try:
                item, asset_id, path, response = cached_fetch(store, source, task["url"], fetcher)
                rows, links = [], []
                if task["kind"] == "airport_pdf":
                    if item["media_type"] != "application/pdf":
                        raise CrawlerError("Airport attachment returned non-PDF")
                    rows = parse_pdf(path, task["period"], registry["entities"])
                    if task.get("published_at"):
                        item["published_at"] = task["published_at"]
                        store.register_asset(item, response.body)
                elif task["kind"] == "airport_article":
                    links = [{**link, "kind": "airport_pdf", "period": task["period"]} for link in airport_pdf_links(response.body, task["url"])]
                elif item["media_type"] == "text/html":
                    parsed = parse_bulletin(response.body, source["id"], registry["entities"]) or parse_topic(response.body, source["id"], registry["entities"])
                    if parsed:
                        item["published_at"] = parsed["published_at"]
                        store.register_asset(item, response.body)
                        rows = parsed["records"]
                    links = discover_links(response.body, task["url"], source, start, end, registry["entities"])
                    if task["kind"] == "page" and source["id"] == "caac-east-monthly" and not monthly_links(response.body, task["url"], "2000-01", "2099-12"):
                        review.append({"url": task["url"], "reason": "monthly_directory_format_or_coverage_requires_review", "asset_id": asset_id})
                    if not parsed and task["kind"] != "page":
                        review.append({"url": task["url"], "reason": "article_or_entry_requires_adapter_or_scope_review", "asset_id": asset_id})
                else:
                    annual = parse_annual_pdf(path, source["id"], registry["entities"], task["url"]) if item['media_type'] == 'application/pdf' else None
                    if annual:
                        item['published_at'] = annual['published_at']
                        store.register_asset(item, response.body)
                        rows = annual['records']
                    else:
                        review.append({"url": task["url"], "reason": "unsupported_document_format_requires_reviewed_adapter", "asset_id": asset_id})
                rows = [r for r in rows if r["period_start"] <= date.fromisoformat(end) and r["period_end"] >= date.fromisoformat(start)]
                counts = store.write_facts(run_id, asset_id, rows)
                for k, v in counts.items():
                    report["records"][k] += v
                if counts["quarantined"]:
                    review.append({"url": task["url"], "reason": "fact_conflict_requires_review"})
                review = [r for r in review[:review_before] if r['url'] != task['url']] + review[review_before:]
                known = completed | {q["url"] for q in queue}
                for link in links:
                    if link["url"] not in known:
                        queue.append(link)
                        known.add(link["url"])
                # Cursor and completed URLs advance only after successful parsing/write.
                queue.pop(0)
                completed.add(task["url"])
                with Session(store.engine) as session, session.begin():
                    row = session.get(FetchState, key_for(source["id"], task["url"]))
                    row.status = "processed"
                    row.details = {**row.details, "task": task, "numeric_rows": len(rows)}
                item_report.update(status="processed", http_status=response.status, sha256=item["sha256"], counts=counts)
            except (CrawlerError, ValueError, OSError) as exc:
                reason = str(exc) if isinstance(exc, CrawlerError) else "Source parsing/I/O failed; review input"
                failure = {"url": task["url"], "reason": reason, "retry_at": getattr(exc, "retry_at", None)}
                failures.append(failure)
                store.issue(run_id, source["id"], "incremental_fetch_or_parse_failed", failure)
                item_report.update(status="failed", **failure)
                # Move failure out of this cycle; keep it in pending at finalization.
                queue.pop(0)
                failure["task"] = task
            report["items"].append(item_report)
            checkpoint()
        queue.extend(f["task"] for f in failures)
        report["robots"] = [p[1] for p in getattr(fetcher, "robots", {}).values()]
        report["discovery_complete_in_configured_entries"] = not queue
        report["pending"] = len(queue)
        report["review_required"] = review
        report["status"] = "partial" if queue or review else "succeeded"
        checked = instant()
        retries = [f["retry_at"] for f in failures if f.get("retry_at")]
        current["next_due"] = max(retries, key=instant) if retries else (checked + timedelta(hours=1)).isoformat() if failures else (checked + timedelta(minutes=5)).isoformat() if queue else next_check(source, checked)
        if report["status"] == "succeeded":
            current["last_success"] = now()
        checkpoint(report["status"])
    except BaseException:
        # The last checkpoint still contains the in-flight URL. Do not swallow
        # interrupts; the next exclusive process marks this run interrupted.
        report["status"] = "interrupted"
        raise
    finally:
        report["finished_at"] = now()
        store.finish_run(run_id, report)
    return report


def recover_runs(store):
    with Session(store.engine) as session, session.begin():
        rows = list(session.scalars(select(Run).where(Run.status == "running")))
        for row in rows:
            row.status, row.finished_at = "interrupted", now()
            row.summary = {**row.summary, "recovered_by_next_exclusive_process": True}
        return len(rows)


def update(store, registry, *, start="2023-01-01", end=None, due=True, source_ids=None, as_of=None,
           max_pages=20, max_documents=50, fetcher_factory=None, progress=None):
    at = instant(as_of)
    end = end or at.astimezone(timezone(timedelta(hours=8))).date().isoformat()
    reports = []
    recovered = recover_runs(store)
    selected = [s for s in registry["sources"] if not source_ids or s["id"] in source_ids]
    if source_ids and set(source_ids) - {s["id"] for s in selected}:
        raise CrawlerError("Unknown source selection")
    for source in selected:
        previous = state(store, source["id"])
        if due and not source.get("automatic_scheduling_enabled", False):
            reports.append({"source_id": source["id"], "status": "disabled_requires_review", "next_due": previous["next_due"]})
            continue
        if due and previous["next_due"] and instant(previous["next_due"]) > at:
            reports.append({"source_id": source["id"], "status": "not_due", "next_due": previous["next_due"]})
            continue
        try:
            report = crawl_source(store, registry, source, start=start, end=end,
                max_pages=max_pages, max_documents=max_documents,
                fetcher=fetcher_factory(source) if fetcher_factory else None)
        except Exception as exc:
            report = {"source_id": source["id"], "status": "failed", "error_type": type(exc).__name__}
        reports.append(report)
        if progress:
            progress({"source_id": source["id"], "status": report["status"]})
    result = {"status": "succeeded" if all(r["status"] in {"succeeded", "not_due", "disabled_requires_review"} for r in reports) else "partial",
              "started_at": at.isoformat(), "finished_at": now(), "clock_override": bool(as_of),
              "window": {"start": start, "end": end}, "recovered_interrupted_runs": recovered,
              "sources": reports, "team_database_touched": False}
    report_path = store.root / "runs" / f"update-{key_for(now())[:24]}.json"
    write_json(report_path, result)
    return result
