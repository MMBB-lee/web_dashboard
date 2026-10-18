"""Index videos placed directly in a local folder without fabricating scenic or recording metadata."""
from datetime import datetime,timezone
import hashlib
import json
import mimetypes
from pathlib import Path
from threading import Lock
from uuid import NAMESPACE_URL,uuid5
import sqlalchemy as sa
from ..db import Base
from ..errors import NotFound,Conflict,InvalidArgument

EXTENSIONS={'.mp4','.webm','.mov','.mkv','.avi'}
_sync_lock=Lock()
table=sa.Table('local_video_files',Base.metadata,
    sa.Column('id',sa.String(36),primary_key=True),
    sa.Column('relative_path',sa.String(512),unique=True,nullable=False),
    sa.Column('title',sa.String(300),nullable=False),
    sa.Column('sha256',sa.String(64),nullable=False),
    sa.Column('byte_size',sa.BigInteger,nullable=False),
    sa.Column('mtime_ns',sa.BigInteger,nullable=False),
    sa.Column('duration_seconds',sa.Float,nullable=True),
    sa.Column('source_note',sa.Text,nullable=True),
    sa.Column('status',sa.String(16),nullable=False),
    sa.Column('last_seen_at',sa.DateTime,nullable=False))

def digest(path):
    result=hashlib.sha256()
    with path.open('rb') as stream:
        while block:=stream.read(1024*1024): result.update(block)
    return result.hexdigest()

def duration(path):
    try:
        import cv2
        capture=cv2.VideoCapture(str(path))
        try:
            rate=capture.get(cv2.CAP_PROP_FPS);frames=capture.get(cv2.CAP_PROP_FRAME_COUNT)
            return round(frames/rate,3) if capture.isOpened() and rate>0 and frames>0 else None
        finally: capture.release()
    except (ImportError,ValueError,OSError): return None

def sync_directory(store,root):
    with _sync_lock:
        return _sync_directory(store,root)

def _sync_directory(store,root):
    root=Path(root).resolve();root.mkdir(parents=True,exist_ok=True)
    table.create(store.engine,checkfirst=True)
    with store.read() as u:
        previous={r['relative_path']:dict(r) for r in u.session.execute(sa.select(table)).mappings()}
    now=datetime.now(timezone.utc).replace(tzinfo=None);seen=set();updates=[]
    counts={'inserted':0,'updated':0,'unchanged':0,'missing':0}
    for path in sorted(root.iterdir()):
        if path.suffix.lower() not in EXTENSIONS or not path.is_file(): continue
        resolved=path.resolve()
        if not resolved.is_relative_to(root): continue
        name=path.name;seen.add(name);stat=path.stat();old=previous.get(name)
        note=None
        sidecar=path.with_suffix('.metadata.json')
        if sidecar.is_file() and sidecar.resolve().is_relative_to(root):
            try: note=str(json.loads(sidecar.read_text(encoding='utf-8')).get('source_note') or '')[:2000] or None
            except (OSError,ValueError): pass
        same_file=old and old['byte_size']==stat.st_size and old['mtime_ns']==stat.st_mtime_ns and old['status']=='ready'
        if same_file and old['source_note']==note:
            counts['unchanged']+=1;continue
        sha=old['sha256'] if same_file else digest(resolved)
        after=path.stat()
        if (stat.st_size,stat.st_mtime_ns)!=(after.st_size,after.st_mtime_ns): continue
        updates.append({'id':str(uuid5(NAMESPACE_URL,'anhui-local-video:'+name)),'relative_path':name,'title':path.stem,
            'sha256':sha,'byte_size':stat.st_size,'mtime_ns':stat.st_mtime_ns,'duration_seconds':old['duration_seconds'] if same_file else duration(resolved),
            'source_note':note,'status':'ready','last_seen_at':now})
        counts['updated' if old else 'inserted']+=1
    with store.write() as u:
        for values in updates:
            old=previous.get(values['relative_path'])
            if old: u.session.execute(table.update().where(table.c.id==old['id']).values(**values))
            else: u.session.execute(table.insert().values(**values))
        for name,row in previous.items():
            if name not in seen and row['status']!='missing':
                u.session.execute(table.update().where(table.c.id==row['id']).values(status='missing'));counts['missing']+=1
    return counts

def list_files(store):
    with store.read() as u:
        if not sa.inspect(u.session.bind).has_table(table.name): return []
        return [dict(r) for r in u.session.execute(sa.select(table).where(table.c.status=='ready').order_by(table.c.title)).mappings()]

def get_file(store,video_id):
    with store.read() as u:
        if not sa.inspect(u.session.bind).has_table(table.name): return None
        row=u.session.execute(sa.select(table).where(table.c.id==video_id)).mappings().first()
        return dict(row) if row else None

def playback_ref(store,root,video_id):
    row=get_file(store,video_id)
    if not row or row['status']!='ready': raise NotFound('本地录像不存在或已移出videos目录')
    root=Path(root).resolve();path=(root/row['relative_path']).resolve()
    if not path.is_relative_to(root) or path.suffix.lower() not in EXTENSIONS: raise InvalidArgument('录像路径不在videos目录内')
    if not path.is_file(): raise NotFound('录像文件已移出videos目录，请刷新列表')
    if path.stat().st_size!=row['byte_size'] or digest(path)!=row['sha256']: raise Conflict('录像文件已更新，请刷新列表后播放')
    return {'video_id':row['id'],'path':str(path),'byte_size':row['byte_size'],
            'media_type':mimetypes.guess_type(path.name)[0] or 'video/mp4'}
