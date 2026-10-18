"""Read original public statistics without inventing finer statistical periods."""
from __future__ import annotations

from uuid import UUID
import sqlalchemy as sa

from app.core.errors import AppError, CODE_BAD_REQUEST, CODE_NOT_FOUND
from app.core.providers import get_c_store

METRIC_NAMES = {
    "scenic_visits": "景区接待人次", "scenic_mountain_entries": "进山人数（原始口径）",
    "airport_throughput": "机场旅客吞吐量", "airport_passenger_throughput": "机场旅客吞吐量",
    "rail_departures": "铁路发送量", "station_passenger_departures": "铁路发送量",
}


def entity_data(entity_id: str, include_pending: bool = False) -> dict:
    from data_foundation.db.models import Entity, Observation
    from data_foundation.db.public_data import public_data_facts as facts
    try:
        UUID(entity_id)
    except ValueError:
        raise AppError(400, CODE_BAD_REQUEST, "对象 ID 不是合法 UUID") from None
    store = get_c_store()
    with store.read() as uow:
        entity = uow.session.get(Entity, entity_id)
        if entity is None:
            raise AppError(404, CODE_NOT_FOUND, "对象不存在")
        available = uow.session.execute(sa.select(
            Observation.metric_code.label("metric"), Observation.grain,
            sa.func.min(Observation.period_start).label("start"),
            sa.func.max(Observation.period_end).label("end"), sa.func.count().label("count")
        ).where(Observation.entity_id == entity_id, Observation.quality_status == "verified")
          .group_by(Observation.metric_code, Observation.grain)).mappings().all()
        rows = []
        if sa.inspect(uow.session.bind).has_table("public_data_facts"):
            query = sa.select(facts).where(facts.c.entity_name == entity.name)
            if not include_pending:
                query = query.where(facts.c.quality_status == "verified", facts.c.qualifier.is_(None))
            rows = uow.session.execute(query.order_by(facts.c.period_end.desc(), facts.c.id).limit(500)).mappings().all()
        result = {"entity": {"id": entity.id, "name": entity.name, "type": entity.type},
                  "available_series": [dict(r) for r in available], "facts": []}
        seen = set()
        for row in rows:
            # Same normalized metric/value/period/URL in the two imports is one display item.
            metric = {"airport_passenger_throughput": "airport_throughput",
                      "station_passenger_departures": "rail_departures"}.get(row["metric_code"], row["metric_code"])
            key = tuple(str(x) for x in (metric, row["grain"], row["period_start"], row["period_end"],
                                         row["value"], row["unit"], row["source_url"], row["quality_status"]))
            if key in seen:
                continue
            seen.add(key)
            result["facts"].append({k: row[k] for k in (
                "id", "metric_code", "scope", "grain", "period_start", "period_end", "value", "unit",
                "quality_status", "qualifier", "source_url", "source_title", "published_at")} | {
                    "metric_name": METRIC_NAMES.get(row["metric_code"], row["metric_code"])})
    from .native_series import capabilities
    known = {(r['metric'],r['grain']):r for r in result['available_series']}
    for row in capabilities(store,result['entity']):
        key=(row['metric'],row['grain'])
        if key in known:
            previous=known[key]
            previous['start']=min(previous['start'],row['start']);previous['end']=max(previous['end'],row['end'])
            previous['count']=max(previous['count'],row['count'])
        else:
            result['available_series'].append(row);known[key]=row
    return result
