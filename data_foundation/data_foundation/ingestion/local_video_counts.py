"""Persist directory-video counting tasks using relative video time, without invented recording dates."""
from datetime import datetime,timezone,timedelta
import hashlib,json,math
from statistics import median
from uuid import uuid4
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from ..db import Base
from ..errors import NotFound,Conflict,AnalysisNotReady
from .local_videos import table as videos,get_file

analyses=sa.Table('local_video_analyses',Base.metadata,
    sa.Column('id',sa.String(36),primary_key=True),
    sa.Column('video_id',sa.String(36),sa.ForeignKey(videos.c.id),nullable=False),
    sa.Column('video_sha256',sa.String(64),nullable=False),
    sa.Column('config_sha256',sa.String(64),nullable=False),
    sa.Column('config',sa.JSON,nullable=False),sa.Column('provenance',sa.JSON,nullable=True),
    sa.Column('status',sa.String(16),nullable=False),
    sa.Column('processed_samples',sa.Integer,nullable=False),sa.Column('total_samples',sa.Integer,nullable=False),
    sa.Column('duration_seconds',sa.Float,nullable=False),sa.Column('error_message',sa.Text,nullable=True),
    sa.Column('created_at',sa.DateTime,nullable=False),sa.Column('updated_at',sa.DateTime,nullable=False),
    sa.UniqueConstraint('video_id','video_sha256','config_sha256',name='uq_local_video_analysis_input'))
points=sa.Table('local_video_count_points',Base.metadata,
    sa.Column('analysis_id',sa.String(36),sa.ForeignKey(analyses.c.id),primary_key=True),
    sa.Column('point_index',sa.Integer,primary_key=True),sa.Column('grain',sa.String(8),nullable=False),
    sa.Column('offset_seconds',sa.Float,nullable=False),sa.Column('period_start_seconds',sa.Float,nullable=True),
    sa.Column('value',sa.Float,nullable=True),sa.Column('sample_count',sa.Integer,nullable=False),
    sa.Column('quality_status',sa.String(16),nullable=False))

def now(): return datetime.now(timezone.utc).replace(tzinfo=None)

def ensure(store):
    analyses.create(store.engine,checkfirst=True);points.create(store.engine,checkfirst=True)

def get(store,analysis_id):
    with store.read() as u:
        row=u.session.execute(sa.select(analyses).where(analyses.c.id==analysis_id)).mappings().first()
        return dict(row) if row else None

def latest(store,video_id):
    ensure(store)
    video=get_file(store,video_id)
    if not video or video['status']!='ready': raise NotFound('录像不存在，请刷新目录')
    with store.write() as u:
        u.session.execute(analyses.update().where(analyses.c.video_id==video_id,
            analyses.c.status=='processing',analyses.c.updated_at<now()-timedelta(minutes=3)).values(status='queued'))
        row=u.session.execute(sa.select(analyses).where(analyses.c.video_id==video_id,
            analyses.c.video_sha256==video['sha256']).order_by(analyses.c.updated_at.desc())).mappings().first()
        return dict(row) if row else None

def request(store,video,config):
    ensure(store)
    config_sha=hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest()
    condition=(analyses.c.video_id==video['id'],analyses.c.video_sha256==video['sha256'],analyses.c.config_sha256==config_sha)
    with store.read() as u:
        row=u.session.execute(sa.select(analyses).where(*condition)).mappings().first()
    if row:
        if row['status']=='failed':
            with store.write() as u:
                u.session.execute(analyses.update().where(analyses.c.id==row['id'],analyses.c.status=='failed').values(
                    status='queued',processed_samples=0,error_message=None,updated_at=now()))
        elif row['status']=='ready':
            with store.write() as u:
                u.session.execute(analyses.update().where(analyses.c.id==row['id']).values(updated_at=now()))
        return get(store,row['id'])
    duration=float(video['duration_seconds'] or 0)
    if not math.isfinite(duration) or duration<=0: raise Conflict('无法读取视频时长，请检查文件')
    values=dict(id=str(uuid4()),video_id=video['id'],video_sha256=video['sha256'],config_sha256=config_sha,
        config=config,provenance=None,status='queued',processed_samples=0,
        total_samples=math.ceil(duration/config['sample_interval_seconds']),duration_seconds=duration,
        error_message=None,created_at=now(),updated_at=now())
    try:
        with store.write() as u: u.session.execute(analyses.insert().values(**values))
    except IntegrityError:
        with store.read() as u:
            return dict(u.session.execute(sa.select(analyses).where(*condition)).mappings().one())
    return values

