"""Verify map scope, automatic enrichment and native chart periods."""
import copy
import json
from pathlib import Path
from uuid import uuid4
from decimal import Decimal
import pytest
import sqlalchemy as sa
from data_foundation import create_store, EntityCreate
from data_foundation.db import Base
from data_foundation.geography import enrich_coordinates, photon_candidate, valid_anhui_point
from data_foundation.ingestion.real_dataset import import_real_dataset
from data_foundation.ingestion.public_bridge import load_records, apply_import
from app.services.c_store import CStore
from app.services.native_series import query_series, capabilities
from app.api.v1.dashboard import _fetch_entities,_build_cards,_build_map
from datetime import date


@pytest.fixture(scope='module')
def database(tmp_path_factory):
    root=tmp_path_factory.mktemp('geo-native')
    native=create_store('sqlite+pysqlite:///'+(root/'data.sqlite3').as_posix(),data_root=root,allow_sqlite_for_tests=True)
    Base.metadata.create_all(native.engine)
    project=Path(__file__).resolve().parents[2]
    manifest=project/'data_foundation/real_data/2026-10-12-v2/manifest.json'
    import_real_dataset(native,manifest)
    apply_import(native,load_records(project/'爬取工具/data/local',manifest))
    yield CStore(native)
    native.dispose()


def test_province_contains_every_city_and_all_map_coordinates(database):
    entities=_fetch_entities(database,'340000')
    assert len(entities)==15
    cards,points=_build_cards(database,entities,database.list_metrics(),'month',date(2026,10,18))
    assert len(_build_map(entities,points))==15
    assert all(point is not None for metric,point in points.values())
    assert all(e['longitude'] and e['latitude'] for e in entities)
    assert all(c.value is not None for c in cards)


def test_native_periods_have_values_and_are_never_disaggregated(database):
    entities=_fetch_entities(database,'340000')
    huangshan=next(e for e in entities if e['name']=='黄山风景区主景区')
    annual=query_series(huangshan['id'],'scenic_visits',store=database)
    assert annual['grain']=='year' and len(annual['points'])==3
    assert annual['points'][-1]['value']==5168000
    monthly=query_series(huangshan['id'],'scenic_visits',grain='month',store=database)
    assert monthly['points']==[]
    wanfo=next(e for e in entities if e['name']=='万佛湖')
    holiday=query_series(wanfo['id'],'scenic_visits',store=database)
    assert holiday['grain']=='holiday' and len(holiday['points'])==1
    assert holiday['points'][0]['value']==111900
    assert holiday['points'][0]['period_start']=='2025-05-01'


def test_real_holiday_calendar_single_total_and_missing_periods(database, client, monkeypatch):
    monkeypatch.setattr('app.api.v1.holidays.get_c_store', lambda: database)
    client.post('/api/v1/auth/login', json={'username': 'admin1', 'password': 'admin-pass-123'})
    response = client.get('/api/v1/holidays', params={'region_code': '340000'})
    assert response.status_code == 200
    holidays = response.json()['data']
    assert len(holidays) == 26  # National holidays apply to Anhui.
    ids = { (h['year'], h['kind']): h['id'] for h in holidays }
    entities = _fetch_entities(database, '340000')
    wanfo = next(e for e in entities if e['name'] == '万佛湖')
    params = {'entity_id': wanfo['id'], 'holiday_ids': ids[2025, 'may_day'], 'mode': 'period_total'}
    total = client.get('/api/v1/holidays/statistics', params=params)
    assert total.status_code == 200, total.text
    assert total.json()['data']['items'][0]['value'] == 111900
    assert total.json()['data']['items'][0]['source_ids']
    assert total.json()['data']['comparable']
    daily = client.get('/api/v1/holidays/statistics', params=params | {'mode': 'daily_average'}).json()['data']
    assert daily['items'][0]['value'] == 22380
    assert daily['items'][0]['unit'] == '人次/日'
    assert daily['items'][0]['missing_days'] == 5  # No fabricated daily observations.
    missing = client.get('/api/v1/holidays/statistics', params=params | {
        'holiday_ids': ids[2025, 'may_day'] + ',' + ids[2024, 'may_day']}).json()['data']
    assert not missing['comparable']
    assert [i['value'] for i in missing['items']] == [111900, None]
    indexed = client.get('/api/v1/holidays/statistics', params=params | {'mode': 'day_index'}).json()['data']
    assert indexed['items'][0]['value'] is None
    assert not indexed['comparable']
    jiuhua = next(e for e in entities if e['name'] == '九华山')
    spring = client.get('/api/v1/holidays/statistics', params=params | {
        'entity_id': jiuhua['id'], 'holiday_ids': ids[2025, 'spring_festival']}).json()['data']
    assert spring['items'][0]['value'] == 471000
    assert not client.get('/api/v1/holidays', params={'kind': 'summer'}).json()['data']


