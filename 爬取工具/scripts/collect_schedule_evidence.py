"""Collect native scheduler state and reference actual update-run artifacts.

Pass at least three chronological runs: a failure followed by two successes.
This reads existing artifacts; it cannot turn manual runs into scheduler evidence.
Keep the native trigger history alongside the collected receipt for review.
"""
import argparse
import json
import os
from pathlib import Path
import platform
import re
import subprocess

from anhui_crawler.common import CrawlerError, now, sha256, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheel', type=Path, required=True)
    parser.add_argument('--run-report', type=Path, action='append', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--label', default='org.anhui.public-data-crawler')
    parser.add_argument('--trigger-history', type=Path, help='Reviewed native trigger records with run SHA and native event reference')
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Receipt already exists; use a new path')
    report = {'status': 'failed', 'platform': platform.system(), 'collected_at': now(),
              'wheel_sha256': sha256(args.wheel.read_bytes()), 'native_task_query_succeeded': False, 'runs': [],
              'trigger_history_requires_review': True}
    try:
        if platform.system() == 'Darwin':
            if not re.fullmatch(r'[A-Za-z0-9.-]+', args.label):
                raise CrawlerError('Invalid task label')
            command = ['launchctl', 'print', f'gui/{os.getuid()}/' + args.label]
        elif platform.system() == 'Windows':
            command = ['powershell.exe', '-NoProfile', '-Command',
                "$t=Get-ScheduledTask -TaskName 'AnhuiPublicDataCrawler'; $i=Get-ScheduledTaskInfo -TaskName 'AnhuiPublicDataCrawler'; "
                "[PSCustomObject]@{TaskName=$t.TaskName;State=[string]$t.State;LastTaskResult=$i.LastTaskResult;LastRunTime=$i.LastRunTime;NextRunTime=$i.NextRunTime}|ConvertTo-Json"]
        else:
            raise CrawlerError('Native Mac or Windows scheduler required')
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise CrawlerError('Native task query failed')
        # Never store the whole launchd environment (it may contain credentials).
        evidence = '\n'.join(line.strip() for line in result.stdout.splitlines() if
            platform.system() == 'Windows' or any(line.strip().startswith(key) for key in ('state =', 'runs =', 'last exit code =', 'pid =', 'program =')))
        report.update(native_task_query_succeeded=True, native_query=evidence)
        times = []
        for path in args.run_report:
            raw = path.read_bytes()
            run = json.loads(raw)
            if 'sources' not in run or run.get('clock_override') or not run.get('started_at') or not run.get('finished_at'):
                raise CrawlerError('Need actual unmodified update reports, not clock override runs')
            times.append(run['started_at'])
            report['runs'].append({'file': path.name, 'sha256': sha256(raw), 'status': run['status'],
                                   'started_at': run['started_at'], 'finished_at': run['finished_at'], 'clock_override': False})
        from anhui_crawler.updating import instant
        if times != sorted(times, key=instant) or len(set(times)) != len(times):
            raise CrawlerError('Run timestamps must be distinct and chronological')
        runs = report['runs']
        if len(runs) < 3 or not any(r['status'] in {'partial', 'failed'} for r in runs[:-2]) or not all(r['status'] == 'succeeded' for r in runs[-2:]):
            raise CrawlerError('Need failure followed by two successful scheduled runs')
        if not args.trigger_history:
            raise CrawlerError('Native trigger history review is required; a task query alone does not prove these runs were scheduled')
        history = json.loads(args.trigger_history.read_text(encoding='utf-8'))
        if history.get('platform') != platform.system() or history.get('reviewed') is not True:
            raise CrawlerError('Trigger history has not been reviewed on this platform')
        events = history.get('events', [])
        if not all(any(e.get('run_sha256') == r['sha256'] and e.get('native_event_reference') for e in events) for r in runs):
            raise CrawlerError('Each update report requires a corresponding native trigger event')
        report['trigger_history_reviewed'] = True
        report['status'] = 'passed'
    except Exception as exc:
        report.update(error_type=type(exc).__name__, reason=str(exc) if isinstance(exc, CrawlerError) else 'Native schedule collection failed')
    write_json(args.output, report)
    print(json.dumps({k: report[k] for k in ('status', 'platform', 'native_task_query_succeeded')}))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
