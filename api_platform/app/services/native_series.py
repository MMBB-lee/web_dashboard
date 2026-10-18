"""Expose verified original periods through the existing chart data shape."""
from __future__ import annotations
from datetime import date
from collections import defaultdict
from decimal import Decimal
import sqlalchemy as sa
from app.core.providers import get_c_store
from app.core.errors import AppError, CODE_BAD_REQUEST, CODE_NOT_FOUND
from app.schemas.common import iso_cn
from app.api.v1._series import series_response

GRAINS = ('day','week','month','holiday','year','quarter','half_year','cumulative')
ALIASES = {'airport_throughput':['airport_throughput','airport_passenger_throughput'],
           'rail_departures':['rail_departures','station_passenger_departures'],
           'scenic_visits':['scenic_visits']}


def archive_rows(store, entity_name, metric=None):
    from data_foundation.db.public_data import public_data_facts as table
    with store.read() as uow:
        if not sa.inspect(uow.session.bind).has_table('public_data_facts'):
            return []
        query = sa.select(table).where(table.c.entity_name == entity_name,
            table.c.quality_status == 'verified', sa.or_(table.c.qualifier.is_(None),table.c.qualifier == ''))
        if metric:
            query = query.where(table.c.metric_code.in_(ALIASES.get(metric,[metric])))
        return [dict(r) for r in uow.session.execute(query.order_by(table.c.period_end,table.c.id)).mappings()]


def capabilities(store, entity):
    grouped = defaultdict(list)
    for row in archive_rows(store,entity['name']):
        code = next((code for code, names in ALIASES.items() if row['metric_code'] in names),None)
        if code and row['unit'] == '人次':
            grouped[(code,row['grain'])].append(row)
    from data_foundation.ingestion.project_flow import read_rows
    for row in read_rows(store,entity['id']):
        grouped[(row['metric'],row['grain'])].append(row)
    return [{'metric':code,'grain':grain,'start':min(r['period_start'] for r in rows),
             'end':max(r['period_end'] for r in rows),'count':len({(r['period_start'],r['period_end']) for r in rows})}
            for (code,grain),rows in grouped.items()]


