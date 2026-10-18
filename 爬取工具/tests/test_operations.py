from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from anhui_crawler.cli import main
from anhui_crawler.common import CrawlerError, sha256, write_json
from anhui_crawler.locking import AlreadyRunning, run_lock
from anhui_crawler.maintenance import backup, migrate, restore, schema_version
from anhui_crawler.models import Base, Meta, SourceState
from anhui_crawler.network import DeferredRequest, Response
from anhui_crawler.pipeline import load_registry
from anhui_crawler.pipeline import airport_pdf_links
from anhui_crawler.reporting import matrix, verify
from anhui_crawler.storage import Store
from anhui_crawler.topics import parse_topic
from anhui_crawler.updating import cached_fetch, crawl_source, next_check, state, update


BODY = '''<html><head><title>舒城“五一”文旅热力值拉满</title><meta name="PubDate" content="2025-05-06 11:31"></head>
<body><div id="J_content"><p>全县累计接待游客80.5万人次。万佛湖景区假期五天累计接待游客11.19万人次，综合收入1220.36万元。</p></div></body></html>'''.encode()


@pytest.fixture
def store(tmp_path):
    result = Store('sqlite+pysqlite:///' + str(tmp_path / 'local' / 'crawler.sqlite3'), tmp_path / 'local')
    result.initialize()
    result.seed(load_registry())
    yield result
    result.dispose()


def source():
    return next(s for s in load_registry()['sources'] if s['id'] == 'scenic-wanfohu')


class FakeFetcher:
    robots = {}

    def __init__(self, body=BODY, status=200, failure=None):
        self.body, self.status, self.failure, self.calls = body, status, failure, []

    def fetch(self, url, **headers):
        self.calls.append((url, headers))
        if self.failure:
            raise self.failure
        return Response(url, url, self.body if self.status != 304 else b'', 'text/html', self.status,
                        '2026-10-16T00:00:00+00:00', '"sample-v1"', 'Tue, 06 May 2025 03:31:00 GMT')


def test_topic_selects_scenic_not_county_and_preserves_native_period():
    parsed = parse_topic(BODY, source()['id'], load_registry()['entities'])
    row = parsed['records'][0]
    assert row['value'] == 111900 and row['grain'] == 'holiday'
    assert row['period_start'] == date(2025, 5, 1) and row['period_end'] == date(2025, 5, 5)


@pytest.mark.parametrize('replace', [
    ('11.19万人次', '约11.19万人次'),
    ('11.19万人次', '11.19人次'),
    ('假期五天累计接待', '预计假期五天累计接待'),
    ('2025-05-06', '2024-05-06'),
    ('id="J_content"', 'id="changed"'),
])
def test_topic_format_unit_forecast_and_date_drift_fail_closed(replace):
    raw = BODY.decode().replace(*replace).encode()
    with pytest.raises(CrawlerError):
        parse_topic(raw, source()['id'], load_registry()['entities'])


def test_topic_duplicate_metric_is_not_selected_arbitrarily():
    raw = BODY.replace(b'</div>', b'<p>' + '万佛湖景区假期五天累计接待游客11.19万人次。'.encode() + b'</p></div>')
    with pytest.raises(CrawlerError):
        parse_topic(raw, source()['id'], load_registry()['entities'])


def test_run_lock_excludes_second_store_and_releases_after_exception(store):
    other = Store(str(store.engine.url), store.root)
    try:
        with pytest.raises(RuntimeError):
            with run_lock(store):
                with pytest.raises(AlreadyRunning):
                    with run_lock(other):
                        pytest.fail('Second process acquired lock')
                raise RuntimeError('injected')
        with run_lock(other):
            pass
    finally:
        other.dispose()


def test_explicit_schema_upgrade_preserves_facts_and_has_restorable_backup(store, tmp_path):
    crawl_source(store, load_registry(), source(), start='2023-01-01', end='2026-10-16', fetcher=FakeFetcher())
    original = store.export_facts()
    # Construct the exact old eight-table schema; it is not an unknown partial DB.
    with store.engine.begin() as connection:
        Base.metadata.tables['ac_fetch_state'].drop(connection)
        Base.metadata.tables['ac_source_state'].drop(connection)
        connection.execute(text("UPDATE ac_meta SET value='1' WHERE `key`='schema_version'"))
    with pytest.raises(CrawlerError):
        store.initialize()
    result = migrate(store, load_registry(), tmp_path / 'backups')
    assert result['status'] == 'migrated' and schema_version(store) == '2'
    assert store.export_facts() == original
    assert migrate(store, load_registry(), tmp_path / 'backups')['status'] == 'up_to_date'
    snapshot = next((tmp_path / 'backups').glob('*.zip'))
    target = Store('sqlite+pysqlite:///' + str(tmp_path / 'restored' / 'crawler.sqlite3'), tmp_path / 'restored')
    try:
        with run_lock(target):
            assert restore(target, snapshot)['schema_version'] == '1'
            migrate(target, load_registry(), tmp_path / 'restored-backups')
        assert target.export_facts() == original
        assert verify(target, load_registry())['status'] == 'passed'
    finally:
        target.dispose()


