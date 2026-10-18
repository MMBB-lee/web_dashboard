"""Run against an installed wheel, with private copied originals, on each OS.

No system schedules are installed and no credentials are printed. MySQL requires
an existing empty independent *_test schema in CRAWLER_ACCEPTANCE_MYSQL_URL.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import platform
import sys
from zipfile import ZipFile

from sqlalchemy import inspect, text

import anhui_crawler
from anhui_crawler.common import CrawlerError, now, sha256, write_json
from anhui_crawler.locking import AlreadyRunning, run_lock
from anhui_crawler.maintenance import backup, restore
from anhui_crawler.pipeline import ingest_seed, load_registry
from anhui_crawler.reporting import verify
from anhui_crawler.storage import Store, validate_url


def database_checks(url, root, manifest, output):
    store = Store(url, root)
    try:
        if inspect(store.engine).get_table_names() or inspect(store.engine).get_view_names():
            raise CrawlerError('Acceptance database must be empty')
        with run_lock(store):
            store.initialize()
            store.seed(load_registry())
            other = Store(url, root)
            try:
                try:
                    with run_lock(other):
                        raise CrawlerError('Second process unexpectedly acquired lock')
                except AlreadyRunning:
                    pass
            finally:
                other.dispose()
            first = ingest_seed(store, load_registry(), manifest)
            before = store.export_facts(include_candidates=True)
            second = ingest_seed(store, load_registry(), manifest)
            if first['status'] != 'succeeded' or second['status'] != 'succeeded' or second['records']['inserted'] or second['records']['revised'] or before != store.export_facts(include_candidates=True):
                raise CrawlerError('Real backfill or repeat verification failed')
            quality = verify(store, load_registry())
            if quality['status'] != 'passed':
                raise CrawlerError('Original/fact verification failed')
            snapshot = output / ('backup-' + store.engine.dialect.name + '.zip')
            snapshot_receipt = backup(store, snapshot)
        with store.engine.connect() as connection:
            database_version = connection.scalar(text('SELECT VERSION()' if store.engine.dialect.name == 'mysql' else 'SELECT sqlite_version()'))
        report = {'status': 'passed', 'database_dialect': store.engine.dialect.name, 'database_version': database_version,
                  'first': first, 'repeat': second, 'quality': quality, 'backup': snapshot_receipt,
                  'business_fact_sha256': sha256(json.dumps(before, ensure_ascii=False, sort_keys=True).encode()),
                  'lock_contention_checked': True}
        write_json(output / ('database-' + store.engine.dialect.name + '.json'), report)
        return report, before, snapshot
    finally:
        store.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheel', type=Path, required=True)
    parser.add_argument('--seed', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--require-platform', choices=['Windows', 'Darwin', 'Linux'])
    parser.add_argument('--require-mysql', action='store_true')
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        parser.error('Use a new/empty output directory')
    args.output.mkdir(parents=True, exist_ok=True)
    report = {'status': 'failed', 'started_at': now(), 'platform': platform.system(), 'os': platform.platform(),
              'python': platform.python_version(), 'crawler_version': anhui_crawler.__version__,
              'wheel_sha256': sha256(args.wheel.read_bytes()), 'scheduler_acceptance': 'pending_separate_native_evidence',
              'full_acceptance': False}
    try:
        if args.require_platform and platform.system() != args.require_platform:
            raise CrawlerError('Required native operating system is unavailable')
        package = Path(anhui_crawler.__file__).resolve().parent
        if not package.is_relative_to(Path(sys.prefix).resolve()) or 'site-packages' not in package.parts:
            raise CrawlerError('Use a clean environment with an installed non-editable wheel')
        checked = 0
        with ZipFile(args.wheel) as wheel:
            for member in wheel.namelist():
                if member.startswith('anhui_crawler/') and not member.endswith('/'):
                    installed = package.parent / member
                    if not installed.is_file() or installed.read_bytes() != wheel.read(member):
                        raise CrawlerError('Installed package differs from the formal wheel')
                    checked += 1
        if any(importlib.util.find_spec(module) for module in ('data_foundation', 'intelligent_analysis')):
            raise CrawlerError('Acceptance environment contains team packages')
        report['installed_files_checked'] = checked
        root = args.output.resolve() / 'sqlite'
        sqlite, before, snapshot = database_checks('sqlite+pysqlite:///' + str(root / 'crawler.sqlite3'), root, args.seed, args.output)
        restored_root = args.output.resolve() / 'restored'
        restored = Store('sqlite+pysqlite:///' + str(restored_root / 'crawler.sqlite3'), restored_root)
        try:
            with run_lock(restored):
                restore(restored, snapshot)
                if restored.export_facts(include_candidates=True) != before or verify(restored, load_registry())['status'] != 'passed':
                    raise CrawlerError('Restored business facts differ')
        finally:
            restored.dispose()
        report.update(sqlite='passed', restore='passed', fact_count=len(before), mysql='pending')
        mysql_url = os.environ.get('CRAWLER_ACCEPTANCE_MYSQL_URL')
        if mysql_url:
            parsed = validate_url(mysql_url)
            if parsed.drivername != 'mysql+pymysql' or not parsed.database.endswith('_test'):
                raise CrawlerError('MySQL acceptance requires an independent *_test schema')
            mysql, mysql_facts, _ = database_checks(mysql_url, args.output.resolve() / 'mysql', args.seed, args.output)
            if mysql_facts != before:
                raise CrawlerError('SQLite and MySQL business facts differ')
            report['mysql'] = 'passed'
        if args.require_mysql and report['mysql'] != 'passed':
            raise CrawlerError('Required MySQL acceptance configuration is missing')
        report['status'] = 'passed'
    except Exception as exc:
        report.update(error_type=type(exc).__name__, reason=str(exc) if isinstance(exc, CrawlerError) else 'Acceptance failed; inspect controlled local inputs')
    finally:
        report['finished_at'] = now()
        write_json(args.output / 'receipt.json', report)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
