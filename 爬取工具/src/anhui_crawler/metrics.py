"""Versioned metric contracts. Registry aliases never rewrite existing facts."""
from .common import CrawlerError

VERSION = "anhui-crawler-metrics-1"
TARGETS = {
    "airport_passenger_throughput": ["airport_passenger_throughput"],
    "city_tourism_visits": ["city_tourism_visits"],
    "city_tourism_expenditure": ["city_tourism_expenditure", "city_tourism_revenue"],
    "scenic_site_count": ["a_rated_scenic_sites"],
    "hotel_count": ["star_rated_hotels"],
    "city_transport_passengers": ["passenger_transport_volume", "rail_passenger_departures"],
    "scenic_visits": ["scenic_visits", "scenic_mountain_entries"],
    "rail_departures": ["station_passenger_departures"],
}
CONTRACTS = {
    "scenic_mountain_entries": {"entity": "scenic:", "unit": "人", "scopes": ["scenic_mountain_entries"], "integer": True, "grains": ["year"]},
    "scenic_visits": {"entity": "scenic:", "unit": "人次", "scopes": ["scenic_reported_visits", "scenic_mountain_entries"], "integer": True},
    "station_passenger_departures": {"entity": "rail:", "unit": "人次", "scopes": ["single_station_departures"], "integer": True},
    "city_tourism_visits": {"entity": "city:", "unit": "人次", "scopes": ["municipal_tourism_visits", "municipal_domestic_tourism_visits", "municipal_inbound_tourism_visits"], "integer": True},
    "city_tourism_expenditure": {"entity": "city:", "unit": "元", "scopes": ["municipal_tourism_expenditure"], "integer": False},
    "city_tourism_revenue": {"entity": "city:", "unit": "元", "scopes": ["municipal_tourism_revenue"], "integer": False},
}
KNOWN = {name for names in TARGETS.values() for name in names}


def validate_metric(record, value):
    if record["metric"] not in KNOWN:
        raise CrawlerError("Unknown metric; add a reviewed metric contract first")
    spec = CONTRACTS.get(record["metric"])
    if spec and (not record["entity_id"].startswith(spec["entity"]) or record["unit"] != spec["unit"] or
                 record["scope"] not in spec["scopes"] or (spec["integer"] and value != int(value)) or
                 spec.get("grains") and record["grain"] not in spec["grains"]):
        raise CrawlerError("Metric entity, scope, unit or count precision requires review")


def dictionary():
    return {"schema_version": VERSION, "target_mapping": TARGETS, "additional_contracts": CONTRACTS,
            "existing_city_and_airport_contracts": "storage.validate_fact; preserved without renaming"}
