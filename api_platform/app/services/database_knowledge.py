"""Incremental RAG exports of explicitly allowed business fields, using the normal E indexing queue."""
from __future__ import annotations
from collections import defaultdict
from datetime import datetime, timezone
from io import BytesIO
import hashlib
import json
import threading
from uuid import NAMESPACE_URL, uuid5, uuid4
import sqlalchemy as sa
from app.core.errors import AppError

ADAPTER = 'project.database_rag.v1'
PERMISSION = '项目授权业务数据；保留原统计期、来源、核验状态及估算模拟标记。'
_sync_lock = threading.Lock()
_index_lock = threading.Lock()
LABELS = {'scenic_visits':'景区接待人次','rail_departures':'铁路发送人次','airport_throughput':'机场旅客吞吐量',
          'airport_passenger_throughput':'机场旅客吞吐量','station_passenger_departures':'铁路发送人次'}
KINDS = {'verified':'已核验公开统计','published':'公开统计','estimated':'估算','simulated':'模拟','pending_review':'待核验'}
GRAINS = {'day':'日','week':'周','month':'月','holiday':'假期','year':'年','quarter':'季度','half_year':'半年','cumulative':'累计'}

def identifier(value): return str(uuid5(NAMESPACE_URL, ADAPTER + ':' + value))

def export_business_documents(store):
    """No reflection or SELECT *: credentials, users, sessions and jobs never enter the export."""
    from data_foundation.db.models import Entity, Metric, Holiday, Observation, Event, Source
    from data_foundation.db.public_data import public_data_facts
    from data_foundation.ingestion.project_flow import table as project
    grouped = defaultdict(list)
    with store.read() as u:
        entities = u.session.execute(sa.select(Entity.id,Entity.name,Entity.type,Entity.region_code,Entity.longitude,Entity.latitude).order_by(Entity.id)).mappings().all()
        by_id = {e['id']:e for e in entities}; by_name = {e['name']:e for e in entities}
        source_info = {r.id:r for r in u.session.execute(sa.select(Source.id,Source.title,Source.url))}
        catalog = [f"地点：{e['name']}；类型：{e['type']}；地区：{e['region_code']}；经度：{e['longitude']}；纬度：{e['latitude']}。" for e in entities]
        for m in u.session.execute(sa.select(Metric.code,Metric.display_name,Metric.definition,Metric.unit)):
            catalog.append(f"指标：{m.display_name}；代码：{m.code}；单位：{m.unit}；定义：{m.definition}。")
        observed = u.session.execute(sa.select(Observation.entity_id,Observation.metric_code,Observation.grain,Observation.period_start,
            Observation.period_end,Observation.value,Observation.unit,Observation.quality_status,Observation.source_id).order_by(Observation.id)).mappings().all()
        rows = []
        for r in observed:
            e = by_id.get(r['entity_id'])
            if e:
                src = source_info.get(r['source_id'])
                rows.append(dict(r)|{'entity_name':e['name'],'metric':r['metric_code'],'kind':r['quality_status'],
                    'source':f'{src.title} {src.url}' if src else '已导入观测'})
        inspector = sa.inspect(u.session.bind)
        if inspector.has_table(public_data_facts.name):
            for r in u.session.execute(sa.select(public_data_facts.c.entity_name,public_data_facts.c.metric_code,public_data_facts.c.grain,
                public_data_facts.c.period_start,public_data_facts.c.period_end,public_data_facts.c.value,public_data_facts.c.unit,
                public_data_facts.c.quality_status,public_data_facts.c.qualifier,public_data_facts.c.source_title,public_data_facts.c.source_url).order_by(public_data_facts.c.id)).mappings():
                rows.append(dict(r)|{'metric':r['metric_code'],'kind':r['quality_status'],
                    'source':f"{r['source_title']} {r['source_url']}；限定语：{r['qualifier'] or '无'}"})
        if inspector.has_table(project.name):
            for r in u.session.execute(sa.select(project.c.entity_id,project.c.metric,project.c.grain,project.c.period_start,project.c.period_end,
                project.c.value,project.c.unit,project.c.data_kind).order_by(project.c.id)).mappings():
                e = by_id.get(r['entity_id'])
                if e: rows.append(dict(r)|{'entity_name':e['name'],'kind':r['data_kind'],'source':'项目客流数据清单'})
        for r in rows:
            e = by_name.get(r['entity_name'])
            key = f"entity/{e['id'] if e else r['entity_name']}/{r['period_start'].year}"
            grouped[key].append(f"{r['entity_name']}；{LABELS.get(r['metric'],r['metric'])}；统计期：{r['period_start']} 至 {r['period_end']}；"
                f"{GRAINS.get(r['grain'],r['grain'])}；{r['value']} {r['unit']}；{KINDS.get(r['kind'],r['kind'])}；来源：{r['source']}。")
        holidays = [f"{h.name}；类型：{h.kind}；{h.start_date} 至 {h.end_date}；{h.days_count}天；地区：{h.region_code}；范围：{h.scope_key}。"
            for h in u.session.execute(sa.select(Holiday.name,Holiday.kind,Holiday.start_date,Holiday.end_date,Holiday.days_count,Holiday.region_code,Holiday.scope_key).order_by(Holiday.id))]
        events = [f"事件：{e.title}；{e.description}；生效：{e.starts_at} 至 {e.ends_at}；状态：{e.status}；发布性质：{e.authority}。"
            for e in u.session.execute(sa.select(Event.title,Event.description,Event.starts_at,Event.ends_at,Event.status,Event.authority).order_by(Event.id))]
    docs = {'catalog':('数据库地点与指标目录',catalog),'holidays':('数据库假期日历与分析窗口',holidays),
            'events':('数据库事件资料',events or ['当前没有已登记事件。'])}
    for key, lines in grouped.items():
        parts = key.split('/'); e = by_id.get(parts[1]); name = e['name'] if e else parts[1]
        docs[key] = (f"{name} {parts[-1]}年数据库客流资料", sorted(set(lines)))
    return {key:{'title':title,'text':'# '+title+'\n\n'+PERMISSION+'\n历史数据不代表实时客流，不同指标及重叠统计期不能相加。\n\n'+'\n\n'.join(lines),
                 'records':len(lines)} for key,(title,lines) in docs.items()}