def test_registry_change_requires_backed_up_migration(store, tmp_path):
    reg = load_registry()
    reg['sources'][0]['scope_note'] += ' reviewed'
    with pytest.raises(CrawlerError, match='migration'):
        store.seed(reg)
    result = migrate(store, reg, tmp_path / 'backups')
    assert result['status'] == 'migrated'
    store.seed(reg)
    reg['entities'][0]['type'] = 'airport'
    with pytest.raises(CrawlerError, match='retype'):
        migrate(store, reg, tmp_path / 'backups')


def test_backup_restore_rejects_corruption_and_nonempty_target(store, tmp_path):
    crawl_source(store, load_registry(), source(), start='2023-01-01', end='2026-10-16', fetcher=FakeFetcher())
    snapshot = tmp_path / 'backup.zip'
    backup(store, snapshot)
    with pytest.raises(CrawlerError):
        restore(store, snapshot)
    with ZipFile(snapshot) as archive:
        contents = {name: archive.read(name) for name in archive.namelist()}
    raw_name = next(name for name in contents if name.startswith('raw/'))
    contents[raw_name] = b'CORRUPT'
    bad = tmp_path / 'bad.zip'
    with ZipFile(bad, 'w') as archive:
        for name, raw in contents.items():
            archive.writestr(name, raw)
    target = Store('sqlite+pysqlite:///' + str(tmp_path / 'new' / 'crawler.sqlite3'), tmp_path / 'new')
    try:
        with pytest.raises(CrawlerError, match='digest'):
            restore(target, bad)
        assert inspect(target.engine).get_table_names() == []
    finally:
        target.dispose()


def test_conditional_request_reuses_only_verified_cache(store):
    first = FakeFetcher()
    cached_fetch(store, source(), source()['url'], first)
    second = FakeFetcher(status=304)
    item, _, _, response = cached_fetch(store, source(), source()['url'], second)
    assert second.calls[0][1]['etag'] == '"sample-v1"'
    assert response.status == 304 and response.body == BODY
    assert item['sha256'] == sha256(BODY)


def test_missing_cache_forces_unconditional_get_and_rejects_invalid_304(store):
    _, _, path, _ = cached_fetch(store, source(), source()['url'], FakeFetcher())
    path.unlink()
    second = FakeFetcher(status=304)
    with pytest.raises(CrawlerError, match='304'):
        cached_fetch(store, source(), source()['url'], second)
    assert second.calls[0][1]['etag'] is None


def test_retry_after_persisted_across_fetcher_instances(store):
    later = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    with pytest.raises(DeferredRequest):
        cached_fetch(store, source(), source()['url'], FakeFetcher(failure=DeferredRequest('deferred', later)))
    replacement = FakeFetcher()
    with pytest.raises(DeferredRequest):
        cached_fetch(store, source(), source()['url'], replacement)
    assert replacement.calls == []


def test_incremental_run_repeat_and_due_check(store):
    reg = load_registry()
    first = update(store, reg, source_ids=[source()['id']], end='2026-10-16', due=False, fetcher_factory=lambda _: FakeFetcher())
    assert first['sources'][0]['records']['inserted'] == 1
    second = update(store, reg, source_ids=[source()['id']], end='2026-10-16', due=False, fetcher_factory=lambda _: FakeFetcher(status=304))
    assert second['sources'][0]['records']['inserted'] == 0
    assert second['sources'][0]['records']['skipped'] == 1
    third = update(store, reg, source_ids=[source()['id']], end='2026-10-16', fetcher_factory=lambda _: pytest.fail('not due'))
    assert third['sources'][0]['status'] == 'not_due'


