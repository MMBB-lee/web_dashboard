"""Cached, provenance-preserving coordinate enrichment for imported entities.

Source coordinates are WGS84. Business coordinates are GCJ02, matching the
shipped DataV boundaries. Existing complete pairs are never overwritten.
"""
from __future__ import annotations
import json
import math
import os
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import sqlalchemy as sa
from .db.models import Entity


def wgs84_to_gcj02(lon, lat):
    lon, lat = float(lon), float(lat)
    x, y = lon - 105, lat - 35
    dlat = -100 + 2*x + 3*y + .2*y*y + .1*x*y + .2*math.sqrt(abs(x))
    dlat += (20*math.sin(6*x*math.pi) + 20*math.sin(2*x*math.pi))*2/3
    dlat += (20*math.sin(y*math.pi) + 40*math.sin(y/3*math.pi))*2/3
    dlat += (160*math.sin(y/12*math.pi) + 320*math.sin(y*math.pi/30))*2/3
    dlon = 300 + x + 2*y + .1*x*x + .1*x*y + .1*math.sqrt(abs(x))
    dlon += (20*math.sin(6*x*math.pi) + 20*math.sin(2*x*math.pi))*2/3
    dlon += (20*math.sin(x*math.pi) + 40*math.sin(x/3*math.pi))*2/3
    dlon += (150*math.sin(x/12*math.pi) + 300*math.sin(x/30*math.pi))*2/3
    rad = lat/180*math.pi
    magic = 1-.00669342162296594323*math.sin(rad)**2
    a = 6378245
    return (lon+dlon*180/(a/math.sqrt(magic)*math.cos(rad)*math.pi),
            lat+dlat*180/((a*(1-.00669342162296594323)/(magic*math.sqrt(magic)))*math.pi))


def valid_anhui_point(lon, lat):
    try:
        return math.isfinite(float(lon)) and math.isfinite(float(lat)) and 114.8 <= float(lon) <= 119.8 and 29.2 <= float(lat) <= 34.8
    except (ValueError, TypeError):
        return False


def normalize_name(name):
    return name.replace('国际', '').replace('风景区主景区', '').replace('风景名胜区','').replace('风景区', '').replace('景区', '').removesuffix('站')


def photon_candidate(payload, name, kind):
    allowed = {'airport': {('aeroway','aerodrome')},
               'rail_station': {('railway','station'),('building','train_station')},
               'scenic': {('leisure','park'),('tourism','attraction'),('natural','peak'),('natural','water'),('water','reservoir'),('boundary','protected_area'),('boundary','administrative')}}
    matches = []
    for feature in payload.get('features', []):
        props = feature.get('properties', {})
        coords = feature.get('geometry', {}).get('coordinates', [])
        if len(coords) != 2 or not valid_anhui_point(*coords):
            continue
        if props.get('countrycode') != 'CN' or props.get('state') not in {'安徽省', 'Anhui'}:
            continue
        if normalize_name(props.get('name', '')) != normalize_name(name):
            continue
        if (props.get('osm_key'), props.get('osm_value')) not in allowed.get(kind, set()):
            continue
        if props.get('osm_value') == 'administrative' and '风景区' not in props.get('name',''):
            continue
        osm_type = {'N':'node','W':'way','R':'relation'}.get(props.get('osm_type'))
        if not osm_type or not props.get('osm_id'):
            continue
        matches.append({'type':kind, 'longitude':coords[0], 'latitude':coords[1], 'crs':'WGS84',
            'source_name':'OpenStreetMap via Photon', 'source_url':f"https://www.openstreetmap.org/{osm_type}/{props['osm_id']}",
            'matched_name':props['name'], 'selection_rank':0 if props.get('osm_value')=='train_station' else 1,
            'retrieved_at':datetime.now(timezone.utc).isoformat()})
    if matches:
        rank = min(item['selection_rank'] for item in matches)
        matches = [item for item in matches if item['selection_rank']==rank]
    return matches[0] if len(matches) == 1 else None


def enrich_coordinates(store, *, allow_network=True, fetcher=None):
    registry = json.loads((Path(__file__).parent/'defaults/entity_coordinates.json').read_text(encoding='utf-8'))['entities']
    root = Path(store.data_root)
    cache_path = root/'geocoding-cache.json'
    try:
        cache = json.loads(cache_path.read_text(encoding='utf-8'))
    except (FileNotFoundError, ValueError):
        cache = {}
    with store.read() as uow:
        missing = [(e.id,e.name,e.type) for e in uow.session.scalars(sa.select(Entity).where(
            sa.or_(Entity.longitude.is_(None), Entity.latitude.is_(None)), Entity.type.in_(['airport','rail_station','scenic'])))]
    result = {'updated':0, 'unresolved':[], 'sources':{}}
    for entity_id, name, kind in missing:
        item = registry.get(name) or cache.get(name, {}).get('point')
        attempt = cache.get(name, {})
        fresh = time.time() - attempt.get('attempted_at', 0) < 7*86400
        if not item and allow_network and (fetcher or not fresh):
            try:
                if fetcher:
                    payload = fetcher(name,kind)
                else:
                    # One query per new name, exact POI/type/region match, cached for seven days.
                    endpoint = os.getenv('GEOCODING_PHOTON_URL', 'https://photon.komoot.io/api/').rstrip('/') + '/'
                    req = Request(endpoint+'?'+urlencode({'q':name,'limit':10,'bbox':'114.8,29.2,119.8,34.8'}),
                                  headers={'User-Agent':'AnhuiTourismProject/1.0 (coordinate enrichment)'})
                    with urlopen(req,timeout=12) as response:
                        payload = json.load(response)
                    time.sleep(1)
                item = photon_candidate(payload,name,kind)
                cache[name] = {'point':item, 'attempted_at':time.time()}
            except Exception as exc:
                cache[name] = {'point':None, 'attempted_at':time.time(), 'error':type(exc).__name__}
        if not item or item['type'] != kind or not valid_anhui_point(item['longitude'],item['latitude']):
            result['unresolved'].append(name)
            continue
        lon, lat = wgs84_to_gcj02(item['longitude'],item['latitude'])
        with store.write() as uow:
            row = uow.session.get(Entity,entity_id,with_for_update=True)
            if row.longitude is not None and row.latitude is not None:
                continue
            row.longitude = Decimal(str(round(lon,7)))
            row.latitude = Decimal(str(round(lat,7)))
            previous = row.location_note
            row.location_note = json.dumps({'coordinate_system':'GCJ02','original_coordinate':item,
                'previous_note':previous,'enriched_at':datetime.now(timezone.utc).isoformat()},ensure_ascii=False)
        result['updated'] += 1
        result['sources'][name] = item['source_url']
    if cache:
        root.mkdir(parents=True,exist_ok=True)
        temporary = cache_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(cache,ensure_ascii=False,indent=2),encoding='utf-8')
        temporary.replace(cache_path)
    return result