def sync_database(store, requested_by=None):
    from data_foundation.db.models import Source, KnowledgeDocument
    if not _sync_lock.acquire(blocking=False): raise AppError(409,'sync_in_progress','数据库知识同步正在进行，请稍后查看状态')
    try:
        exported = export_business_documents(store)
        with store.read() as u:
            previous = u.session.scalars(sa.select(Source).where(Source.adapter_key==ADAPTER)).all()
            previous_keys = {s.adapter_config['key'] for s in previous}
        for key in previous_keys - exported.keys():
            exported[key] = {'title':'已移除数据库资料','text':'该组业务数据已从数据库移除，不再提供旧值。','records':0}
        counts = {'created':0,'updated':0,'unchanged':0,'queued':0,'records':sum(d['records'] for d in exported.values())}
        for key, doc in sorted(exported.items()):
            raw=doc['text'].encode('utf-8'); digest=hashlib.sha256(raw).hexdigest()
            sid=identifier(key); did=identifier(key+':'+digest); now=datetime.now(timezone.utc)
            url=f'http://127.0.0.1:8000/api/v1/knowledge/documents/{did}'
            with store.read() as u:
                existing=u.session.get(KnowledgeDocument,did)
                old=u.session.get(Source,sid)
                config=dict(old.adapter_config) if old else {}
                index_status=existing.index_status if existing else None
            if index_status=='ready' or (existing and config.get('job_id') and (store.get_job(config['job_id']) or {}).get('status') in {'queued','running'}):
                counts['unchanged']+=1
            else:
                if existing is None:
                    if not old:
                        store.register_source(source_id=sid,publisher='项目业务数据库',title=doc['title'],url=url,usage_note=PERMISSION,
                            adapter_key=ADAPTER,adapter_config={'key':key},verification_status='pending_review',enabled=False)
                    file=store.persist_file(source_id=sid,original_name='database.txt',stream=BytesIO(raw))
                    asset=store.register_asset(source_id=sid,url=url,title=doc['title'],retrieved_at=now,published_at=now,
                        relative_path=file['relative_path'],sha256=file['sha256'],media_type='text/plain',byte_size=file['byte_size'],usage_note=PERMISSION)
                    store.register_document(document_id=did,source_id=sid,asset_id=asset['id'],title=doc['title'],url=url,
                        publisher='项目业务数据库',published_at=now,usage_note=PERMISSION,document_version='sha256:'+digest[:32])
                    counts['updated' if old else 'created']+=1
                else:
                    counts['unchanged']+=1
                job_id=store.create_job('knowledge_index',{'document_id':did,'document_version':'sha256:'+digest[:32]},
                    analysis_id=did,idempotency_key='db-'+hashlib.sha256((did+str(uuid4())).encode()).hexdigest()[:40],
                    requested_by=requested_by,reason='管理员更新数据库知识')
                config['job_id']=job_id;counts['queued']+=1
            with store.write() as u:
                src=u.session.get(Source,sid)
                src.adapter_config=config|{'key':key,'document_id':did,'sha256':digest,'records':doc['records']}
        return counts | {'status':'queued' if counts['queued'] else 'up_to_date','documents':len(exported)}
    finally:
        _sync_lock.release()