def test_parse_failure_keeps_pending_url_and_recovers(store):
    reg = load_registry()
    bad = FakeFetcher(body=BODY.replace('11.19万人次'.encode(), '11.19亿元'.encode()))
    first = crawl_source(store, reg, source(), start='2023-01-01', end='2026-10-16', fetcher=bad)
    assert first['status'] == 'partial' and state(store, source()['id'])['details']['pending']
    second = crawl_source(store, reg, source(), start='2023-01-01', end='2026-10-16', fetcher=FakeFetcher())
    assert second['resumed'] and second['status'] == 'succeeded'
    assert second['records']['inserted'] == 1


def test_hard_interrupt_leaves_checkpoint_and_next_run_recovers(store):
    def interrupted(url, **kwargs):
        raise KeyboardInterrupt()
    fetcher = FakeFetcher()
    fetcher.fetch = interrupted
    with pytest.raises(KeyboardInterrupt):
        crawl_source(store, load_registry(), source(), start='2023-01-01', end='2026-10-16', fetcher=fetcher)
    assert state(store, source()['id'])['details']['pending'][0]['url'] == source()['url']
    result = update(store, load_registry(), source_ids=[source()['id']], end='2026-10-16', due=False, fetcher_factory=lambda _: FakeFetcher())
    assert result['sources'][0]['records']['inserted'] == 1


def test_one_failed_source_does_not_block_another(store):
    reg = load_registry()
    ids = ['city-bengbu', source()['id']]
    result = update(store, reg, source_ids=ids, end='2026-10-16', due=False,
        fetcher_factory=lambda s: FakeFetcher(failure=CrawlerError('restricted')) if s['id'] == 'city-bengbu' else FakeFetcher())
    assert result['status'] == 'partial'
    assert len(store.export_facts()) == 1


def test_matrix_does_not_call_one_fact_historical_completion(store):
    crawl_source(store, load_registry(), source(), start='2023-01-01', end='2026-10-16', fetcher=FakeFetcher())
    report = matrix(store, load_registry(), '2023-01-01', '2026-10-16')
    assert report['entities'] == 44 and report['targets'] == 108
    assert not report['all_targets_closed'] and report['closed_targets'] == 0
    assert verify(store, load_registry())['facts_verified'] == 1


def test_fact_export_filter_overlap_keeps_whole_holiday(store):
    crawl_source(store, load_registry(), source(), start='2023-01-01', end='2026-10-16', fetcher=FakeFetcher())
    assert len(store.export_facts(start=date(2025, 5, 3), end=date(2025, 5, 3))) == 1
    assert store.export_facts(start=date(2025, 6, 1)) == []
    assert store.export_facts(end=date(2025, 4, 30)) == []
    with pytest.raises(CrawlerError):
        store.export_facts(start=date(2025, 6, 1), end=date(2025, 5, 1))


def test_cli_fact_filters_do_not_reinterpret_old_coverage_parameters(store, tmp_path, monkeypatch):
    crawl_source(store, load_registry(), source(), start='2023-01-01', end='2026-10-16', fetcher=FakeFetcher())
    monkeypatch.delenv('CRAWLER_DATABASE_URL', raising=False)
    output = tmp_path / 'export'
    assert main(['--data-root', str(store.root), 'export', '--output', str(output), '--end', '2026-10', '--facts-start', '2025-06-01']) == 0
    assert json.loads((output / 'facts.json').read_text())['records'] == []
    assert (output / 'facts.csv').exists() and (output / 'candidates.csv').exists()
    assert not json.loads((output / 'manifest.json').read_text())['data_acceptance_complete']


def test_schedule_frequency_respects_native_source_kind():
    at = datetime(2026, 10, 16, tzinfo=timezone.utc)
    assert instant_delta(next_check({'id': 'city-wuhu'}, at), at) == 30
    assert instant_delta(next_check({'id': 'caac-east-monthly'}, at), at) == 7
    assert instant_delta(next_check({'id': 'scenic-x', 'holiday_windows': [{'start': '2026-10-01', 'end': '2026-10-07'}]}, at), at) == 1


def test_legacy_caac_attachment_name_does_not_select_province_totals():
    raw = ("<script>__$pubtime='2024-11-25 10:00:00';</script>"
           '<a href="rank.pdf">2024年10月月报排序.pdf</a>'
           '<a href="province.pdf">2024年10月月报分省排序.pdf</a>').encode()
    assert airport_pdf_links(raw, 'https://example.invalid/article.html')[0]['url'].endswith('/rank.pdf')


