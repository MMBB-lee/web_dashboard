"""Prepare review targets with actual registered-source evidence; closes nothing."""
import argparse
import json
import os
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from anhui_crawler.common import now, write_json
from anhui_crawler.locking import run_lock
from anhui_crawler.models import FetchState, Asset
from anhui_crawler.pipeline import load_registry
from anhui_crawler.storage import Store
from anhui_crawler.updating import state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--start', default='2023-01-01')
    parser.add_argument('--end', required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Review template already exists; it is never overwritten')
    registry = load_registry()
    store = Store(os.environ.get('CRAWLER_DATABASE_URL') or 'sqlite+pysqlite:///' + str(args.data_root.resolve() / 'crawler.sqlite3'), args.data_root)
    try:
        with run_lock(store):
            store.initialize()
            sources = {s['id']: s for s in registry['sources']}
            entries = []
            with Session(store.engine) as session:
                for entity in registry['entities']:
                    evidence = []
                    due = []
                    for sid in entity['source_ids']:
                        source = sources[sid]
                        current = state(store, sid)
                        if current['next_due']:
                            due.append(current['next_due'])
                        request = session.scalar(select(FetchState).where(FetchState.source_id == sid, FetchState.url == source['url']))
                        if request:
                            evidence.append({'url': request.url, 'checked_at': request.checked_at, 'route': 'registered_source_online_check',
                                'outcome': request.details.get('error') or request.status, 'source_id': sid,
                                'source_sha256': request.details.get('sha256')})
                        else:
                            asset = session.scalar(select(Asset).where(Asset.source_id == sid).order_by(Asset.retrieved_at.desc()))
                            if asset:
                                evidence.append({'url': asset.url, 'checked_at': asset.retrieved_at,
                                    'route': 'archived_original_not_current_availability', 'outcome': 'archived_bytes_available',
                                    'source_id': sid, 'source_sha256': asset.sha256})
                    for metric in entity['target_metrics']:
                        entries.append({'entity_id': entity['id'], 'target_metric': metric, 'start': args.start, 'end': args.end,
                            'status': 'pending', 'evidence': evidence, 'next_check': min(due) if due else None,
                            'discovered_documents_exhausted': False,
                            'review_note': 'Verify yearly/native-period coverage and alternative official/search routes before changing status.'})
            write_json(args.output, {'schema_version': 'anhui-crawler-audit-1', 'prepared_at': now(), 'entries': entries})
            print(json.dumps({'status': 'prepared', 'entities': len(registry['entities']), 'targets': len(entries), 'closed': 0}))
    finally:
        store.dispose()


if __name__ == '__main__':
    main()
