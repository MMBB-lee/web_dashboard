"""Video bytes use the configured videos root; metadata, digest and authorization still govern playback."""
from datetime import datetime,timedelta,timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import pytest
from data_foundation import create_store
from data_foundation.db import Base
from data_foundation.db.models import Source,Entity,PermissionRecord
from data_foundation.errors import InvalidPermission
from app.services.c_store import CStore

def test_backend_startup_creates_media_folders_and_keeps_existing_files(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app

    root=tmp_path/'新项目'/'videos'
    captures=root.parent/'cap_images'
    monkeypatch.setattr('app.main.get_settings',lambda:SimpleNamespace(debug=False,video_storage_dir=str(root)))
    monkeypatch.setattr('app.services.e_bindings.configure_e_services',lambda:None)
    assert not root.exists() and not captures.exists()
    with TestClient(app) as client:
        assert client.get('/api/v1/health/live').status_code==200
        assert root.is_dir() and captures.is_dir()
    (root/'existing.mp4').write_bytes(b'existing recording')
    (captures/'existing.jpg').write_bytes(b'existing capture')
    with TestClient(app):
        assert (root/'existing.mp4').read_bytes()==b'existing recording'
        assert (captures/'existing.jpg').read_bytes()==b'existing capture'


def test_new_video_root_playback_and_legacy_fallback(tmp_path,monkeypatch):
    root=tmp_path/'videos';data=tmp_path/'data'
    monkeypatch.setattr('app.core.config.get_settings',lambda:SimpleNamespace(video_storage_dir=str(root)))
    native=create_store('sqlite+pysqlite:///'+(tmp_path/'test.sqlite').as_posix(),data_root=data,allow_sqlite_for_tests=True)
    Base.metadata.create_all(native.engine);store=CStore(native)
    sid,scenic,camera,permission=[str(uuid4()) for _ in range(4)]
    now=datetime.now(timezone.utc)
    try:
        with store.write() as u:
            u.session.add(Source(id=sid,publisher='测试',title='单元测试视频',url='https://example.org/test',usage_note='单元测试',
                verification_status='approved',update_frequency='manual',adapter_key='test',enabled=False))
            u.session.add(Entity(id=scenic,name='单元测试地点',type='scenic',region_code='340000',status='active'))
            u.session.flush()
            u.session.add(Entity(id=camera,name='单元测试摄像头',type='camera',region_code='340000',status='active',parent_entity_id=scenic))
            u.session.add(PermissionRecord(id=permission,source_id=sid,basis='单元测试授权',grantor='测试',evidence_path='test.txt',
                evidence_sha256='0'*64,allowed_uses=['analysis','replay'],valid_from=(now-timedelta(days=1)).replace(tzinfo=None),
                playback_allowed=True,status='approved'))
        content=b'unit test bytes, not production footage'
        stored=store.persist_video_file(source_id=sid,original_name='unit.mp4',stream=BytesIO(content))
        assert (root/stored['relative_path']).read_bytes()==content
        assert not (data/stored['relative_path']).exists()
        video=store.register_uploaded_video(source_id=sid,scenic_id=scenic,camera_id=camera,permission_record_id=permission,
            recorded_start_at=now-timedelta(seconds=5),recorded_end_at=now,camera_location_note='单元测试',
            relative_path=stored['relative_path'],sha256=stored['sha256'],byte_size=stored['byte_size'],media_type='video/mp4',original_name='unit.mp4')
        assert Path(store.get_playback_ref(video['id'])['path']).is_relative_to(root)
        target=data/stored['relative_path'];target.parent.mkdir(parents=True,exist_ok=True)
        (root/stored['relative_path']).rename(target)
        assert Path(store.get_playback_ref(video['id'])['path'])==target
        with store.write() as u: u.session.get(PermissionRecord,permission).playback_allowed=False
        with pytest.raises(InvalidPermission): store.get_playback_ref(video['id'])
    finally:
        if hasattr(store,'_video_store'): store._video_store.dispose()
        native.dispose()