def test_backup_restores_audit_and_migration_control_files(store, tmp_path):
    write_json(store.root / 'audit.json', {'schema_version': 'anhui-crawler-audit-1', 'entries': []})
    write_json(store.root / 'migrations/previous.json', {'from': '1', 'to': '2'})
    snapshot = tmp_path / 'controls.zip'
    backup(store, snapshot)
    target = Store('sqlite+pysqlite:///' + str(tmp_path / 'control-restore/crawler.sqlite3'), tmp_path / 'control-restore')
    try:
        restore(target, snapshot)
        assert (target.root / 'audit.json').read_bytes() == (store.root / 'audit.json').read_bytes()
        assert (target.root / 'migrations/previous.json').exists()
    finally:
        target.dispose()


def test_discovery_budget_retains_queue_and_resumes_without_reprocessing_first(store):
    second_url = 'https://wlj.luan.gov.cn/second.html'
    first_body = BODY.replace(b'</body>', ('<a href="' + second_url + '">2025年万佛湖假期客流</a></body>').encode())
    first = crawl_source(store, load_registry(), source(), start='2023-01-01', end='2026-10-16', max_documents=1, fetcher=FakeFetcher(first_body))
    assert first['pending'] == 1 and not first['discovery_complete_in_configured_entries']
    next_fetcher = FakeFetcher()
    second = crawl_source(store, load_registry(), source(), start='2023-01-01', end='2026-10-16', fetcher=next_fetcher)
    assert second['status'] == 'succeeded' and second['resumed']
    assert [url for url, _ in next_fetcher.calls] == [second_url]
    assert len(store.export_facts()) == 1


def test_interrupt_after_fact_commit_replays_as_skip(store, monkeypatch):
    import anhui_crawler.updating as module
    save = module.save_state

    def interrupted_after_commit(store, value):
        if value['details']['completed']:
            raise KeyboardInterrupt()
        save(store, value)

    monkeypatch.setattr(module, 'save_state', interrupted_after_commit)
    with pytest.raises(KeyboardInterrupt):
        crawl_source(store, load_registry(), source(), start='2023-01-01', end='2026-10-16', fetcher=FakeFetcher())
    assert len(store.export_facts()) == 1
    monkeypatch.setattr(module, 'save_state', save)
    result = crawl_source(store, load_registry(), source(), start='2023-01-01', end='2026-10-16', fetcher=FakeFetcher(status=304))
    assert result['records']['inserted'] == 0 and result['records']['skipped'] == 1


def test_changed_live_value_is_quarantined(store):
    crawl_source(store, load_registry(), source(), start='2023-01-01', end='2026-10-16', fetcher=FakeFetcher())
    result = crawl_source(store, load_registry(), source(), start='2023-01-01', end='2026-10-16', fetcher=FakeFetcher(BODY.replace(b'11.19', b'12.19')))
    assert result['status'] == 'partial' and result['records']['quarantined'] == 1
    assert store.export_facts()[0]['value'] == '111900.0000'


def test_disabled_sources_are_not_automatically_fetched(store):
    result = update(store, load_registry(), source_ids=['city-bengbu'], fetcher_factory=lambda _: pytest.fail('disabled'))
    assert result['sources'][0]['status'] == 'disabled_requires_review'
    assert result['status'] == 'succeeded'


def test_unknown_schema_columns_refused_before_snapshot(store, tmp_path):
    with store.engine.begin() as connection:
        connection.execute(text('ALTER TABLE ac_facts ADD COLUMN foreign_column TEXT'))
    with pytest.raises(CrawlerError, match='columns'):
        migrate(store, load_registry(), tmp_path / 'should-not-exist')
    assert not (tmp_path / 'should-not-exist').exists()


def test_schedule_generation_handles_spaced_paths_without_installation(tmp_path):
    import plistlib
    from anhui_crawler.scheduling import generate
    result = generate(tmp_path / '中文 data', tmp_path / 'schedule', python=Path('/some python/bin/python'))
    job = plistlib.loads((tmp_path / 'schedule/crawler.plist').read_bytes())
    assert job['ProgramArguments'][0] == '/some python/bin/python'
    assert job['ProgramArguments'][5] == str((tmp_path / '中文 data').resolve())
    assert job['StartCalendarInterval'] == {'Hour': 9, 'Minute': 0}
    assert not result['installed']


def test_formal_gate_never_confuses_integrity_with_full_acceptance(tmp_path):
    from anhui_crawler.gate import final_gate
    result = final_gate({'status': 'passed', 'data_acceptance_complete': False})
    assert result['status'] == 'incomplete' and len(result['blockers']) == 6
    assert 'object_metric_historical_audits_incomplete' in result['blockers']


