"""Direct-folder playback: index lifecycle, file integrity, authentication and HTTP Range."""
from pathlib import Path
import json
import sqlalchemy as sa
import pytest
from data_foundation import create_store
from data_foundation.db import Base
from data_foundation.errors import Conflict, InvalidArgument
from data_foundation.ingestion import local_videos as local
from app.services.c_store import CStore
from app.core.config import Settings

@pytest.fixture
def directory_store(tmp_path, monkeypatch):
    root=tmp_path/'videos';root.mkdir()
    native=create_store('sqlite+pysqlite:///'+(tmp_path/'test.sqlite').as_posix(),data_root=tmp_path/'data',allow_sqlite_for_tests=True)
    Base.metadata.create_all(native.engine)
    monkeypatch.setattr(local,'duration',lambda path:17.16)
    settings=Settings(video_storage_dir=str(root),data_dir=str(tmp_path/'data'))
    monkeypatch.setattr('app.core.config.get_settings',lambda:settings)
    yield native,root
    native.dispose()

def test_directory_lifecycle_and_sidecar(directory_store):
    native,root=directory_store
    path=root/'录像.mp4';path.write_bytes(b'original test video')
    (root/'archive').mkdir();(root/'archive'/'ignored.mp4').write_bytes(b'ignored')
    (root/'archive.zip').write_bytes(b'ignored')
    assert local.sync_directory(native,root)['inserted']==1
    row=local.list_files(native)[0];original_id=row['id']
    assert row['duration_seconds']==17.16
    assert local.sync_directory(native,root)['unchanged']==1
    sidecar=path.with_suffix('.metadata.json')
    sidecar.write_text(json.dumps({'source_note':'测试片段来源'}),encoding='utf-8')
    assert local.sync_directory(native,root)['updated']==1
    assert local.list_files(native)[0]['source_note']=='测试片段来源'
    path.write_bytes(b'replacement video with different content')
    with pytest.raises(Conflict): local.playback_ref(native,root,original_id)
    assert local.sync_directory(native,root)['updated']==1
    assert local.list_files(native)[0]['id']==original_id
    assert Path(local.playback_ref(native,root,original_id)['path']).read_bytes()==path.read_bytes()
    path.unlink()
    assert local.sync_directory(native,root)['missing']==1
    assert local.list_files(native)==[]
    assert local.get_file(native,original_id)['status']=='missing'
    path.write_bytes(b'restored video')
    local.sync_directory(native,root)
    assert local.list_files(native)[0]['id']==original_id

def test_root_escape_is_rejected(directory_store,tmp_path):
    native,root=directory_store
    (root/'clip.mp4').write_bytes(b'test')
    local.sync_directory(native,root);row=local.list_files(native)[0]
    (tmp_path/'outside.mp4').write_bytes(b'test')
    with native.write() as u:
        u.session.execute(local.table.update().where(local.table.c.id==row['id']).values(relative_path='../outside.mp4'))
    with pytest.raises(InvalidArgument): local.playback_ref(native,root,row['id'])

def test_authenticated_list_and_range(directory_store,client,monkeypatch):
    native,root=directory_store
    content=b'local folder test file for HTTP Range'
    (root/'测试录像.mp4').write_bytes(content)
    monkeypatch.setattr('app.api.v1.videos.get_c_store',lambda:CStore(native))
    assert client.get('/api/v1/videos').status_code==401
    client.post('/api/v1/auth/login',json={'username':'viewer1','password':'viewer-pass-123'})
    response=client.get('/api/v1/videos')
    assert response.status_code==200,response.text
    item=response.json()['data'][0]
    assert item['title']=='测试录像' and item['origin']=='local_folder'
    assert item['recorded_start_at'] is None and item['scenic_id'] is None
    assert 'path' not in item and 'relative_path' not in item
    response=client.get(f"/api/v1/videos/{item['id']}/playback",headers={'Range':'bytes=3-9'})
    assert response.status_code==206,response.text
    assert response.content==content[3:10]
    assert response.headers['content-range']==f'bytes 3-9/{len(content)}'
    assert 'no-store' in response.headers['cache-control']
