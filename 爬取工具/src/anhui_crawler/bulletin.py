"""Reviewed city-bulletin rules, separate from the immutable source registry.

Only article metadata plus one source-specific content container is trusted.
These are annual city statistics, never station counts or monthly estimates.
"""
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
import re
import unicodedata

from lxml import html

from .common import CrawlerError

PARSER_VERSION = "city-bulletin-annual-1"
NUMBER = r"(?P<number>\d{1,12}(?:\.\d{1,6})?)"
PASSENGER_UNIT = r"(?P<unit>万人次|万人)"


def flow(label, metric="passenger_transport_volume", scope="municipal_road_transport"):
    return (label + NUMBER + PASSENGER_UNIT + r"(?=[,。;])", metric, scope, "year", "人次")


def stock(label, metric, unit):
    return (label + NUMBER + r"(?P<unit>" + unit + r")(?=[,。;])",
            metric, "municipal_year_end_inventory", "day", unit)


CONTENT = '//*[contains(concat(" ", normalize-space(@class), " "), " j-fontContent ")]'
RULES = {
    "city-anqing": ("安庆市", "//*[@id='J_content']", [
        flow("公路旅客运输量"), stock("年末拥有星级宾馆", "star_rated_hotels", "家"),
        stock("年末有A级及以上旅游景区", "a_rated_scenic_sites", "处")]),
    "city-chizhou": ("池州市", "//*[@id='zoom']", [
        stock(r"年末共有A级及以上旅游景区\(点\)", "a_rated_scenic_sites", "个")]),
    "city-huaibei": ("淮北市", "//*[@id='zoom']", [flow("全年公路客运量")]),
    "city-huainan": ("淮南市", CONTENT, [
        flow("公路旅客运输量"), stock("星级饭店", "star_rated_hotels", "家")]),
    "city-luan": ("六安市", "//*[@id='J_content']", [flow("公路客运量"),
        flow("水上客运量", scope="municipal_water_transport")]),
    "city-suzhou": ("宿州市", "//*[@id='zoom']", [flow("全年公路旅客运输量")]),
    "city-tongling": ("铜陵市", "//*[@id='zoomcon']", [flow("公路旅客运输量"),
        flow("全年铁路旅客发送量", "rail_passenger_departures", "municipal_rail_not_single_station")]),
    "city-wuhu": ("芜湖市", CONTENT, [flow("全年公路客运量"),
        flow("铁路旅客发送量", "rail_passenger_departures", "municipal_rail_not_single_station"),
        stock("星级饭店", "star_rated_hotels", "家"),
        stock(r"A级及以上旅游景点\(区\)", "a_rated_scenic_sites", "处")]),
    "city-xuancheng": ("宣城市", "//*[@id='zoom']", [
        flow("旅客运输量", scope="municipal_road_and_water_excluding_rail")]),
}


def capabilities():
    return {"parser_version": PARSER_VERSION, "mode": "offline_reviewed_bulletin_ingestion",
            "automatic_network_discovery": False,
            "sources": [{"source_id": sid, "city": rule[0],
                         "metrics": [{"metric": r[1], "scope": r[2], "grain": r[3], "unit": r[4]}
                                     for r in rule[2]]} for sid, rule in RULES.items()]}


def compact(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))


