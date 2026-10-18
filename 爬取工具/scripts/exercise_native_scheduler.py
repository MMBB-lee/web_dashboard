"""Exercise a temporary native task: injected failure -> live recovery -> due skip.

The task uses an isolated SQLite directory and an installed wheel. No production
schedule or source files are changed. Removes only its own task after evidence.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import plistlib
import subprocess
import sys
import time

from sqlalchemy.orm import Session

from anhui_crawler.common import CrawlerError, now, sha256, write_json
from anhui_crawler.models import SourceState
from anhui_crawler.pipeline import load_registry
from anhui_crawler.storage import Store
from anhui_crawler.updating import state, save_state


def run(command):
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise CrawlerError('Native task operation failed: ' + command[0])
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheel', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if platform.system() not in {'Darwin', 'Windows'}:
        parser.error('Native Mac/Windows required')
    if args.output.exists() and any(args.output.iterdir()):
        parser.error('Use a new/empty output directory')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    root = output / 'controlled-data'
    url = 'sqlite+pysqlite:///' + str(root / 'crawler.sqlite3')
    store = Store(url, root)
    store.initialize()
    store.seed(load_registry())
    source_id = 'scenic-wanfohu'
    end = datetime.now(timezone.utc).astimezone(__import__('datetime').timezone(__import__('datetime').timedelta(hours=8))).date().isoformat()
    # Explicitly synthetic failure; it must be refused before any network request.
    value = {'source_id': source_id, 'last_checked': None, 'last_success': None, 'next_due': '2000-01-01T00:00:00+00:00',
             'status': 'partial', 'details': {'window': {'start': '2023-01-01', 'end': end}, 'pending': [
                 {'url': 'https://not-allowlisted.invalid/controlled-failure', 'kind': 'document'}], 'completed': []}}
    save_state(store, value)
    label = 'org.anhui.public-data-crawler.acceptance'
    task_name = 'AnhuiPublicDataCrawlerAcceptance'
    # The wrapper only fixes an isolated DB environment; it invokes the actual
    # installed production CLI without a clock override or synthetic fetcher.
    wrapper = output / 'native_entry.py'
    wrapper.write_text("import os\nfrom anhui_crawler.cli import main\nos.environ['CRAWLER_DATABASE_URL']=" + repr(url) +
        "\nraise SystemExit(main(" + repr(['--data-root', str(root), 'update', '--due', '--source-id', source_id, '--end', end]) + "))\n", encoding='utf-8')
    arguments = [sys.executable, '-I', str(wrapper)]
    report = {'status': 'failed', 'platform': platform.system(), 'wheel_sha256': sha256(args.wheel.read_bytes()),
              'native_task_query_succeeded': False, 'trigger_history_reviewed': False, 'runs': [],
              'test_scope': 'isolated_database_injected_allowlist_failure_then_live_source_recovery',
              'natural_daily_intervals_tested': False, 'scheduled_task_removed': False, 'started_at': now()}
    installed = False
    try:
        if platform.system() == 'Darwin':
            job = {'Label': label, 'ProgramArguments': arguments, 'WorkingDirectory': str(output),
                   'RunAtLoad': False, 'StartCalendarInterval': {'Hour': 9, 'Minute': 0},
                   'StandardOutPath': str(output / 'stdout.log'), 'StandardErrorPath': str(output / 'stderr.log')}
            jobfile = output / 'acceptance.plist'
            jobfile.write_bytes(plistlib.dumps(job))
            target = f'gui/{os.getuid()}/' + label
            run(['launchctl', 'bootstrap', f'gui/{os.getuid()}', str(jobfile)])
            trigger = ['launchctl', 'kickstart', target]
            query = ['launchctl', 'print', target]
            remove = ['launchctl', 'bootout', target]
        else:
            config = output / 'task.json'
            write_json(config, {'python': sys.executable, 'arguments': '-I "' + str(wrapper) + '"', 'directory': str(output)})
            installer = output / 'install.ps1'
            installer.write_text('''param([string]$Config)
$ErrorActionPreference='Stop'
$c=Get-Content -LiteralPath $Config -Raw | ConvertFrom-Json
$a=New-ScheduledTaskAction -Execute $c.python -Argument $c.arguments -WorkingDirectory $c.directory
$t=New-ScheduledTaskTrigger -Daily -At '09:00'
$p=New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$s=New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName 'AnhuiPublicDataCrawlerAcceptance' -InputObject (New-ScheduledTask -Action $a -Trigger $t -Principal $p -Settings $s) | Out-Null
''', encoding='utf-8-sig')
            run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(installer), '-Config', str(config)])
            prefix = ['powershell.exe', '-NoProfile', '-Command']
            trigger = prefix + ["Start-ScheduledTask -TaskName '" + task_name + "'"]
            query = prefix + ["$i=Get-ScheduledTaskInfo -TaskName '" + task_name + "'; [PSCustomObject]@{LastTaskResult=$i.LastTaskResult;LastRunTime=$i.LastRunTime}|ConvertTo-Json"]
            remove = prefix + ["Unregister-ScheduledTask -TaskName '" + task_name + "' -Confirm:$false"]
        installed = True
        for index in range(3):
            if index == 1:
                restored_state = state(store, source_id)
                restored_state['details']['pending'] = [{'url': next(s['url'] for s in load_registry()['sources'] if s['id'] == source_id), 'kind': 'document'}]
                restored_state['next_due'] = '2000-01-01T00:00:00+00:00'
                save_state(store, restored_state)
            before = set((root / 'runs').glob('*.json')) if (root / 'runs').exists() else set()
            triggered_at = now()
            run(trigger)
            deadline = time.monotonic() + 180
            artifact = None
            while time.monotonic() < deadline:
                new = set((root / 'runs').glob('*.json')) - before if (root / 'runs').exists() else set()
                if new:
                    artifact = sorted(new)[-1]
                    break
                time.sleep(1)
            if artifact is None:
                raise CrawlerError('Native task did not produce an update artifact within 180 seconds')
            # Wait for process completion before querying the task or triggering again.
            for _ in range(30):
                text = run(query)
                if platform.system() == 'Darwin':
                    if not any(line.strip().startswith('pid =') for line in text.splitlines()):
                        break
                else:
                    if '267009' not in text:  # SCHED_S_TASK_RUNNING
                        break
                time.sleep(1)
            raw = artifact.read_bytes()
            result = json.loads(raw)
            if result['status'] != ('partial' if index == 0 else 'succeeded'):
                raise CrawlerError('Native controlled failure/recovery did not match expected result')
            native_query = '\n'.join(line.strip() for line in text.splitlines() if platform.system() == 'Windows' or
                any(line.strip().startswith(key) for key in ('state =', 'runs =', 'last exit code =', 'pid =', 'program =')))
            report['runs'].append({'file': artifact.relative_to(output).as_posix(), 'sha256': sha256(raw),
                'status': result['status'], 'clock_override': result['clock_override'], 'triggered_at': triggered_at,
                'started_at': result['started_at'], 'finished_at': result['finished_at'],
                'trigger': trigger, 'native_query': native_query})
        report.update(status='passed', native_task_query_succeeded=True, trigger_history_reviewed=True)
    except Exception as exc:
        report.update(error_type=type(exc).__name__, reason=str(exc) if isinstance(exc, CrawlerError) else 'Native scheduler exercise failed')
    finally:
        if installed:
            try:
                run(remove)
                report['scheduled_task_removed'] = True
            except Exception:
                report['cleanup_required'] = label if platform.system() == 'Darwin' else task_name
        store.dispose()
        report['finished_at'] = now()
        write_json(output / (platform.system() + '.json'), report)
    print(json.dumps({k: report[k] for k in ('status', 'platform', 'scheduled_task_removed')}))
    return 0 if report['status'] == 'passed' and report['scheduled_task_removed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
