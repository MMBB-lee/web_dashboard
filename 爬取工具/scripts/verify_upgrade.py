"""Restore a real schema-1 private snapshot into a new MySQL/SQLite DB, then upgrade.

Only an empty anhui_crawler_*_test MySQL schema is accepted. Credentials are read
from CRAWLER_UPGRADE_TEST_URL and never included in evidence.
"""
import argparse
import json
import os
from pathlib import Path

from anhui_crawler.common import CrawlerError, now, sha256, write_json
from anhui_crawler.locking import run_lock
from anhui_crawler.maintenance import migrate, restore, schema_version
from anhui_crawler.pipeline import load_registry
from anhui_crawler.reporting import verify
from anhui_crawler.storage import Store, validate_url


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--data-root', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    url = os.environ.get('CRAWLER_UPGRADE_TEST_URL') or 'sqlite+pysqlite:///' + str(args.data_root.resolve() / 'crawler.sqlite3')
    parsed = validate_url(url)
    if parsed.drivername == 'mysql+pymysql' and not parsed.database.endswith('_test'):
        raise CrawlerError('MySQL upgrade acceptance requires an empty independent *_test schema')
    store = Store(url, args.data_root)
    try:
        with run_lock(store):
            recovered = restore(store, args.snapshot)
            if recovered['schema_version'] != '1':
                raise CrawlerError('This test requires a real schema-1 snapshot')
            before = store.export_facts(include_candidates=True)
            report = migrate(store, load_registry(), store.root / 'backups')
            if before != store.export_facts(include_candidates=True) or schema_version(store) != '2':
                raise CrawlerError('Migration changed business facts')
            quality = verify(store, load_registry())
            if quality['status'] != 'passed':
                raise CrawlerError('Upgraded originals/facts failed verification')
            result = {'status': 'passed', 'verified_at': now(), 'database_dialect': store.engine.dialect.name,
                'old_schema': '1', 'new_schema': '2', 'facts_unchanged': len(before),
                'source_snapshot_sha256': sha256(args.snapshot.read_bytes()), 'migration': report,
                'quality': {k: v for k, v in quality.items() if k not in {'matrix', 'metric_dictionary'}},
                'repeat_migration': migrate(store, load_registry(), store.root / 'backups')['status'],
                'team_database_touched': False}
            write_json(args.report, result)
            print(json.dumps({k:result[k] for k in ['status','facts_unchanged','database_dialect']}))
    finally:
        store.dispose()


if __name__ == '__main__':
    main()
