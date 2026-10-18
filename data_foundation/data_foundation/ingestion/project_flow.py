"""Reproducible project series. Published, estimated and simulated values stay distinct.

This table never overwrites official observations. The project API explicitly opts
into it; original evidence, assumptions and source kinds remain exportable.
"""
from __future__ import annotations
import calendar
from collections import Counter, defaultdict
import csv
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import io
import json
import math
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5
import sqlalchemy as sa
from ..db import Base
from ..db.models import Entity, Holiday, Source
from ..contracts import AssetCreate, SourceCreate

VERSION = 'project-flow-v1'
SOURCE_ID = str(uuid5(NAMESPACE_URL, VERSION))
SOURCE_URL = 'http://127.0.0.1:8765/api/project-data/export.csv'
ENTITY_TYPES = {**dict.fromkeys(('黄山风景区主景区','九华山','万佛湖'),'scenic'),
                **dict.fromkeys(('合肥南站','黄山北站','芜湖站','阜阳西站'),'rail_station'),
                **dict.fromkeys(('合肥新桥机场','黄山屯溪机场','芜湖宣州机场','池州九华山机场','阜阳机场','安庆天柱山机场','亳州机场','蚌埠滕湖机场'),'airport')}
METRICS = {'scenic':'scenic_visits','rail_station':'rail_departures','airport':'airport_throughput'}
METRIC_ALIASES = {'station_passenger_departures':'rail_departures','airport_passenger_throughput':'airport_throughput'}
OPERATIONS = {
    '亳州机场': {'start':'2025-11-12','url':'https://www.mot.gov.cn/xinwen/jiaotongyaowen/202512/t20251226_4183970.html'},
    '蚌埠滕湖机场': {'start':'2026-04-29','url':'https://www.mot.gov.cn/xinwen/jiaotongyaowen/202604/t20260430_4204601.html'},
}
table = sa.Table('project_flow_data', Base.metadata,
    sa.Column('id', sa.String(36), primary_key=True),
    sa.Column('entity_id', sa.String(36), sa.ForeignKey('entities.id'), nullable=False),
    sa.Column('metric', sa.String(100), nullable=False),
    sa.Column('grain', sa.String(32), nullable=False),
    sa.Column('period_start', sa.Date, nullable=False), sa.Column('period_end', sa.Date, nullable=False),
    sa.Column('value', sa.Numeric(24, 4), nullable=False), sa.Column('unit', sa.String(40), nullable=False),
    sa.Column('data_kind', sa.String(32), nullable=False),
    sa.Column('source_id', sa.String(36), sa.ForeignKey('sources.id'), nullable=False),
    sa.Column('provenance', sa.JSON, nullable=False), sa.Column('updated_at', sa.DateTime, nullable=False),
    sa.UniqueConstraint('entity_id', 'metric', 'grain', 'period_start', 'period_end', name='uq_project_flow_period'),
    sa.CheckConstraint('value >= 0', name='project_flow_positive'),
    sa.CheckConstraint("data_kind IN ('published','estimated','simulated')", name='project_flow_kind'),
    sa.Index('ix_project_flow_entity_grain', 'entity_id', 'grain', 'period_start'))


def stable_id(*parts):
    return str(uuid5(NAMESPACE_URL, ':'.join(map(str, parts))))


def read_rows(store, entity_id=None, grain=None, start=None, end=None):
    with store.read() as u:
        if not sa.inspect(u.session.bind).has_table(table.name):
            return []
        query = sa.select(table)
        for col, value in [(table.c.entity_id, entity_id), (table.c.grain, grain)]:
            if value is not None: query = query.where(col == value)
        if start: query = query.where(table.c.period_start >= start)
        if end: query = query.where(table.c.period_end <= end)
        return [dict(r) for r in u.session.execute(query.order_by(table.c.period_start)).mappings()]


def point(row):
    return {'id': row['id'], 'metric': row['metric'], 'value': row['value'], 'unit': row['unit'],
            'grain': row['grain'], 'period_start': row['period_start'], 'period_end': row['period_end'],
            'quality_status': row['data_kind'], 'source_ids': [row['source_id']],
            'published_at': None, 'ingested_at': row['updated_at'].replace(tzinfo=timezone.utc),
            'methodology_version': VERSION, 'provenance': row['provenance']}


def seasonal_windows(year):
    # Analytical windows chosen for this project, not official school calendars.
    return [{'name': f'{year}年寒假', 'kind': 'winter', 'year': year,
             'start': f'{year}-01-01', 'end': f'{year}-02-{calendar.monthrange(year, 2)[1]}'},
            {'name': f'{year}年暑假', 'kind': 'summer', 'year': year,
             'start': f'{year}-07-01', 'end': f'{year}-08-31'}]


