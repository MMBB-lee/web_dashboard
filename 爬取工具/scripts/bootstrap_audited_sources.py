"""One-time COPY of audited files into a portable independent crawler seed.

Reads JSON/files only, imports no team package, changes/deletes no old material.
All later crawler commands need only the output directory and own registry.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from urllib.parse import urlparse


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def checked_path(root, relative):
    parts = PurePosixPath(relative)
    if (not relative or "\\" in relative or "\0" in relative or parts.is_absolute()
            or parts.as_posix() != relative or ".." in parts.parts):
        raise ValueError("Unsafe input path")
    result = root.resolve() / parts
    if result.is_symlink() or not result.resolve().is_relative_to(root.resolve()):
        raise ValueError("Input outside seed root")
    return result


def json_file(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()
    if path.exists() and path.read_bytes() != raw:
        raise ValueError("Existing generated manifest differs; use a fresh bootstrap output")
    path.write_bytes(raw)


def bootstrap(audit_registry, dataset, audit_dir, output):
    registry = json.loads(audit_registry.read_text(encoding="utf-8"))
    plan = json.loads((dataset / "source_plan.json").read_text(encoding="utf-8"))
    acquired = []
    for name in ("acquisition.json", "acquisition_history.json"):
        acquired.extend(json.loads((audit_dir / name).read_text(encoding="utf-8")))
    host_map = {s["id"]: {urlparse(s["url"]).hostname} for s in registry["sources"]}
    for item in acquired:
        if item.get("path"):
            for field in ("url", "effective_url"):
                if item.get(field) and urlparse(item[field]).scheme == "https":
                    host_map[item["source_id"]].add(urlparse(item[field]).hostname)
    sources = []
    for source in registry["sources"]:
        sources.append({"id": source["id"], "url": source["url"], "title": source["title"],
            "publisher": source["publisher"], "allowed_hosts": sorted(h for h in host_map[source["id"]] if h),
            "adapter": "caac_monthly" if source["id"] == "caac-east-monthly" else "archive_only",
            "native_grain": source.get("native_grain"), "scope_note": source["scope_note"],
            "priority": source["priority"], "licence_status": source["licence_status"],
            "source_review": "audited_sample_only_not_all_fields_verified",
            "automatic_scheduling_enabled": False})
    entities = []
    for entity in registry["entities"]:
        entities.append({"id": entity["key"], "name": entity["name"], "type": entity["type"],
            "region_code": entity["region_code"], "source_ids": entity["source_ids"],
            "aliases": entity.get("aliases", []), "scope_note": entity["scope_note"],
            "target_metrics": ["airport_passenger_throughput"] if entity["type"] == "airport" else entity["target_metrics"]})
    independent_registry = {"schema_version": "anhui-crawler-registry-1", "origin_audit_date": registry["audit_date"],
        "scope": "independent_Anhui_public_data_collection", "sources": sources, "entities": entities}
    assets, seen = [], set()

    def copy(item, root):
        path = checked_path(root, item["path"])
        raw = path.read_bytes()
        if sha(raw) != item["sha256"] or len(raw) != item["byte_size"]:
            raise ValueError("Audited source changed; integrity check failed")
        identity = (item["source_id"], item["url"], item["sha256"])
        if identity in seen:
            return
        seen.add(identity)
        suffix = ".pdf" if item["media_type"] == "application/pdf" else ".html"
        target = output / "inputs" / item["sha256"] / ("body" + suffix)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and sha(target.read_bytes()) != item["sha256"]:
            raise ValueError("Existing copied asset is corrupt")
        if not target.exists():
            target.write_bytes(raw)
        assets.append({**item, "path": target.relative_to(output).as_posix()})

    for request in plan["requests"]:
        item = request.get("metadata") or {}
        period = item.get("period", "")
        if item.get("media_type") != "application/pdf" or not re.fullmatch(r"20\d{2}-(?:\d{2}|annual)", period):
            continue
        copy({"source_id": "caac-east-monthly", "url": item["url"], "effective_url": item.get("effective_url", item["url"]),
            "sha256": item["sha256"], "byte_size": item["byte_size"], "media_type": item["media_type"],
            "path": item["path"], "retrieved_at": item["retrieved_at"], "published_at": item.get("published_at"),
            "title": item["title"], "page_url": item.get("page_url"),
            "period": period if "annual" not in period else None,
            "original_reporting_label": period, "usage_note": item["usage_note"]}, dataset)
    for item in acquired:
        if not item.get("path") or not item.get("sha256"):
            continue
        copy({"source_id": item["source_id"], "url": item["url"], "effective_url": item.get("effective_url", item["url"]),
            "path": item["path"], "sha256": item["sha256"], "byte_size": item["byte_size"],
            "media_type": item["media_type"], "retrieved_at": item["checked_at"], "published_at": None,
            "audit_status": item["status"], "period": None, "usage_note": "Public sample; numeric fields/redistribution terms need source-specific review"}, audit_dir)
    json_file(output / "registry.json", independent_registry)
    json_file(output / "seed.json", {"schema_version": "anhui-crawler-seed-1", "assets": assets})
    summary = {"status": "copied_and_verified", "entities": len(entities), "sources": len(sources),
               "monthly_airport_PDFs": sum(bool(a.get("period")) for a in assets),
               "other_originals_for_review": sum(not a.get("period") for a in assets),
               "source_inputs_modified": False, "team_packages_imported": False}
    json_file(output / "bootstrap-receipt.json", summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-registry", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--audit-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(bootstrap(args.audit_registry, args.dataset, args.audit_dir, args.output), ensure_ascii=False))
