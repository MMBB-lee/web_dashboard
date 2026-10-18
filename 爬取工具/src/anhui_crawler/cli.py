from __future__ import annotations

import argparse
import calendar
import csv
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys

from . import __version__
from .bulletin import capabilities
from .annual import capabilities as annual_capabilities
from .common import CrawlerError, now, write_json
from .locking import AlreadyRunning, run_lock
from .maintenance import backup, migrate, restore
from .metrics import dictionary
from .pipeline import coverage, discover_caac, ingest_seed, load_registry
from .reporting import load_audit, matrix, verify
from .storage import Store
from .scheduling import generate as generate_schedule
from .topics import capabilities as topic_capabilities
from .updating import state, update


def progress(item):
    print(json.dumps({k: item[k] for k in ('source_id', 'period', 'status') if k in item}, ensure_ascii=False), file=sys.stderr, flush=True)


def write_csv(path, rows):
    fields = list(rows[0]) if rows else ['id', 'entity_id', 'metric', 'value', 'unit', 'quality']
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for k, v in row.items()})


def export_data(store, registry, args):
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        raise CrawlerError('Use a new/empty export directory; existing exports are not overwritten')
    start = date.fromisoformat(args.facts_start) if args.facts_start else None
    end = date.fromisoformat(args.facts_end) if args.facts_end else None
    facts = store.export_facts(start=start, end=end)
    candidates = [r for r in store.export_facts(include_candidates=True, start=start, end=end) if r['quality'] == 'candidate']
    cov = coverage(store, registry, args.start, args.end)
    matrix_end = date(int(args.end[:4]), int(args.end[5:]), calendar.monthrange(int(args.end[:4]), int(args.end[5:]))[1]).isoformat()
    matrix_end = min(matrix_end, datetime.now(timezone(timedelta(hours=8))).date().isoformat())
    mat = matrix(store, registry, args.start + '-01', matrix_end)
    quality = verify(store, registry, start=args.start + '-01', end=matrix_end)
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / 'facts.json', {'schema_version': 'anhui-crawler-facts-1', 'records': facts})
    write_csv(output / 'facts.csv', facts)
    write_json(output / 'candidates.json', {'schema_version': 'anhui-crawler-facts-1', 'records': candidates})
    write_csv(output / 'candidates.csv', candidates)
    write_json(output / 'coverage.json', cov)
    write_json(output / 'matrix.json', mat)
    write_json(output / 'gaps.json', {'rows': [r for r in mat['rows'] if not r['audit_closed']]})
    write_json(output / 'source_status.json', {'sources': [state(store, s['id']) for s in registry['sources']]})
    write_json(output / 'quality.json', {k: v for k, v in quality.items() if k not in {'matrix', 'metric_dictionary'}})
    write_json(output / 'registry.json', registry)
    write_json(output / 'metrics.json', dictionary())
    write_json(output / 'parser_capabilities.json', {'city_bulletins': capabilities(), 'reviewed_topics': topic_capabilities(), 'annual_reports': annual_capabilities(),
        'airports': {'parser_version': 'caac-east-airports-monthly-1', 'source_id': 'caac-east-monthly'}})
    result = {'status': 'exported', 'records': len(facts), 'candidates': len(candidates), 'version': __version__,
        'generated_at': now(), 'database_dialect': store.engine.dialect.name,
        'fact_filter': {'start': args.facts_start, 'end': args.facts_end, 'semantics': 'interval_overlap_no_period_clipping'},
        'coverage_window': {'start': args.start, 'end': args.end}, 'data_acceptance_complete': mat['all_targets_closed'],
        'audit_window': mat['window'],
        'quality_status': quality['status'], 'package_scope': 'independent_data_only'}
    write_json(output / 'manifest.json', result)
    return result


def parser():
    root = argparse.ArgumentParser(description='独立安徽公开数据采集器；不连接团队 C/D/E 服务')
    root.add_argument('--version', action='version', version=__version__)
    root.add_argument('--data-root', type=Path, default=Path('data'))
    root.add_argument('--registry', type=Path)
    sub = root.add_subparsers(dest='command', required=True)
    sub.add_parser('init', help='初始化独立库；已有旧库必须显式migrate')
    sub.add_parser('status')
    ingest = sub.add_parser('ingest', help='可携带原件清单离线回补')
    ingest.add_argument('manifest', type=Path)
    ingest.add_argument('--accept-revisions', action='store_true')
    old = sub.add_parser('crawl-airports', help='兼容旧机场发现命令')
    old.add_argument('--start', default='2023-01')
    old.add_argument('--end', required=True)
    old.add_argument('--pages', type=int, default=1)
    old.add_argument('--max-reports', type=int, default=2)
    for name in ('crawl', 'update'):
        crawl = sub.add_parser(name, help='持久化发现和增量更新；预算耗尽保留续采队列')
        crawl.add_argument('--start', default='2023-01-01')
        crawl.add_argument('--end')
        crawl.add_argument('--source-id', action='append')
        crawl.add_argument('--kind', choices=['city', 'airport', 'scenic', 'rail_station'])
        crawl.add_argument('--max-pages', type=int, default=20)
        crawl.add_argument('--max-documents', type=int, default=50)
        if name == 'update':
            crawl.add_argument('--due', action='store_true', help='仅检查到期来源；update默认即此行为')
            crawl.add_argument('--as-of', help='测试用调度时钟；报告明确标记override')
    export = sub.add_parser('export', help='事实、候选、覆盖和质量报告；不覆盖已有导出')
    export.add_argument('--output', type=Path, required=True)
    export.add_argument('--start', default='2023-01', help='仅控制覆盖报告，兼容旧版')
    export.add_argument('--end', required=True)
    export.add_argument('--facts-start')
    export.add_argument('--facts-end')
    migration = sub.add_parser('migrate', help='显式备份后升级独立库及来源配置')
    migration.add_argument('--backup-dir', type=Path)
    back = sub.add_parser('backup', help='内部恢复包包含原文，勿公开发布')
    back.add_argument('--output', type=Path, required=True)
    rest = sub.add_parser('restore', help='恢复到全新空库及原件目录')
    rest.add_argument('snapshot', type=Path)
    verification = sub.add_parser('verify', help='逐原件及事实复核，完整性与数据验收分别报告')
    verification.add_argument('--start', default='2023-01-01')
    verification.add_argument('--end')
    verification.add_argument('--final', action='store_true', help='执行双端、调度及数据完整性正式放行门禁')
    verification.add_argument('--wheel', type=Path)
    verification.add_argument('--mac-receipt', type=Path)
    verification.add_argument('--windows-receipt', type=Path)
    verification.add_argument('--scheduler-receipts', type=Path)
    audit = sub.add_parser('audit-import', help='导入经审阅的对象指标检索证据，不自动关闭缺口')
    audit.add_argument('file', type=Path)
    schedule = sub.add_parser('schedule', help='生成Mac/Windows任务配置；安装与验收另行显式执行')
    schedule.add_argument('--output', type=Path, required=True)
    schedule.add_argument('--python', type=Path)
    for name in ('ingest', 'crawl-airports', 'crawl', 'update', 'migrate', 'verify', 'backup', 'restore', 'audit-import'):
        sub.choices[name].add_argument('--report', type=Path)
    return root