def test_formal_gate_requires_native_platform_digest_mysql_and_reviewed_scheduler(tmp_path):
    from anhui_crawler import __version__
    from anhui_crawler.gate import final_gate
    wheel = tmp_path / 'formal.whl'
    wheel.write_bytes(b'SYNTHETIC wheel digest fixture')
    receipt = {'status': 'passed', 'crawler_version': __version__, 'wheel_sha256': sha256(wheel.read_bytes()),
               'sqlite': 'passed', 'mysql': 'passed', 'restore': 'passed'}
    for system in ['Darwin', 'Windows']:
        write_json(tmp_path / (system + '-package.json'), {**receipt, 'platform': system})
        write_json(tmp_path / (system + '.json'), {'status': 'passed', 'platform': system,
            'wheel_sha256': receipt['wheel_sha256'], 'native_task_query_succeeded': True, 'trigger_history_reviewed': True,
            'runs': [{'sha256': str(i), 'status': 'partial' if i == 0 else 'succeeded', 'clock_override': False} for i in range(3)]})
    parameters = {'wheel': wheel, 'mac_receipt': tmp_path / 'Darwin-package.json',
        'windows_receipt': tmp_path / 'Windows-package.json', 'schedules': tmp_path}
    assert final_gate({'status': 'passed', 'data_acceptance_complete': True}, **parameters)['status'] == 'passed'
    receipt['mysql'] = 'pending'
    write_json(tmp_path / 'Windows-package.json', {**receipt, 'platform': 'Darwin'})
    assert not final_gate({'status': 'passed', 'data_acceptance_complete': True}, **parameters)['full_acceptance']


def test_recorded_external_restriction_can_close_availability_but_not_parser_failure(store):
    from anhui_crawler.updating import save_state
    evidence = [{'url': 'https://www.bengbu.gov.cn/', 'checked_at': '2026-10-14T08:00:00+00:00', 'route': 'official_catalog', 'outcome': 'restricted'},
                {'url': 'https://www.bengbu.gov.cn/', 'checked_at': '2026-10-16T08:00:00+00:00', 'route': 'alternative_official_route', 'outcome': 'restricted'}]
    write_json(store.root / 'audit.json', {'schema_version': 'anhui-crawler-audit-1', 'entries': [
        {'entity_id': 'city:bengbu', 'target_metric': 'city_transport_passengers', 'start': '2023-01-01', 'end': '2026-10-16',
         'status': 'access_restricted', 'evidence': evidence, 'next_check': '2026-10-23T08:00:00+00:00'}]})
    pending = [{'url': 'https://www.bengbu.gov.cn/', 'kind': 'document'}]
    value = {'source_id': 'city-bengbu', 'last_checked': None, 'last_success': None, 'next_due': None, 'status': 'partial',
             'details': {'pending': pending, 'review_required': [], 'failures': [{'url': pending[0]['url'], 'reason': 'robots.txt unavailable or restricted'}]}}
    save_state(store, value)
    row = lambda: next(r for r in matrix(store, load_registry(), '2023-01-01', '2026-10-16')['rows'] if r['entity_id'] == 'city:bengbu' and r['target_metric'] == 'city_transport_passengers')
    assert row()['audit_closed']
    value['details']['failures'][0]['reason'] = 'Expected one reviewed metric; parser drift'
    save_state(store, value)
    assert not row()['audit_closed']


def test_malformed_long_anchor_is_not_a_discovery_title():
    from anhui_crawler.updating import discover_links
    body = ('<a href="/menu.html">万佛湖假期' + 'navigation ' * 30 + '</a>').encode()
    assert not discover_links(body, source()['url'], source(), '2023-01-01', '2026-10-16', load_registry()['entities'])


def test_daily_cutoff_advance_keeps_pending_work_and_rechecks_entry(store):
    from anhui_crawler.updating import save_state
    other = 'https://wlj.luan.gov.cn/pending-report.html'
    save_state(store, {'source_id': source()['id'], 'status': 'partial', 'last_checked': None,
        'last_success': None, 'next_due': None, 'details': {'window': {'start': '2023-01-01', 'end': '2026-10-15'},
        'pending': [{'url': other, 'kind': 'document'}], 'completed': [source()['url']], 'review_required': []}})
    fetcher = FakeFetcher()
    result = crawl_source(store, load_registry(), source(), start='2023-01-01', end='2026-10-16', fetcher=fetcher)
    assert result['resumed'] and result['status'] == 'succeeded'
    assert [url for url, _ in fetcher.calls] == [source()['url'], other]


def instant_delta(value, at):
    return (datetime.fromisoformat(value) - at).days