def database_status(store):
    from data_foundation.db.models import Source,KnowledgeDocument,Job
    result={'documents':0,'ready':0,'pending':0,'failed':0,'records':0}
    with store.read() as u:
        for s in u.session.scalars(sa.select(Source).where(Source.adapter_key==ADAPTER)):
            config=s.adapter_config;d=u.session.get(KnowledgeDocument,config.get('document_id'))
            job=u.session.get(Job,config.get('job_id')) if config.get('job_id') else None
            result['documents']+=1;result['records']+=config.get('records',0)
            result['ready' if d and d.index_status=='ready' else 'failed' if job and job.status=='failed' else 'pending']+=1
    return result

def index_pending_database_documents(store):
    """The button also wakes a scoped queue consumer; no separate CLI worker is required."""
    if not _index_lock.acquire(blocking=False): return
    try:
        from data_foundation.db.models import Source,Job
        from app.worker import DWorker
        worker=DWorker(store)
        while True:
            with store.read() as u:
                ids=[s.adapter_config.get('job_id') for s in u.session.scalars(sa.select(Source).where(Source.adapter_key==ADAPTER))]
                queued=u.session.scalars(sa.select(Job.id).where(Job.id.in_([id for id in ids if id]),Job.status=='queued')).all()
            if not queued: break
            for job_id in queued:
                claimed=store.claim_job('database-rag-button',datetime.now(timezone.utc),lease_seconds=120,
                                        job_type='knowledge_index',job_id=job_id)
                if claimed: worker.run_claimed(claimed)
    finally:
        _index_lock.release()

def latest_ready_ids(store, ids):
    """Retain the previous immutable version until its replacement is ready."""
    from data_foundation.db.models import Source,KnowledgeDocument
    eligible=set(ids); result=set(ids)
    with store.read() as u:
        sources=u.session.scalars(sa.select(Source).where(Source.adapter_key==ADAPTER)).all()
        for src in sources:
            versions=u.session.execute(sa.select(KnowledgeDocument.id,KnowledgeDocument.created_at)
                .where(KnowledgeDocument.source_id==src.id).order_by(KnowledgeDocument.created_at.desc())).all()
            ready=[v.id for v in versions if v.id in eligible]
            result.difference_update(ready)
            desired=src.adapter_config.get('document_id')
            if desired in ready: result.add(desired)
            elif ready: result.add(ready[0])
    return [id for id in ids if id in result]
