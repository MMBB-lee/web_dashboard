"""Freeze a read-only SQLite runtime into a new private work directory.

Run with the installed release wheel (python -I). All verification, export and
restore operations use the copy. Evidence contains no originals or credentials;
the recovery ZIP remains private. This never installs or triggers a scheduler.
"""
import argparse
from collections import Counter
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from zipfile import ZipFile

import anhui_crawler
from anhui_crawler.cli import export_data
from anhui_crawler.common import CrawlerError, canonical, now, safe_path, sha256, write_json
from anhui_crawler.gate import final_gate
from anhui_crawler.locking import run_lock
from anhui_crawler.maintenance import backup, restore
from anhui_crawler.pipeline import load_registry
from anhui_crawler.reporting import verify
from anhui_crawler.storage import Store


def snapshot(source, destination):
    """SQLite online backup fixes the DB view without writing the live root."""
    if destination.exists():
        raise CrawlerError('Snapshot destination must not exist')
    destination.mkdir(parents=True)
    with sqlite3.connect((source / 'crawler.sqlite3').as_uri() + '?mode=ro', uri=True) as live:
        with sqlite3.connect(destination / 'crawler.sqlite3') as target:
            live.backup(target)
            if target.execute("SELECT COUNT(*) FROM ac_runs WHERE status='running'").fetchone()[0]:
                raise CrawlerError('Live run in progress; take a new snapshot after completion')
            assets = target.execute('SELECT relative_path, sha256, byte_size FROM ac_assets').fetchall()
    for relative, digest, size in assets:
        raw = safe_path(source, relative).read_bytes()
        if len(raw) != size or sha256(raw) != digest:
            raise CrawlerError('Live original integrity mismatch')
        path = safe_path(destination, relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    for pattern in ('audit.json', 'audits/*.json', 'migrations/*.json', 'runs/*.json'):
        for path in source.glob(pattern):
            target = safe_path(destination, path.relative_to(source).as_posix())
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)


