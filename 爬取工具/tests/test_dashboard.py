"""Read-only access and data semantics for the separately shipped local console."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest

from anhui_crawler.pipeline import load_registry
from anhui_crawler.storage import Store

spec = importlib.util.spec_from_file_location('crawler_dashboard', Path(__file__).resolve().parents[1] / 'scripts/dashboard.py')
dashboard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dashboard)


@pytest.fixture
def managed(tmp_path):
    store = Store('sqlite+pysqlite:///' + str(tmp_path / 'crawler.sqlite3'), tmp_path)
    store.initialize()
    store.seed(load_registry())
    store.dispose()
    return tmp_path


def test_missing_database_is_not_created(tmp_path):
    with pytest.raises(sqlite3.Error):
        dashboard.read_dashboard(tmp_path)
    assert not (tmp_path / 'crawler.sqlite3').exists()


def test_read_only_dashboard_does_not_change_database(managed):
    db = managed / 'crawler.sqlite3'
    digest = hashlib.sha256(db.read_bytes()).hexdigest()
    mtime = db.stat().st_mtime_ns
    data = dashboard.read_dashboard(managed)
    assert data['summary']['entities'] == 44
    assert data['summary']['sources'] == 34
    assert data['summary']['standard'] == data['summary']['candidates'] == 0
    assert data['database_mode'] == 'read_only'
    assert hashlib.sha256(db.read_bytes()).hexdigest() == digest
    assert db.stat().st_mtime_ns == mtime


def test_rejects_foreign_database(managed):
    with sqlite3.connect(managed / 'crawler.sqlite3') as conn:
        conn.execute('CREATE TABLE team_private (id TEXT)')
    with pytest.raises(ValueError, match='独立爬虫'):
        dashboard.read_dashboard(managed)


def test_incomplete_update_file_does_not_break_console(managed):
    folder = managed / 'runs'
    folder.mkdir()
    (folder / 'update-1.json').write_text(json.dumps({'status': 'partial', 'sources': []}))
    (folder / 'update-2.json').write_text('{')
    data = dashboard.read_dashboard(managed)
    assert data['latest_update']['status'] == 'partial'


def test_run_summary_projects_fields_without_private_paths():
    row = {'id': 'run1', 'kind': 'incremental_source', 'status': 'partial',
        'started_at': '2026-10-17T01:00:00Z', 'finished_at': None,
        'summary': json.dumps({'private_path': '/private/secret', 'database_url': 'mysql://secret',
            'items': [{'url': 'file:///private/secret', 'status': 'failed'}]})}
    view = dashboard.run_view(row)
    assert view['items'][0]['url'] is None
    assert 'private_path' not in view and 'database_url' not in view


@pytest.mark.parametrize('url', ['javascript:alert(1)', 'http://example.com', 'https://user:password@example.com', 'file:///tmp/a'])
def test_external_links_only_allow_public_https(url):
    assert dashboard.public_url(url) is None


def test_public_https_url_preserves_original_location():
    url = 'https://example.gov.cn/report.pdf'
    assert dashboard.public_url(url) == url
