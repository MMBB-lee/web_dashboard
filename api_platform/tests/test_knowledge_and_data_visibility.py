"""Exercise actual C storage and E indexing; no production database or API key."""
from pathlib import Path

import pytest
from data_foundation import create_store
from data_foundation.db import Base
from data_foundation.ingestion.public_bridge import load_records, apply_import
from app.services.c_store import CStore
from app.services import data_answers, public_data, e_bindings
from app.worker import DWorker


@pytest.fixture
def storage(tmp_path, monkeypatch):
    native = create_store('sqlite+pysqlite:///' + (tmp_path / 'test.sqlite3').as_posix(),
                          data_root=tmp_path, allow_sqlite_for_tests=True)
    Base.metadata.create_all(native.engine)
    project = Path(__file__).resolve().parents[2]
    records = load_records(project / '爬取工具/data/local', project / 'data_foundation/real_data/2026-10-12-v2/manifest.json')
    from data_foundation.ingestion.real_dataset import import_real_dataset
    import_real_dataset(native, project / 'data_foundation/real_data/2026-10-12-v2/manifest.json')
    apply_import(native, records)
    store = CStore(native)
    for name in ['app.core.providers.get_c_store', 'app.core.providers.get_job_store',
                 'app.services.public_data.get_c_store', 'app.services.data_answers.get_c_store',
                 'app.services.e_bridge.get_c_store', 'app.api.v1.knowledge.get_job_store']:
        monkeypatch.setattr(name, lambda: store)
    yield store
    e_bindings.reset_for_tests()
    native.dispose()


def test_public_original_grains_and_candidates_are_separate(storage):
    with storage.read() as u:
        entities = u.catalog.list_entities()['items']
    huangshan = next(e for e in entities if e['name'] == '黄山风景区主景区')
    visible = public_data.entity_data(huangshan['id'])
    assert any(f['grain'] == 'year' for f in visible['facts'])
    assert all(f['quality_status'] == 'verified' for f in visible['facts'])
    assert any(f['quality_status'] != 'verified' for f in public_data.entity_data(huangshan['id'], True)['facts'])


def test_plain_question_resolves_alias_latest_and_never_uses_old_data_as_current(storage):
    answer = data_answers.answer_from_data('合肥机场最近的客流量', None, None)
    assert answer['status'] == 'answered'
    assert '2026-08-31' in answer['answer'] and '1,292,985' in answer['answer']
    assert answer['citations'][0]['url'].startswith('https://')
    answer = data_answers.answer_from_data('现在黄山景区有多少人', None, None)
    assert answer['status'] == 'insufficient_evidence' and not answer['citations']


def test_greeting_and_explicit_metric_paths():
    assert data_answers.answer_from_data('你好', None, None)['status'] == 'answered'
    assert data_answers.answer_from_data('合肥机场最近客流量', None, {'metric': 'airport_throughput'}) is None


def test_admin_upload_worker_and_verified_snapshot(storage, client, tmp_path, monkeypatch):
    from app.core.config import Settings
    settings = Settings(data_dir=str(tmp_path), rag_index_path=str(tmp_path / 'rag.json'))
    monkeypatch.setattr('app.core.config.get_settings', lambda: settings)
    monkeypatch.setattr('app.services.e_bindings.get_settings', lambda: settings)
    e_bindings.reset_for_tests()
    assert e_bindings.configure_e_services()
    login = client.post('/api/v1/auth/login', json={'username': 'admin1', 'password': 'admin-pass-123'})
    csrf = login.json()['data']['csrf_token']
    content = '铁路发送量统计从车站出发的旅客人次；机场吞吐量统计进出港旅客人次。'.encode('utf-8')
    form = {'title': '指标口径说明', 'publisher': '测试单位', 'source_url': 'https://example.test/definitions',
            'published_at': '2026-10-18T00:00:00+08:00', 'permission_note': '仅供隔离测试'}
    forbidden = client.post('/api/v1/knowledge/documents', files={'file': ('definitions.txt', content)}, data=form)
    assert forbidden.status_code == 403
    accepted = client.post('/api/v1/knowledge/documents', files={'file': ('definitions.txt', content)},
                           data=form, headers={'X-CSRF-Token': csrf})
    assert accepted.status_code == 202, accepted.text
    assert DWorker(storage).run_once()['status'] == 'succeeded'
    doc_id = accepted.json()['data']['document_id']
    found = client.get('/api/v1/knowledge/documents/' + doc_id)
    assert found.status_code == 200 and found.json()['data']['index_status'] == 'ready'
    assert found.json()['data']['allowed_snippets']
    snapshot = e_bindings.knowledge_snapshot_loader()()
    assert doc_id in snapshot['documents']
    listed = client.get('/api/v1/knowledge/documents').json()['data']
    assert any(d['id'] == doc_id and d['index_status'] == 'ready' for d in listed)


def test_registered_html_ignores_scripts_and_keeps_article(tmp_path):
    from app.services.knowledge_input import read_document,prepared_source
    file = tmp_path / 'notice.html'
    file.write_text('<h1>公告</h1><script>fake text</script><style>fake CSS</style><p>客流 123 人次</p>', encoding='utf-8')
    result = read_document(file)
    assert '公告' in result and '客流 123 人次' in result and 'fake' not in result
    original=file.read_bytes()
    from intelligent_analysis.knowledge.parse import read_document as upstream_read
    with prepared_source(file,tmp_path/'data') as path:
        prepared=Path(path)
        assert upstream_read(prepared)==result
    assert not prepared.exists()
    assert file.read_bytes()==original