def allocate(total, weights):
    """Integer largest-remainder allocation preserves totals exactly."""
    if not sum(weights):
        if total: raise ValueError('没有运营日期，不能分配正客流')
        return [0]*len(weights)
    scale = total / sum(weights)
    raw = [w * scale for w in weights]
    values = [math.floor(v) for v in raw]
    for i in sorted(range(len(raw)), key=lambda i: (-(raw[i] - values[i]), i))[:total-sum(values)]:
        values[i] += 1
    return values


def build_package(output: Path, records, calendar_rows, reviewed_sources: list[dict], *, end=date(2026, 10, 7)):
    """Create one immutable JSON package plus CSV; generation needs no MySQL access."""
    start = date(2023, 1, 1)
    if not start <= end <= date.today(): raise ValueError('项目数据截止日期无效')
    output.mkdir(parents=True, exist_ok=True)
    windows = [{k: str(h[k]) for k in ('name', 'kind', 'year')} |
               {'start': str(h['start_date']), 'end': str(h['end_date'])}
               for h in calendar_rows if start <= h['start_date'] and h['end_date'] <= end]
    for year in range(start.year, end.year + 1):
        windows += [h for h in seasonal_windows(year) if date.fromisoformat(h['end']) <= end]
    anchors = []
    names = tuple(ENTITY_TYPES)
    seen = set()
    for r in records:
        if r['entity_name'] not in names or r['unit'] != '人次' or r['quality_status'] != 'verified': continue
        metric=METRIC_ALIASES.get(r['metric_code'],r['metric_code'])
        if metric != METRICS[ENTITY_TYPES[r['entity_name']]]: continue
        if r['period_start'] < start or r['period_end'] > end: continue
        key = (r['entity_name'], str(r['period_start']), str(r['period_end']), str(r['value']))
        if key in seen: continue
        seen.add(key)
        anchors.append({'entity': r['entity_name'], 'start': str(r['period_start']), 'end': str(r['period_end']),
                        'value': int(r['value']), 'grain': r['grain'], 'source_url': r['source_url'],
                        'title': r['source_title'], 'evidence': r['provenance'], 'data_kind': 'published'})
    for r in reviewed_sources:
        if r['entity'] in names and date.fromisoformat(r['end']) <= end:
            anchors.append(r | {'data_kind': 'published'})
    rows = []
    for name in names:
        entity_type=ENTITY_TYPES[name]
        metric=METRICS[entity_type]
        opening=date.fromisoformat(OPERATIONS.get(name,{}).get('start','1900-01-01'))
        available = [r for r in anchors if r['entity'] == name]
        year_totals = {int(r['start'][:4]): r['value'] for r in available if r['grain'] == 'year'}
        # Baseline scale is explicit, not presented as an observed yearly count.
        fallback_year = {'黄山风景区主景区': 4_900_000, '九华山': 3_100_000, '万佛湖': 1_050_000,
                         '合肥南站':30_000_000,'黄山北站':6_200_000,'芜湖站':18_000_000,'阜阳西站':7_000_000}.get(name)
        if fallback_year is None:
            monthly=[r for r in available if r['grain']=='month']
            latest_year=max((int(r['start'][:4]) for r in monthly),default=2025)
            recent=[r['value'] for r in monthly if int(r['start'][:4])==latest_year]
            fallback_year=round(sum(recent)/len(recent)*12/1.035**(latest_year-2025)) if recent else 300_000
        monthly_weights = [0.65, 0.75, 0.8, 1.1, 1.25, 0.9, 1.35, 1.4, 0.9, 1.2, 0.65, 0.55]
        if entity_type!='scenic': monthly_weights=[1.12,1.22,0.9,0.96,1.02,0.98,1.16,1.2,0.94,1.08,0.86,0.93]
        daily = {}
        for year in range(start.year, end.year+1):
            dates = [date(year,1,1)+timedelta(days=i) for i in range(366 if calendar.isleap(year) else 365)]
            total = year_totals.get(year)
            if total is None:
                previous = sorted(year_totals)
                total = round(year_totals[previous[-1]] * 1.035 ** (year-previous[-1])) if previous else round(fallback_year * 1.035 ** (year-2025))
            weights = []
            for d in dates:
                noise = int(hashlib.sha256(f'{VERSION}:{name}:{d}'.encode()).hexdigest()[:8],16) / 0xffffffff
                holiday = any(h['kind'] not in {'summer','winter'} and h['start'] <= str(d) <= h['end'] for h in windows)
                weights.append(0 if d<opening else monthly_weights[d.month-1] * (1.25 if d.weekday()>=5 else 0.9) * (1.8 if holiday else 1) * (0.94+0.12*noise))
            if not any(weights): total=0
            for d,v in zip(dates, allocate(total,weights)):
                if start<=d<=end: daily[d] = {'value': v, 'data_kind': 'estimated' if d<opening else 'simulated', 'anchor_ids': []}
        # Preserve exact nested constraints: allocate a larger original total only
        # over days not already fixed by a shorter original period.
        groups = defaultdict(list)
        for a in available: groups[(a['start'],a['end'])].append(a)
        constrained=set()
        for (a,b), sources in sorted(groups.items(), key=lambda pair: (date.fromisoformat(pair[0][1])-date.fromisoformat(pair[0][0])).days):
            days = [d for d in daily if a<=str(d)<=b]
            if not days: continue
            total = round(sum(s['value'] for s in sources)/len(sources))
            free=[d for d in days if d not in constrained and d>=opening]
            remaining=total-sum(daily[d]['value'] for d in days if d in constrained)
            if remaining<0 or (not free and remaining):
                raise ValueError('原始周期总量互相冲突，需要人工核对：'+name+a+b)
            values = allocate(remaining, [max(1,daily[d]['value']) for d in free]) if free else []
            for d,v in zip(free,values):
                daily[d] = {'value':v,'data_kind':'published' if len(days)==1 and len({s['value'] for s in sources})==1 else 'estimated',
                            'anchor_ids':[anchors.index(s) for s in sources]}
            constrained.update(days)
        def add(grain,a,b):
            days=[d for d in daily if a<=d<=b]
            if not days or len(days)!=(b-a).days+1: return
            exact=[s for s in available if s['start']==str(a) and s['end']==str(b)]
            kinds={daily[d]['data_kind'] for d in days}
            if exact:
                value=round(sum(s['value'] for s in exact)/len(exact))
                kind='published' if len({s['value'] for s in exact})==1 else 'estimated'
            else:
                value=sum(daily[d]['value'] for d in days)
                kind='simulated' if 'simulated' in kinds else 'estimated'
            rows.append({'entity_name':name,'metric':metric,'grain':grain,'period_start':str(a),'period_end':str(b),
                         'value':value,'unit':'人次','data_kind':kind,'provenance':{'version':VERSION,
                             'method':'original_period_mean' if exact else 'seeded_seasonality_with_exact_anchor_constraints',
                             **({'operation_start':str(opening),'operation_source':OPERATIONS[name]['url']} if name in OPERATIONS else {}),
                             'anchor_ids': sorted({anchors.index(s) for s in exact} | {i for d in days for i in daily[d]['anchor_ids']})}})
        for d in daily: add('day',d,d)
        monday = start+timedelta(days=(-start.weekday())%7)
        while monday+timedelta(days=6)<=end:
            add('week',monday,monday+timedelta(days=6));monday+=timedelta(days=7)
        cursor=start
        while cursor<=end:
            last=date(cursor.year,cursor.month,calendar.monthrange(cursor.year,cursor.month)[1])
            if last<=end: add('month',cursor,last)
            cursor=last+timedelta(days=1)
        for h in windows: add('holiday',date.fromisoformat(h['start']),date.fromisoformat(h['end']))
    payload={'version':VERSION,'start':str(start),'end':str(end),'generated_at':datetime.now(timezone.utc).isoformat(),
        'assumptions':{'user_authorized_simulation':True,'winter_window':'Jan 1 through last day of February; project analytical window, not a school calendar',
                       'transport_grains':'day/week/month; complete periods only; no positive civil passenger flow before official opening',
                       'operating_windows':OPERATIONS,
                       'summer_window':'July 1 through August 31','averaging':'Only same entity, metric, unit and exact period; never mix station/city/scenic or annual/daily values',
                       'simulation':'Deterministic seasonal/weekend/holiday weights; exact original period anchors are constraints; allocated subperiods are estimated, unanchored periods simulated'},
        'anchors':anchors,'holidays':windows,'rows':rows}
    manifest=output/'manifest.json'
    if manifest.is_file():
        previous=json.loads(manifest.read_text(encoding='utf-8'))
        if all(previous.get(k)==v for k,v in payload.items() if k!='generated_at'):
            payload['generated_at']=previous['generated_at']
    manifest.write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    with (output/'flow_data.csv').open('w',encoding='utf-8-sig',newline='') as f:
        fields=['entity_name','metric','grain','period_start','period_end','value','unit','data_kind']
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
        writer.writerows({k:r[k] for k in fields} for r in rows)
    return payload