def requeue_for_captures(store,analysis_id):
    """Explicitly rerun a cached analysis whose sampled screenshots are missing."""
    with store.write() as u:
        u.session.execute(analyses.update().where(analyses.c.id==analysis_id,
            analyses.c.status=='ready').values(status='queued',processed_samples=0,
            error_message=None,updated_at=now()))
    return get(store,analysis_id)


def claim(store,analysis_id):
    with store.write() as u:
        changed=u.session.execute(analyses.update().where(analyses.c.id==analysis_id,analyses.c.status=='queued').values(
            status='processing',updated_at=now()))
        return changed.rowcount==1

def progress(store,analysis_id,processed):
    with store.write() as u:
        u.session.execute(analyses.update().where(analyses.c.id==analysis_id,analyses.c.status=='processing').values(
            processed_samples=processed,updated_at=now()))

def finish(store,analysis_id,samples,provenance):
    row=get(store,analysis_id);video=get_file(store,row['video_id'])
    if not video or video['sha256']!=row['video_sha256']: raise Conflict('录像文件已替换，请重新开始计数')
    interval=row['config']['sample_interval_seconds'];values=[]
    for offset,value in samples:
        if not math.isfinite(offset) or not 0<=offset<row['duration_seconds'] or not math.isfinite(value) or value<0:
            raise ValueError('Invalid relative count sample')
        values.append(dict(grain='frame',offset_seconds=offset,period_start_seconds=None,value=value,sample_count=1,quality_status='good'))
    for index in range(int(row['duration_seconds']//300)):
        start=index*300;end=start+300
        window=[value for offset,value in samples if start<=offset<end]
        expected=sum(1 for n in range(row['total_samples']) if start<=n*interval<end)
        values.append(dict(grain='5min',offset_seconds=end,period_start_seconds=start,
            value=median(window) if window else None,sample_count=len(window),
            quality_status='good' if window and len(window)==expected else 'degraded' if window else 'unavailable'))
    with store.write() as u:
        u.session.execute(points.delete().where(points.c.analysis_id==analysis_id))
        if values: u.session.execute(points.insert(),[dict(analysis_id=analysis_id,point_index=i,**value) for i,value in enumerate(values)])
        u.session.execute(analyses.update().where(analyses.c.id==analysis_id).values(status='ready',
            processed_samples=len(samples),provenance=provenance,error_message=None,updated_at=now()))

def fail(store,analysis_id,message):
    with store.write() as u:
        u.session.execute(analyses.update().where(analyses.c.id==analysis_id).values(status='failed',error_message=message,updated_at=now()))

def result(store,video_id,analysis_id,grain):
    row=get(store,analysis_id);video=get_file(store,video_id)
    if not row or row['video_id']!=video_id: raise NotFound('该录像的分析结果不存在')
    if not video or video['status']!='ready': raise NotFound('录像已移出目录')
    if row['video_sha256']!=video['sha256']: raise Conflict('录像已替换，请重新开始计数')
    if row['status'] in {'queued','processing'}: raise AnalysisNotReady('计数尚未完成')
    if row['status']!='ready': raise Conflict(row['error_message'] or '计数尚未完成')
    with store.read() as u:
        samples=[dict(r) for r in u.session.execute(sa.select(points).where(points.c.analysis_id==analysis_id,
            points.c.grain==grain).order_by(points.c.offset_seconds)).mappings()]
    return row,samples

def registered_latest(store,video_id):
    """Discover existing formal analyses without requiring a client-supplied UUID."""
    with store.read() as u:
        inspector=sa.inspect(u.session.bind)
        for name in ('visible_analyses','video_analyses'):
            if not inspector.has_table(name): continue
            registered=sa.Table(name,sa.MetaData(),autoload_with=u.session.bind)
            if not {'id','video_id','status'}.issubset(registered.c.keys()): continue
            query=sa.select(registered).where(registered.c.video_id==video_id)
            if 'created_at' in registered.c: query=query.order_by(registered.c.created_at.desc())
            row=u.session.execute(query.limit(1)).mappings().first()
            if row:
                return dict(analysis_id=row['id'],status='ready' if row['status'] in {'completed','succeeded','ready'} else row['status'],
                    processed_samples=0,total_samples=0,progress=100 if row['status'] in {'completed','succeeded','ready'} else 0,
                    error_message=row.get('error_message'),time_basis='calendar')
    return None
