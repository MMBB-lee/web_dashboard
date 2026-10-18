"""Offline real-document question suite; HTTP and semantic review are separate."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run_suite(snapshot, suite):
    from intelligent_analysis import ServiceBindings, answer_message, configure_services

    rows = []
    for case in suite["cases"]:
        corpus = {"documents": {}} if case.get("corpus") == "empty" else snapshot
        configure_services(ServiceBindings(knowledge_snapshot_loader=lambda corpus=corpus: corpus))
        context = {"as_of": case.get("as_of", suite["as_of"])}
        category = case["category"]
        if category.startswith("metric"):
            context["metric_query"] = {"metric": "fixture_people", "entity_id": "fixture-only"}
        if category == "metric_missing_provenance":
            context["metric_tool"] = lambda q: {"value": 123.5}
        if category == "metric_fixture":
            context["metric_tool"] = lambda q: {"metric": "fixture_people", "value": 123.5,
                "period_start": "2026-10-15T08:00:00+08:00", "period_end": "2026-10-15T08:05:00+08:00",
                "queried_at": suite["as_of"], "source_ids": ["synthetic-tool-source"]}
        checks = {}
        answer = None
        try:
            answer = answer_message(case["question"], context=context)
            checks["expected_error"] = "expected_error" not in case
            checks["status"] = answer["status"] == case.get("expected_status")
            checks["required_text"] = all(s in answer["answer"] for s in case["required_text"])
            if case.get("document_id"):
                checks["expected_document"] = any(c["id"] == case["document_id"] for c in answer["citations"])
            if not category.startswith("metric"):
                checks["citations_match_source_and_chunks"] = all(
                    citation_matches(c, corpus) for c in answer["citations"])
                if answer["status"] == "insufficient_evidence":
                    checks["no_fabricated_citations"] = not answer["citations"]
            if category == "metric_fixture":
                checks["metric_provenance"] = answer["metric_result"]["source_ids"] == ["synthetic-tool-source"]
        except (ValueError, RuntimeError) as exc:
            checks["expected_error"] = type(exc).__name__ == case.get("expected_error")
        rows.append({"id": case["id"], "question": case["question"], "category": category,
            "automated_passed": bool(checks) and all(checks.values()), "checks": checks,
            "answer_excerpt": answer["answer"][:600] if answer else None,
            "citations": answer["citations"] if answer else [],
            "human_review_status": "pending", "human_review": case["human_review"]})
    configure_services(ServiceBindings())
    return {"status": "automated_passed" if all(r["automated_passed"] for r in rows) else "failed",
        "case_count": len(rows), "passed": sum(r["automated_passed"] for r in rows),
        "human_semantic_review": "pending", "D_HTTP_and_intent_routing_verified": False,
        "numeric_tool_is_synthetic": True, "cases": rows}


def citation_matches(citation, snapshot):
    document = snapshot["documents"].get(citation["id"])
    if document is None:
        return False
    return (citation["url"] == document["metadata"].get("source_url")
        and citation["title"] == document["metadata"]["title"]
        and citation["date"] == document["metadata"].get("published_at")
        and citation["chunk_id"] in {c["chunk_id"] for c in document["chunks"]})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "docs/acceptance/RAG-question-suite.json")
    args = parser.parse_args()
    # Default: the shipped real historical notice, reproducible without private DB paths.
    fixture = ROOT / "docs/fixtures/C020a2-historical-notice/index.json"
    expected = json.loads(fixture.with_name("manifest.json").read_text(encoding="utf-8"))
    if hashlib.sha256(fixture.read_bytes()).hexdigest() != expected["sha256"]:
        raise ValueError("Shipped historical artifact SHA256 mismatch")
    snapshot = json.loads(fixture.read_text(encoding="utf-8"))
    suite = json.loads((ROOT / "docs/fixtures/rag-acceptance/questions.json").read_text(encoding="utf-8"))
    report = run_suite(snapshot, suite)
    report.update(corpus="shipped_real_historical_notice", artifact_sha256=expected["sha256"],
        c_verified_store_receiving="separate_C020a2_report")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("status", "case_count", "passed", "human_semantic_review")}))
    if report["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