def query_series(entity_id, metric, grain='auto', preferred_grain='month', start=None, end=None, as_of=None, *, store=None):
    from uuid import UUID
    from data_foundation.db.models import Entity,Observation
    try: UUID(entity_id)
    except ValueError: raise AppError(400,CODE_BAD_REQUEST,'对象 ID 不是合法 UUID') from None
    if grain not in (*GRAINS,'auto') or preferred_grain not in GRAINS or metric not in ALIASES:
        raise AppError(400,CODE_BAD_REQUEST,'指标或统计粒度无效')
    store = store or get_c_store()
    with store.read() as uow:
        entity = uow.session.get(Entity,entity_id)
        if entity is None: raise AppError(404,CODE_NOT_FOUND,'对象不存在')
        info = {k:getattr(entity,k) for k in ('id','name','type')}
        expected = {'airport':'airport_throughput','rail_station':'rail_departures','scenic':'scenic_visits'}
        if expected.get(entity.type) != metric:
            raise AppError(422,'metric_entity_mismatch','指标不适用于该对象')
        observed = uow.session.execute(sa.select(Observation.grain,sa.func.min(Observation.period_start),sa.func.max(Observation.period_end))
            .where(Observation.entity_id==entity_id,Observation.metric_code==metric,Observation.quality_status=='verified')
            .group_by(Observation.grain)).all()
    archived = [r for r in archive_rows(store,info['name'],metric) if r['unit']=='人次']
    from data_foundation.ingestion.project_flow import read_rows
    project=read_rows(store,entity_id)
    for kind in ('day','week','month','holiday'):
        selected_rows=[r for r in project if r['grain']==kind]
        if selected_rows:
            observed.append((kind,min(r['period_start'] for r in selected_rows),max(r['period_end'] for r in selected_rows)))
    cutoff = date.fromisoformat(as_of) if isinstance(as_of,str) else as_of or date.today()
    archived = [r for r in archived if r['period_end']<=cutoff]
    available = {g for g,a,b in observed if a<=cutoff} | {r['grain'] for r in archived}
    selected = grain
    if grain == 'auto':
        latest = {g:max([b for kind,a,b in observed if kind==g and b<=cutoff] +
                         [r['period_end'] for r in archived if r['grain']==g],default=date.min) for g in available}
        priority = ('month','year','day','quarter','holiday','half_year','cumulative','week')
        selected = preferred_grain if preferred_grain in available else max(latest,key=lambda g:(latest[g],-priority.index(g)),default=preferred_grain)
    def day(value):
        if value is None: return None
        try: return date.fromisoformat(value) if isinstance(value,str) else value
        except ValueError: raise AppError(400,CODE_BAD_REQUEST,'日期格式应为 YYYY-MM-DD') from None
    start,end = day(start),day(end)
    if start and end and start>end: raise AppError(400,CODE_BAD_REQUEST,'开始日期不能晚于结束日期')
    ranges = [(a,b) for g,a,b in observed if g==selected] + [(r['period_start'],r['period_end']) for r in archived if r['grain']==selected]
    lower = start or min((a for a,b in ranges),default=cutoff)
    upper = min(end or max((b for a,b in ranges),default=cutoff),cutoff)
    if selected == 'holiday' and any(r['grain']=='holiday' for r in project):
        from data_foundation.ingestion.project_flow import point
        from app.api.v1._series import points_out
        project_points=[point(r) for r in project if r['grain']=='holiday' and lower<=r['period_start'] and r['period_end']<=upper]
        return {'entity':info,'metric':metric,'grain':selected,'points':points_out(project_points),
                'missing_periods':[],'coverage_ratio':1,'transport_type':None}
    if selected in {'day','week','month'} and any(g==selected for g,a,b in observed):
        row = store.query_project_series(entity_id=entity_id,metric=metric,grain=selected,start=lower,end=upper,quality='verified')
        return series_response(row)
    candidates = [r for r in archived if r['grain']==selected and r['period_start']>=lower and r['period_end']<=upper]
    groups = defaultdict(list)
    for row in candidates: groups[(row['period_start'],row['period_end'])].append(row)
    points, conflicts = [],[]
    for period, rows in sorted(groups.items()):
        # C original observations/supplements precede crawler re-extractions. No sums.
        rows.sort(key=lambda r:(0 if r['origin'].startswith('c_') else 1,r['id']))
        if len({(r['value'],r['unit']) for r in rows})>1:
            conflicts.append({'period_start':str(period[0]),'period_end':str(period[1])})
            continue
        row = rows[0]
        points.append({'metric':metric,'value':float(row['value']),'unit':row['unit'],
            'period_start':str(row['period_start']),'period_end':str(row['period_end']),'grain':selected,
            'source_ids':['public-'+r['id'] for r in rows], 'published_at':iso_cn(row['published_at']),
            'source_title':row['source_title'],'source_url':row['source_url'],
            'ingested_at':iso_cn(row['imported_at']),'quality_status':'verified','is_forecast':False,'model_version':None})
    # Discrete native periods are not a continuous daily/monthly coverage claim.
    return {'entity':info,'metric':metric,'grain':selected,'points':points,'missing_periods':conflicts,
            'coverage_ratio':0,'coverage_note':'原始发布周期；不推算连续时序覆盖率',
            'transport_type':{'airport':'airport','rail_station':'rail'}.get(info['type'])}


def fact_source(source_id):
    from data_foundation.db.public_data import public_data_facts as table
    with get_c_store().read() as uow:
        row = uow.session.execute(sa.select(table).where(table.c.id==source_id.removeprefix('public-'))).mappings().first()
    if row is None: raise AppError(404,CODE_NOT_FOUND,'统计来源不存在')
    return {'id':source_id,'publisher':'原发布单位','title':row['source_title'],'url':row['source_url'],
        'published_at':row['published_at'],'retrieved_at':row['imported_at'],'coverage_start':row['period_start'],
        'coverage_end':row['period_end'],'native_grain':row['grain'],'usage_note':'原始公开统计资料；保留原周期与口径，不与重叠资料合计。',
        'verification_status':row['quality_status']}
