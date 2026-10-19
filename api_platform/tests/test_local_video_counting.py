"""Exercise the button API, relative-time persistence, idempotency and actual E ROI point counting."""
from datetime import timedelta
from pathlib import Path
import numpy as np
import pytest
from data_foundation import create_store
from data_foundation.db import Base
from data_foundation.errors import Conflict
from data_foundation.ingestion import local_video_counts as db,local_videos
from app.core.config import Settings
from app.services.c_store import CStore
from app.services import counting_captures,local_counting

CONFIG=dict(count_model='p2pnet',weights_version='leader-SHTechA-50604773',
    roi={'points':[[0,0],[1,0],[1,1],[0,1]]},sample_interval_seconds=5,
    aggregation_minutes=5,inference_engine='pytorch',device='CPU')

class Counter:
    provenance=dict(count_model='p2pnet',weights_version='leader-SHTechA-50604773',
        weights_sha256='a'*64,model_source_revision='test',confidence_threshold=.5,inference_engine='pytorch',device='CPU')
    def validate_config(self,config): assert config['count_model']=='p2pnet'
    def __call__(self,frame):
        amount=int(frame[0,0,0])
        return np.array([[.2,.2,.9]]*amount,dtype=float).reshape(-1,3)

class Capture:
    def __init__(self,path): self.frame=0
    def isOpened(self): return True
    def get(self,key): return 25
    def set(self,key,value): self.frame=value;return True
    def read(self): return True,np.full((128,128,3),int(self.frame//125)*2,dtype=np.uint8)
    def release(self): pass

@pytest.fixture
def counting(tmp_path,monkeypatch):
    native=create_store('sqlite+pysqlite:///'+(tmp_path/'database.sqlite').as_posix(),data_root=tmp_path/'data',allow_sqlite_for_tests=True)
    Base.metadata.create_all(native.engine)
    root=tmp_path/'中文项目'/'videos';root.mkdir(parents=True);(root/'test.mp4').write_bytes(b'test video')
    settings=Settings(data_dir=str(tmp_path/'data'),video_storage_dir=str(root),capture_storage_dir=str(root.parent/'cap_images'))
    monkeypatch.setattr('app.core.config.get_settings',lambda:settings)
    monkeypatch.setattr(local_counting,'get_settings',lambda:settings)
    monkeypatch.setattr(counting_captures,'get_settings',lambda:settings)
    monkeypatch.setattr(local_videos,'duration',lambda path:17.16)
    local_videos.sync_directory(native,root)
    video=local_videos.list_files(native)[0];store=CStore(native)
    monkeypatch.setattr('app.api.v1.videos.get_c_store',lambda:store)
    monkeypatch.setattr('app.services.e_bindings.get_p2p_counter',lambda:Counter())
    monkeypatch.setattr(local_counting,'counter_for',lambda bundle:Counter())
    monkeypatch.setattr('cv2.VideoCapture',Capture)
    monkeypatch.setattr(local_counting,'schedule',lambda store,row:local_counting.run(store,row['id']) if row['status']=='queued' else None)
    yield store,video,root
    native.dispose()

def test_button_creates_reads_and_reuses_real_relative_counts(counting,client):
    store,video,root=counting;endpoint=f"/api/v1/videos/{video['id']}/counting"
    assert client.get(endpoint).status_code==401
    login=client.post('/api/v1/auth/login',json={'username':'viewer1','password':'viewer-pass-123'}).json()['data']
    assert client.post(endpoint,json=CONFIG,headers={'X-CSRF-Token':login['csrf_token']}).status_code==403
    token=client.post('/api/v1/auth/login',json={'username':'admin1','password':'admin-pass-123'}).json()['data']['csrf_token']
    assert client.get(endpoint).json()['data'] is None
    assert client.post(endpoint,json=CONFIG).status_code==403
    first=client.post(endpoint,json=CONFIG,headers={'X-CSRF-Token':token})
    assert first.status_code==202,first.text
    task=client.get(endpoint).json()['data'];assert task['status']=='ready' and task['progress']==100
    second=client.post(endpoint,json=CONFIG,headers={'X-CSRF-Token':token})
    assert second.json()['data']['analysis_id']==task['analysis_id']
    response=client.get(f"/api/v1/videos/{video['id']}/counts",params={'analysis_id':task['analysis_id'],'grain':'frame'})
    assert response.status_code==200,response.text
    data=response.json()['data']
    assert data['time_basis']=='relative' and data['count_model']=='p2pnet'
    assert [p['value'] for p in data['points']]==[0,2,4,6]
    assert [p['offset_seconds'] for p in data['points']]==[0,5,10,15]
    assert all(p['recorded_at'] is None for p in data['points'])
    assert data['manual_validation'] is None
    _,windows=db.result(store._store,video['id'],task['analysis_id'],'5min');assert windows==[]
    unknown=client.get(f"/api/v1/videos/{video['id']}/counts",params={'analysis_id':'unknown','grain':'frame'})
    assert unknown.status_code==404
    (root/'test.mp4').write_bytes(b'replaced video');local_videos.sync_directory(store._store,root)
    with pytest.raises(Conflict): db.result(store._store,video['id'],task['analysis_id'],'frame')

def test_windows_use_complete_duration_and_keep_fractional_median(counting):
    store,video,_=counting
    with store.write() as u:
        u.session.execute(local_videos.table.update().where(local_videos.table.c.id==video['id']).values(duration_seconds=617))
    video=local_videos.get_file(store._store,video['id'])
    config=CONFIG|{'sample_interval_seconds':10}
    row=db.request(store._store,video,config);assert db.claim(store._store,row['id'])
    assert not db.claim(store._store,row['id'])
    db.finish(store._store,row['id'],[(i*10,float(i)) for i in range(62)],Counter.provenance)
    _,points=db.result(store._store,video['id'],row['id'],'5min')
    assert [p['value'] for p in points]==[14.5,44.5]
    assert [p['offset_seconds'] for p in points]==[300,600]
    assert [p['sample_count'] for p in points]==[30,30]

def test_interrupted_and_failed_tasks_can_resume(counting,monkeypatch):
    store,video,_=counting
    row=db.request(store._store,video,CONFIG);db.claim(store._store,row['id'])
    with store.write() as u:
        u.session.execute(db.analyses.update().where(db.analyses.c.id==row['id']).values(updated_at=db.now()-timedelta(minutes=4)))
    assert db.latest(store._store,video['id'])['status']=='queued'
    monkeypatch.setattr('cv2.VideoCapture',lambda path:None)
    local_counting.run(store,row['id']);assert db.get(store._store,row['id'])['status']=='failed'
    assert db.request(store._store,video,CONFIG)['status']=='queued'
    monkeypatch.setattr('cv2.VideoCapture',Capture)
    local_counting.run(store,row['id']);assert db.get(store._store,row['id'])['status']=='ready'


def test_every_sample_saves_annotated_frame_and_cached_tasks_repair_missing_images(counting,monkeypatch):
    import cv2

    store,video,root=counting
    calls=[]
    class TrackedCounter(Counter):
        def __call__(self,frame):
            calls.append(frame.copy())
            return super().__call__(frame)
    monkeypatch.setattr(local_counting,'counter_for',lambda bundle:TrackedCounter())
    task=local_counting.start(store,video['id'],CONFIG)
    folder=counting_captures.directory(video['id'],task['analysis_id'])
    import re
    assert folder.parent==root.parent/'cap_images'
    assert re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}',folder.name)
    files=sorted(folder.glob('*.jpg'))
    assert [p.name for p in files]==[
        'frame_000001_000000000ms.jpg','frame_000002_000005000ms.jpg',
        'frame_000003_000010000ms.jpg','frame_000004_000015000ms.jpg']
    assert len(calls)==4
    for index,path in enumerate(files):
        image=cv2.imdecode(np.frombuffer(path.read_bytes(),dtype=np.uint8),cv2.IMREAD_COLOR)
        assert image.shape==(128,128,3)
        assert np.all(np.abs(image[90,90].astype(int)-index*2)<=2)
        if index:
            assert image[26,26,2]>150 and image[26,26,1]<100
        assert np.count_nonzero(np.all(image[:50]>180,axis=2))>20  # Count badge text.
    assert local_counting.start(store,video['id'],CONFIG)['analysis_id']==task['analysis_id']
    assert len(calls)==4  # Complete artifacts keep the existing inference cache.
    files[1].unlink()
    assert local_counting.start(store,video['id'],CONFIG)['analysis_id']==task['analysis_id']
    assert len(calls)==8 and len(list(folder.glob('*.jpg')))==4
    (folder/'captures.json').write_text('{"format":"raw"}',encoding='utf-8')
    local_counting.start(store,video['id'],CONFIG)
    assert len(calls)==12  # Old unmarked output is regenerated too.
    assert not list(folder.glob('*.tmp'))
    _,points=db.result(store._store,video['id'],task['analysis_id'],'frame')
    assert [p['value'] for p in points]==[0,2,4,6]


def test_capture_failure_does_not_report_success_and_can_retry(counting,monkeypatch):
    import cv2

    store,video,root=counting
    encode=cv2.imencode
    monkeypatch.setattr(cv2,'imencode',lambda *args,**kwargs:(False,None))
    task=local_counting.start(store,video['id'],CONFIG)
    row=db.get(store._store,task['analysis_id'])
    assert row['status']=='failed' and row['processed_samples']==0
    assert not list((root.parent/'cap_images').rglob('*.jpg'))
    monkeypatch.setattr(cv2,'imencode',encode)
    local_counting.start(store,video['id'],CONFIG)
    assert db.get(store._store,task['analysis_id'])['status']=='ready'
    assert counting_captures.complete(db.get(store._store,task['analysis_id']))


def test_loading_cached_result_repairs_deleted_screenshots(counting):
    store,video,_=counting
    task=local_counting.start(store,video['id'],CONFIG)
    folder=counting_captures.directory(video['id'],task['analysis_id'])
    files=list(folder.glob('*.jpg'))
    assert len(files)==4
    files[0].unlink()
    local_counting.current(store,video['id'])
    row=db.get(store._store,task['analysis_id'])
    assert row['status']=='ready'
    assert counting_captures.complete(row)
    assert len(list(folder.glob('*.jpg')))==4


def test_annotation_uses_model_points_roi_and_confidence_without_altering_input():
    from intelligent_analysis.contracts import ROI
    roi=ROI.model_validate({'points':[{'x':0,'y':0},{'x':.5,'y':0},{'x':.5,'y':1},{'x':0,'y':1}]})
    frame=np.zeros((200,200,3),dtype=np.uint8)
    points=np.array([[.2,.5,.9],[.8,.5,.9],[.2,.7,.5]])
    image=counting_captures.annotate(frame,points,roi,.5,1)
    assert image[100,40].tolist()==[0,0,255]
    assert image[100,160].tolist()==[0,0,0]
    assert image[140,40].tolist()==[0,0,0]
    assert not frame.any()
    assert (image[:50,:,0]>180).any()
    with pytest.raises(ValueError,match='differs'):
        counting_captures.annotate(frame,points,roi,.5,3)