def validate_package(path):
    payload=json.loads(Path(path).read_text(encoding='utf-8'))
    if payload['version']!=VERSION: raise ValueError('项目数据版本不匹配')
    seen=set()
    for r in payload['rows']:
        a,b=date.fromisoformat(r['period_start']),date.fromisoformat(r['period_end'])
        if a>b or a<date(2023,1,1) or b>date.fromisoformat(payload['end']): raise ValueError('数据周期越界')
        value=Decimal(str(r['value']))
        if r['data_kind'] not in {'published','estimated','simulated'} or not value.is_finite() or value<0 or value>=Decimal('1e20') or value!=int(value): raise ValueError('数值或数据类型无效')
        if r['entity_name'] not in ENTITY_TYPES or r['grain'] not in {'day','week','month','holiday'} or r['metric']!=METRICS[ENTITY_TYPES[r['entity_name']]] or r['unit']!='人次': raise ValueError('指标、单位或粒度无效')
        if any(i<0 or i>=len(payload['anchors']) for i in r['provenance']['anchor_ids']): raise ValueError('证据索引无效')
        key=tuple(r[k] for k in ('entity_name','metric','grain','period_start','period_end'))
        if key in seen: raise ValueError('数据周期重复')
        seen.add(key)
    return payload


def import_package(store, path):
    payload=validate_package(path)
    table.create(store.engine,checkfirst=True)
    now=datetime.now(timezone.utc);result=Counter()
    with Path(path).open('rb') as f:
        asset=store.persist_file(source_id=SOURCE_ID,original_name='manifest.json',stream=f)
    with store.write() as u:
        if not u.session.get(Source,SOURCE_ID):
            u.provenance.register_source(SourceCreate(id=SOURCE_ID,publisher='项目数据',title='文旅交通客流序列',url=SOURCE_URL,
                usage_note='本资料汇集公开统计、估算及模拟数据，具体来源和处理方式可在数据清单中查看。',
                update_frequency='manual',verification_status='pending_review'))
        ref=u.provenance.register_asset(AssetCreate(source_id=SOURCE_ID,url=SOURCE_URL,title='项目客流数据清单',retrieved_at=now,
            relative_path=asset['relative_path'],sha256=asset['sha256'],byte_size=asset['byte_size'],media_type='application/json',usage_note='保留数据类型、证据和生成参数'))
        u.session.get(Source,SOURCE_ID).title='文旅交通客流序列'
        u.session.get(Source,SOURCE_ID).usage_note='本资料汇集公开统计、估算及模拟数据，具体来源和处理方式可在数据清单中查看。'
        entities={e.name:e.id for e in u.session.scalars(sa.select(Entity).where(Entity.type.in_(['scenic','rail_station','airport'])))}
        existing={r['id']:dict(r) for r in u.session.execute(sa.select(table)).mappings()}
        for row in payload['rows']:
            if row['entity_name'] not in entities: raise ValueError('对象尚未登记：'+row['entity_name'])
            key=stable_id(VERSION,row['entity_name'],row['grain'],row['period_start'],row['period_end'])
            values={k:row[k] for k in ('metric','grain','unit','data_kind')}
            values.update(id=key,entity_id=entities[row['entity_name']],period_start=date.fromisoformat(row['period_start']),
                period_end=date.fromisoformat(row['period_end']),value=Decimal(str(row['value'])),source_id=SOURCE_ID,
                provenance=row['provenance'] | {'asset_id':ref['id'],'package_sha256':asset['sha256'],
                    'source_urls':sorted({payload['anchors'][i]['source_url'] for i in row['provenance']['anchor_ids']})},updated_at=now.replace(tzinfo=None))
            old=existing.get(key)
            if old:
                if all(old[k]==values[k] for k in values if k!='updated_at'): result['skipped']+=1;continue
                u.session.execute(table.update().where(table.c.id==key).values(**values));result['updated']+=1
            else:
                u.session.execute(table.insert().values(**values));result['inserted']+=1
        for h in payload['holidays']:
            if h['kind'] not in {'summer','winter'}: continue
            key=stable_id(VERSION,h['kind'],h['year'])
            if not u.session.get(Holiday,key):
                a,b=date.fromisoformat(h['start']),date.fromisoformat(h['end'])
                u.holidays.create_holiday({'id':key,'name':h['name'],'kind':h['kind'],'year':int(h['year']),
                    'start_date':a,'end_date':b,'days_count':(b-a).days+1,'region_code':'340000',
                    'scope_key':'CN:Anhui:project_analytical_window','definition_source_id':SOURCE_ID,'definition_asset_id':ref['id']})
    return {'status':'imported','counts':dict(result),'rows':len(payload['rows']),
            'kinds':dict(Counter(r['data_kind'] for r in payload['rows'])),'start':payload['start'],'end':payload['end']}