def execute(store, registry, args):
    if args.command == 'migrate':
        return migrate(store, registry, args.backup_dir or store.root / 'backups')
    if args.command == 'restore':
        return restore(store, args.snapshot)
    store.initialize()
    store.seed(registry)
    if args.command in {'init', 'status'}:
        return {'status': 'ready', 'version': __version__, 'scope': 'independent_crawler', 'database_dialect': store.engine.dialect.name,
                'counts': store.statistics(), 'team_database_touched': False}
    if args.command == 'ingest':
        return ingest_seed(store, registry, args.manifest, accept_revisions=args.accept_revisions, progress=progress)
    if args.command == 'crawl-airports':
        discovered, seed = discover_caac(store, registry, start=args.start, end=args.end, pages=args.pages, max_reports=args.max_reports, progress=progress)
        ingested = ingest_seed(store, registry, seed, progress=progress) if discovered['references'] else None
        return {'status': 'failed' if ingested is None else 'partial' if discovered['failures'] else ingested['status'], 'discovery': discovered, 'ingestion': ingested}
    if args.command in {'crawl', 'update'}:
        selected = set(args.source_id or [])
        if args.kind:
            typed = {s for e in registry['entities'] if e['type'] == args.kind for s in e['source_ids']}
            if selected and not selected & typed:
                raise CrawlerError('Source selection and object kind do not overlap')
            selected = selected & typed if selected else typed
        return update(store, registry, start=args.start, end=args.end, due=args.command == 'update', source_ids=selected or None,
                      as_of=getattr(args, 'as_of', None), max_pages=args.max_pages, max_documents=args.max_documents, progress=progress)
    if args.command == 'export':
        return export_data(store, registry, args)
    if args.command == 'verify':
        result = verify(store, registry, start=args.start, end=args.end)
        if args.final:
            from .gate import final_gate
            result['final_gate'] = final_gate(result, wheel=args.wheel, mac_receipt=args.mac_receipt,
                windows_receipt=args.windows_receipt, schedules=args.scheduler_receipts)
            result['status'] = result['final_gate']['status']
        return result
    if args.command == 'backup':
        return backup(store, args.output)
    if args.command == 'schedule':
        return generate_schedule(store.root, args.output, args.python)
    if args.command == 'audit-import':
        load_audit(store, registry, args.file)
        original = store.root / 'audit.json'
        if original.exists():
            from .common import key_for
            write_json(store.root / 'audits' / (key_for(now()) + '.json'), json.loads(original.read_text(encoding='utf-8')))
        write_json(original, json.loads(args.file.read_text(encoding='utf-8')))
        return {'status': 'imported', 'entries': len(load_audit(store, registry))}
    raise CrawlerError('Unsupported command')


def main(argv=None):
    args = parser().parse_args(argv)
    store = None
    try:
        root = args.data_root.resolve()
        url = os.environ.get('CRAWLER_DATABASE_URL') or 'sqlite+pysqlite:///' + str(root / 'crawler.sqlite3')
        registry = load_registry(args.registry)
        store = Store(url, root)
        with run_lock(store):
            result = execute(store, registry, args)
            if getattr(args, 'report', None):
                write_json(args.report, result)
        summary = result if args.command in {'init', 'status', 'export', 'migrate', 'backup', 'restore'} else {
            k: result[k] for k in ('status', 'run_id', 'records', 'assets_processed', 'assets_failed', 'data_acceptance_complete', 'facts_verified', 'originals_verified', 'entries') if k in result}
        print(json.dumps(summary, ensure_ascii=False))
        return 0 if result['status'] in {'ready', 'succeeded', 'exported', 'migrated', 'up_to_date', 'backed_up', 'restored', 'passed', 'imported', 'generated'} else 2
    except AlreadyRunning as exc:
        print(json.dumps({'status': 'already_running', 'message': str(exc)}))
        return 3
    except Exception as exc:
        message = str(exc) if isinstance(exc, CrawlerError) else 'Command failed; check independent crawler inputs/configuration'
        print(json.dumps({'status': 'failed', 'error_type': type(exc).__name__, 'message': message}, ensure_ascii=False), file=sys.stderr)
        return 1
    finally:
        if store is not None:
            store.dispose()
