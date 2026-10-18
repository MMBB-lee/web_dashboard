"""Formal acceptance is stricter than passing source integrity checks."""
import json
from pathlib import Path

from . import __version__
from .common import sha256


def final_gate(quality, *, wheel=None, mac_receipt=None, windows_receipt=None, schedules=None):
    blockers = []
    if quality['status'] != 'passed':
        blockers.append('original_or_fact_integrity_failed')
    if not quality['data_acceptance_complete']:
        blockers.append('object_metric_historical_audits_incomplete')
    digest = sha256(Path(wheel).read_bytes()) if wheel and Path(wheel).is_file() else None
    if not digest:
        blockers.append('formal_wheel_missing')
    receipts = {}
    for platform, path in [('Darwin', mac_receipt), ('Windows', windows_receipt)]:
        try:
            receipt = json.loads(Path(path).read_text(encoding='utf-8')) if path else {}
        except (ValueError, OSError):
            receipt = {}
        okay = (bool(digest) and receipt.get('status') == 'passed' and receipt.get('platform') == platform and
                receipt.get('crawler_version') == __version__ and receipt.get('wheel_sha256') == digest and
                receipt.get('sqlite') == receipt.get('mysql') == receipt.get('restore') == 'passed')
        receipts[platform] = okay
        if not okay:
            blockers.append('native_package_or_database_receipt_missing_or_invalid:' + platform)
    schedule_results = {}
    for platform in ('Darwin', 'Windows'):
        path = Path(schedules) / (platform + '.json') if schedules else None
        try:
            receipt = json.loads(path.read_text(encoding='utf-8')) if path else {}
        except (ValueError, OSError):
            receipt = {}
        runs = receipt.get('runs', [])
        okay = (bool(digest) and receipt.get('status') == 'passed' and receipt.get('platform') == platform and
                receipt.get('wheel_sha256') == digest and receipt.get('native_task_query_succeeded') is True and
                receipt.get('trigger_history_reviewed') is True and
                len(runs) >= 3 and len({r.get('sha256') for r in runs}) == len(runs) and
                any(r.get('status') in {'failed', 'partial'} for r in runs[:-2]) and
                all(r.get('status') == 'succeeded' and not r.get('clock_override') for r in runs[-2:]))
        schedule_results[platform] = okay
        if not okay:
            blockers.append('native_scheduler_trigger_and_recovery_receipt_missing_or_invalid:' + platform)
    return {'status': 'passed' if not blockers else 'incomplete', 'full_acceptance': not blockers,
            'blockers': blockers, 'native_packages': receipts, 'native_schedulers': schedule_results,
            'wheel_sha256': digest, 'crawler_version': __version__}