def descriptor(path):
    return {'name': path.name, 'sha256': sha256(path.read_bytes()), 'byte_size': path.stat().st_size}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='New private work directory')
    parser.add_argument('--evidence', type=Path, required=True, help='Existing source-tests.xml directory')
    parser.add_argument('--end', required=True)
    args = parser.parse_args()
    end = date.fromisoformat(args.end)
    today = datetime.now(timezone(timedelta(hours=8))).date()
    if not date(2023, 1, 1) <= end <= today:
        raise CrawlerError('Cutoff must be between 2023-01-01 and today in Beijing')
    root = Path(__file__).resolve().parents[1]
    output, evidence, source = args.output.resolve(), args.evidence.resolve(), args.source_root.resolve()
    if output.exists() or (evidence / 'closeout.json').exists():
        raise CrawlerError('Closeout destinations must be new; do not overwrite evidence')
    if output == source or output.is_relative_to(source) or source.is_relative_to(output):
        raise CrawlerError('Private output must be separate from live data')
    evidence.mkdir(parents=True, exist_ok=True)
    release = root / 'release'
    old = root / 'acceptance/2026-10-16-implementation'
    receipt = json.loads((release / 'receipt-0.3.0a1.json').read_text())
    baseline = json.loads((old / 'baseline.json').read_text())
    package = Path(anhui_crawler.__file__).resolve().parent
    if not package.is_relative_to(Path(sys.prefix).resolve()) or 'site-packages' not in package.parts:
        raise CrawlerError('Run with the non-editable installed release wheel')
    files = receipt['files'] + [receipt['dependencies']] + baseline['old_release_files']
    for expected in files:
        actual = descriptor(release / expected['name'])
        if actual['sha256'] != expected['sha256']:
            raise CrawlerError('Previously released artifact changed')
    wheel = release / receipt['files'][0]['name']
    installed_checked = 0
    with ZipFile(wheel) as archive:
        for name in archive.namelist():
            if name.startswith('anhui_crawler/') and not name.endswith('/'):
                raw = archive.read(name)
                if (package.parent / name).read_bytes() != raw or (root / 'src' / name).read_bytes() != raw:
                    raise CrawlerError('Wheel, installed package and current source differ')
                installed_checked += 1
    work = output / 'snapshot'
    snapshot(source, work)
    registry = load_registry()
    store = Store('sqlite+pysqlite:///' + str(work / 'crawler.sqlite3'), work)
    try:
        with run_lock(store):
            store.initialize()
            store.seed(registry)
            facts = store.export_facts(include_candidates=True)
            # Use the immutable public data release so this also works after
            # unpacking the delivery bundle, without a private development file.
            with ZipFile(release / 'anhui-crawler-data-0.2.0-2026-10-15.zip') as archive:
                originals = json.loads(archive.read('anhui-crawler-data-0.2.0/facts.json'))
            fingerprints = baseline['fact_fingerprints']
            rows = {f['id']: f for f in facts}
            if {r['id'] for r in originals['records']} != set(fingerprints):
                raise CrawlerError('Baseline identity set differs from release evidence')
            for row in originals['records']:
                if rows.get(row['id']) != row:
                    raise CrawlerError('An original baseline fact changed')
            quality = verify(store, registry, end=args.end)
            write_json(evidence / 'quality.json', quality)
            if quality['status'] != 'passed':
                raise CrawlerError('Snapshot original/fact verification failed')
            exported = export_data(store, registry, argparse.Namespace(output=evidence / 'export',
                start='2023-01', end=args.end[:7], facts_start='2023-01-01', facts_end=args.end))
            recovery = backup(store, output / 'PRIVATE-runtime-recovery.zip')
    finally:
        store.dispose()
    target = output / 'restored'
    restored = Store('sqlite+pysqlite:///' + str(target / 'crawler.sqlite3'), target)
    try:
        with run_lock(restored):
            restore(restored, output / recovery['file'])
            if restored.export_facts(include_candidates=True) != facts or verify(restored, registry, end=args.end)['status'] != 'passed':
                raise CrawlerError('Restored originals or business facts differ')
    finally:
        restored.dispose()
    runs = [json.loads(p.read_text()) for p in (work / 'runs').glob('update-*.json')]
    latest = max(runs, key=lambda r: r['finished_at']) if runs else None
    if latest:
        write_json(evidence / 'latest-natural-update.json', latest)
    # Query the task; this is evidence of current registration only. The stored
    # update and the system query are both retained, without inventing history.
    query = subprocess.run(['launchctl', 'print', f'gui/{__import__("os").getuid()}/org.anhui.public-data-crawler'],
        capture_output=True, text=True, timeout=15) if sys.platform == 'darwin' else None
    task = {'queried_at': now(), 'platform': sys.platform,
        'query_succeeded': query is not None and query.returncode == 0,
        'task_fields': [line.strip() for line in query.stdout.splitlines() if any(
            marker in line for marker in ('state =', 'runs =', 'last exit code =', '"Hour"', '"Minute"'))] if query else [],
        'task_triggered_by_closeout': False}
    write_json(evidence / 'native-task-query.json', task)
    gate = final_gate(quality, wheel=wheel, mac_receipt=old / 'native-mac/receipt.json', schedules=old / 'schedulers')
    write_json(evidence / 'final-gate.json', gate)
    failures = [{'source_id': r['source_id'], 'url': item['url'], 'reason': item['reason']}
        for r in (latest or {}).get('sources', []) for item in r.get('items', []) if item['status'] == 'failed']
    summary = {'status': 'verified_existing_scope', 'frozen_at': now(), 'cutoff_beijing': args.end,
        'crawler_version': anhui_crawler.__version__, 'full_acceptance': gate['full_acceptance'],
        'blockers': gate['blockers'], 'export': exported, 'restored_business_facts_identical': True,
        'baseline_facts_unchanged': len(fingerprints), 'old_release_files_unchanged': len(baseline['old_release_files']),
        'release_files_verified': len(files), 'installed_and_source_files_identical_to_wheel': installed_checked,
        'business_fact_sha256': sha256(canonical(facts).encode()), 'private_recovery': recovery,
        'latest_update': {k: latest[k] for k in ('status', 'started_at', 'finished_at', 'clock_override')} if latest else None,
        'latest_source_outcomes': dict(Counter(r['status'] for r in (latest or {}).get('sources', []))),
        'latest_failures': failures, 'live_database_opened_read_only': True, 'team_database_touched': False,
        'mysql_evidence': {'status': 'prior_verified_not_rerun', 'date': '2026-10-16',
            'receipt': '../2026-10-16-implementation/native-mac/receipt.json'}}
    write_json(evidence / 'closeout.json', summary)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    main()
