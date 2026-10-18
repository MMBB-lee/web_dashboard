"""Preview by default; --apply copies crawler and C facts into the business MySQL."""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    project = Path(__file__).resolve().parents[2]
    parser.add_argument("--crawler-root", type=Path, default=project / "爬取工具/data/local")
    parser.add_argument("--manifest", type=Path, default=project / "data_foundation/real_data/2026-10-12-v2/manifest.json")
    parser.add_argument("--project-manifest", type=Path, default=project / "data_foundation/real_data/project_flow/manifest.json")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--migrate", action="store_true", help="与--apply一起使用，先执行增量建表")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.migrate and not args.apply:
        parser.error("--migrate必须和--apply一起使用")
    from data_foundation.ingestion.public_bridge import load_records, preview, apply_import
    records = load_records(args.crawler_root, args.manifest)
    result = preview(records)
    if args.project_manifest.is_file():
        from data_foundation.ingestion.project_flow import validate_package
        package=validate_package(args.project_manifest)
        result['project_data']={'rows':len(package['rows']),'start':package['start'],'end':package['end']}
    if args.apply:
        from app.core.config import get_settings
        from data_foundation import create_store
        settings = get_settings()
        if args.migrate:
            import os
            from alembic.config import Config
            from alembic import command
            os.environ["DATABASE_URL"] = settings.database_url
            command.upgrade(Config(str(project / "data_foundation/alembic.ini")), "head")
        store = create_store(settings.database_url, data_root=settings.data_dir)
        try:
            from data_foundation.ingestion.real_dataset import import_real_dataset
            baseline = import_real_dataset(store, args.manifest)
            result = apply_import(store, records)
            result["baseline_import"] = {key: baseline[key] for key in ["counts", "documents", "definitions"]}
            if args.project_manifest.is_file():
                from data_foundation.ingestion.project_flow import import_package
                result['project_data']=import_package(store,args.project_manifest)
        finally:
            store.dispose()
    output = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(output, encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