def test_each_available_metric_and_grain_can_be_read(database):
    for entity in _fetch_entities(database,'340000'):
        for item in capabilities(database,entity):
            series=query_series(entity['id'],item['metric'],grain=item['grain'],store=database)
            assert series['points'],(entity['name'],item)
            assert all(p['quality_status']=='verified' and p['source_ids'] for p in series['points'])


def test_coordinate_lookup_rejects_wrong_type_region_and_ambiguous_names():
    point={'type':'Feature','properties':{'name':'测试景区','osm_type':'W','osm_id':123,
        'osm_key':'leisure','osm_value':'park','countrycode':'CN','state':'安徽省'},
        'geometry':{'type':'Point','coordinates':[117.1,31.1]}}
    assert photon_candidate({'features':[point]},'测试景区','scenic')
    wrong=copy.deepcopy(point);wrong['properties']['state']='江苏省'
    assert photon_candidate({'features':[wrong]},'测试景区','scenic') is None
    assert photon_candidate({'features':[point]},'测试景区','rail_station') is None
    assert photon_candidate({'features':[point,point]},'测试景区','scenic') is None
    assert not valid_anhui_point(114.76027778,31.30388889)


def test_new_entities_automatically_get_cached_coordinates_without_overwriting(database):
    identity=str(uuid4())
    with database.write() as u:
        u.catalog.create_entity(EntityCreate(identity,'自动补全测试景区','scenic','340100'))
    calls=[]
    def provider(name,kind):
        calls.append(name)
        return {'features':[{'properties':{'name':name,'osm_type':'W','osm_id':123,'osm_key':'leisure',
            'osm_value':'park','countrycode':'CN','state':'安徽省'},'geometry':{'coordinates':[117.1,31.1]}}]}
    result=enrich_coordinates(database,fetcher=provider)
    assert result['updated']==1 and calls==['自动补全测试景区']
    with database.write() as u:
        from data_foundation.db.models import Entity
        e=u.session.get(Entity,identity)
        assert e.longitude is not None and e.latitude is not None
        assert json.loads(e.location_note)['coordinate_system']=='GCJ02'
        e.longitude=Decimal('117.2')
    assert enrich_coordinates(database,fetcher=provider)['updated']==0
    with database.read() as u:
        assert u.session.get(Entity,identity).longitude==Decimal('117.2')


def test_import_of_new_annual_only_place_registers_and_geocodes_it(tmp_path,monkeypatch):
    from data_foundation import geography
    native=create_store('sqlite+pysqlite:///'+(tmp_path/'new.sqlite3').as_posix(),data_root=tmp_path,allow_sqlite_for_tests=True)
    Base.metadata.create_all(native.engine)
    project=Path(__file__).resolve().parents[2]
    rows=load_records(project/'爬取工具/data/local',project/'data_foundation/real_data/2026-10-12-v2/manifest.json')
    row=copy.deepcopy(next(r for r in rows if r['grain']=='year' and r['entity_type']=='scenic' and r['metric_code']=='scenic_visits'))
    row.update(id='f'*64,entity_key='future:scenic',entity_name='未来新景区')
    row['_entity']={**row['_entity'],'name':'未来新景区','id':'future:scenic','region_code':'340100'}
    original=geography.enrich_coordinates
    calls=[]
    def provider(name,kind):
        calls.append(name)
        return {'features':[{'properties':{'name':name,'osm_type':'W','osm_id':456,'osm_key':'leisure',
            'osm_value':'park','countrycode':'CN','state':'安徽省'},'geometry':{'coordinates':[117.1,31.1]}}]}
    monkeypatch.setattr(geography,'enrich_coordinates',lambda store,**kwargs:original(store,allow_network=True,fetcher=provider))
    result=apply_import(native,[row])
    assert result['coordinates']['updated']==1 and calls==['未来新景区']
    from data_foundation.db.models import Entity,Observation
    with native.read() as u:
        entity=u.session.scalar(sa.select(Entity).where(Entity.name=='未来新景区'))
        assert entity.longitude is not None and entity.latitude is not None
        assert u.session.scalar(sa.select(sa.func.count()).select_from(Observation))==0
    native.dispose()
