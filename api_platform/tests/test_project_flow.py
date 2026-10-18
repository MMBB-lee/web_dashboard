"""Project package reproducibility, real-data precedence and read/import boundaries."""
from collections import defaultdict
from datetime import date
from pathlib import Path
import json
import pytest
import sqlalchemy as sa
from data_foundation.ingestion.project_flow import import_package,read_rows,validate_package,build_package,ENTITY_TYPES,METRICS,OPERATIONS
from app.services.native_series import query_series
from app.services.historical_forecast import run_history_forecast
from app.api.v1.forecasts import _to_series
from test_geography_native_series import database

PACKAGE=Path(__file__).resolve().parents[2]/'data_foundation/real_data/project_flow/manifest.json'


def test_import_is_idempotent_and_keeps_original_observations(database):
    with database.read() as u:
        original=u.session.execute(sa.text('SELECT id,value FROM observations ORDER BY id')).all()
    first=import_package(database._store if hasattr(database,'_store') else database.store,PACKAGE)
    assert first['rows']==24750
    with database.read() as u:
        assert u.session.execute(sa.text('SELECT id,value FROM observations ORDER BY id')).all()==original
    again=import_package(database._store if hasattr(database,'_store') else database.store,PACKAGE)
    assert again['counts']=={'skipped':24750}
    from data_foundation.ingestion.project_flow import SOURCE_ID
    source=database.get_source(SOURCE_ID)
    assert source['coverage_start']==date(2023,1,1)
    assert source['coverage_end']==date(2026,10,7)
    assert source['retrieved_at'].utcoffset() is not None
    assert '估算及模拟数据' in source['usage_note']
    entities=database.list_entities(page_size=100)['items']
    assert {e['name'] for e in entities}==set(ENTITY_TYPES)
    for entity in entities:
        metric=METRICS[entity['type']]
        series=query_series(entity['id'],metric,store=database)
        assert series['grain']=='month' and len(series['points'])==45
        assert series['points'][0]['period_start']=='2023-01-01'
        assert series['points'][-1]['period_end']=='2026-09-30'
        assert set(entity['available_grains'])>={'day','week','month','holiday'}
        prediction=run_history_forecast(_to_series(series['points']),'month',3)
        assert prediction['status']=='ready'
        assert set(p['quality_status'] for p in series['points']) <= {'verified','published','estimated','simulated'}
        weekly=query_series(entity['id'],metric,grain='week',store=database)
        assert weekly['points'][0]['period_start']=='2023-01-02'
        assert weekly['points'][-1]['period_end']=='2026-10-04'
        assert run_history_forecast(_to_series(weekly['points']),'week',4)['status']=='ready'
    from app.api.v1.dashboard import _build_cards
    cards,latest=_build_cards(database,entities,database.list_metrics(),'month',date(2026,10,18))
    assert all(p['period_end'].isoformat()=='2026-09-30' if isinstance(p['period_end'],date) else p['period_end']=='2026-09-30' for _,p in latest.values())
    definitions=database.list_holidays(region_code='340000')
    assert len([h for h in definitions if h['kind'] in {'summer','winter'}])==8
    wanfo=next(e for e in entities if e['name']=='万佛湖')
    actual=next(h for h in definitions if h['kind']=='may_day' and h['year']==2025)
    assert database.query_holiday_values(entity_id=wanfo['id'],metric='scenic_visits',holiday_id=actual['id'])['exact_total']['value']==111900
    winter=next(h for h in definitions if h['kind']=='winter' and h['year']==2023)
    result=database.query_project_holiday_values(entity_id=wanfo['id'],metric='scenic_visits',holiday_id=winter['id'])
    assert result['exact_total']['value']>0
    assert not result['scope_confirmation_required']
    assert result['exact_total']['quality_status'] in {'estimated','simulated'}
    assert not database.query_holiday_values(entity_id=wanfo['id'],metric='scenic_visits',holiday_id=winter['id'])['exact_total']


def test_exported_rollups_and_real_anchors_are_consistent():
    package=validate_package(PACKAGE)
    by_entity=defaultdict(list)
    for row in package['rows']: by_entity[row['entity_name']].append(row)
    for name,rows in by_entity.items():
        days={r['period_start']:r['value'] for r in rows if r['grain']=='day'}
        for row in rows:
            total=sum(value for day,value in days.items() if row['period_start']<=day<=row['period_end'])
            assert total==row['value'],(name,row)
        for anchor in package['anchors']:
            if anchor['entity']!=name: continue
            values=[a['value'] for a in package['anchors'] if a['entity']==name and a['start']==anchor['start'] and a['end']==anchor['end']]
            assert sum(value for day,value in days.items() if anchor['start']<=day<=anchor['end'])==round(sum(values)/len(values))
    assert any(a['start']=='2026-10-01' and a['value']==206600 for a in package['anchors'])
    assert not any(r.get('data_kind')=='verified' for r in package['rows'])


def test_new_airports_have_zero_civil_traffic_before_opening():
    package=validate_package(PACKAGE)
    for name,operation in OPERATIONS.items():
        opening=operation['start']
        days=[r for r in package['rows'] if r['entity_name']==name and r['grain']=='day']
        assert days and all(r['value']==0 for r in days if r['period_end']<opening)
        assert any(r['value']>0 for r in days if r['period_start']>=opening)


def test_package_rejects_invalid_quality_or_out_of_range_period(tmp_path):
    package=json.loads(PACKAGE.read_text(encoding='utf-8'))
    package['rows'][0]['data_kind']='verified'
    target=tmp_path/'bad.json';target.write_text(json.dumps(package),encoding='utf-8')
    with pytest.raises(ValueError): validate_package(target)