def parse_bulletin(raw, source_id, entities):
    """Return None for an entry/index page; recognized articles fail closed."""
    if source_id not in RULES:
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("gb18030")
    tree = html.fromstring(text)
    titles = tree.xpath('//meta[translate(@name,"ABCDEFGHIJKLMNOPQRSTUVWXYZ","abcdefghijklmnopqrstuvwxyz")="articletitle"]/@content')
    if not titles:
        return None  # No inferred article facts from navigation or search snippets.
    city, selector, rules = RULES[source_id]
    if len(titles) != 1:
        raise CrawlerError("Ambiguous bulletin article title metadata")
    title = compact(titles[0])
    match = re.fullmatch(r"(?:" + city + r"(20\d{2})年|(20\d{2})年" + city + r")国民经济和社会发展统计公报(?:\[1\])?", title)
    if not match:
        raise CrawlerError("Article is not the reviewed city's annual statistical bulletin")
    year = int(next(g for g in match.groups() if g))
    dates = tree.xpath('//meta[translate(@name,"ABCDEFGHIJKLMNOPQRSTUVWXYZ","abcdefghijklmnopqrstuvwxyz")="pubdate"]/@content')
    if len(dates) != 1:
        raise CrawlerError("Bulletin requires one official publication timestamp")
    published = datetime.fromisoformat(dates[0].strip())
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone(timedelta(hours=8)))
    if published.date() <= date(year, 12, 31):
        raise CrawlerError("Full-year bulletin published before the reporting year ended")
    bodies = tree.xpath(selector)
    if len(bodies) != 1:
        raise CrawlerError("Bulletin content container changed or is ambiguous")
    body = bodies[0]
    for node in body.xpath('.//script | .//style | .//nav | .//aside'):
        node.drop_tree()
    opening = re.search(r"(20\d{2})年[,是]", compact(body.text_content())[:800])
    if not opening or int(opening[1]) != year:
        raise CrawlerError("Article title and opening reporting year disagree or require review")
    paragraphs = [(p, compact(p.text_content())) for p in body.xpath('.//p[not(ancestor::table)]')]
    if source_id == "city-xuancheng" and not any(
            "旅客运输周转量为公路和水运合计" in s and "不含铁路" in s for _, s in paragraphs):
        raise CrawlerError("Road/water aggregate requires its explicit excluding-rail scope footnote")
    matches = [e for e in entities if e["id"] == source_id.replace("city-", "city:", 1)
               and e["type"] == "city" and source_id in e["source_ids"]]
    if len(matches) != 1:
        raise CrawlerError("Bulletin entity/source mapping requires review")
    records = []
    for pattern, metric, scope, grain, unit in rules:
        found = [(p, s, m) for p, s in paragraphs for m in re.finditer(pattern, s)]
        if len(found) != 1:
            raise CrawlerError("Expected one exact bulletin metric: " + metric + "/" + scope)
        paragraph, paragraph_text, number = found[0]
        if any(word in paragraph_text for word in ("预计", "预测", "计划", "力争", "约", "余", "超过", "以上")):
            # 'A级及以上' is a classification, not an approximate numeric value.
            cleaned = paragraph_text.replace("A级及以上", "A级")
            if any(word in cleaned for word in ("预计", "预测", "计划", "力争", "约", "余", "超过", "以上")):
                raise CrawlerError("Forecast or qualified bulletin paragraph requires manual review")
        if grain == "year" and "全年" not in paragraph_text:
            raise CrawlerError("Flow metric requires an explicit full-year paragraph")
        if grain == "day" and "年末" not in paragraph_text:
            raise CrawlerError("Inventory requires explicit year-end wording")
        value = Decimal(number["number"])
        source_unit = number["unit"]
        if grain == "year":
            value *= 10000
        if value != int(value):
            raise CrawlerError("Passenger/inventory counts require integral normalized values")
        records.append({"entity_id": matches[0]["id"], "metric": metric, "scope": scope,
            "grain": grain, "period_start": date(year, 1, 1) if grain == "year" else date(year, 12, 31),
            "period_end": date(year, 12, 31), "value": value, "unit": unit,
            "quality": "validated", "qualifier": None,
            "original_value": number["number"] + source_unit,
            "locator": {"article_title": titles[0], "content_selector": selector,
                "xpath": tree.getroottree().getpath(paragraph), "paragraph": paragraph.text_content().strip(),
                "matched_text": number[0], "source_unit": source_unit,
                "normalization": "万乘10000；客运人/人次是运输计数，不是常住人口" if grain == "year" else "年末存量，保留原单位",
                "statistical_status": "official_bulletin_not_independently_audited"},
            "parser_version": PARSER_VERSION})
    return {"published_at": published.isoformat(), "records": records}
