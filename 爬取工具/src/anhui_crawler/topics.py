"""Reviewed, article-specific scenic and single-station actuals.

Different articles remain archived until separately reviewed. No inferred years,
station-group allocation, calendar-day disaggregation or forecast promotion.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import re

from lxml import html

from .bulletin import compact
from .common import CrawlerError

VERSION = "reviewed-topics-1"
CALENDAR_SOURCE = "https://www.gov.cn/zhengce/zhengceku/202411/content_6986383.htm"
CLASS = lambda name: f'//*[contains(concat(" ",normalize-space(@class)," ")," {name} ")]'
RULES = {
    "rail_station-huangshan-north-ahnews": {"entity": "rail:huangshan-north", "title": "轨道上的文旅协奏曲", "body": CLASS("content"),
        "published": "2025-07-21", "date": "ahnews", "grain": "cumulative", "period": ("2025-01-01", "2025-06-30"),
        "native_grain": "half_year", "context": ["今年1月至6月", "黄山市的6个高铁站"],
        "patterns": [(r"其中,黄山北站共发送旅客(?P<number>\d+(?:\.\d+)?)万人次", None, None)]},
    "scenic-wanfohu": {"entity": "scenic:wanfohu", "title": "舒城“五一”文旅热力值拉满", "body": "//*[@id='J_content']",
        "published": "2025-05-06", "date": "meta", "grain": "holiday", "period": ("2025-05-01", "2025-05-05"),
        "patterns": [(r"万佛湖景区假期五天累计接待游客(?P<number>\d+(?:\.\d+)?)万人次", None, None)]},
    "scenic-jiuhuashan": {"entity": "scenic:jiuhuashan", "title": "九华山风景区2025年春节假日旅游市场安全平稳有序", "body": "//*[@id='zoom']",
        "published": "2025-02-05", "date": "meta", "grain": "holiday", "period": ("2025-01-28", "2025-02-04"),
        "patterns": [(r"春节期间,接待游客(?P<number>\d+(?:\.\d+)?)万人次", None, None)]},
    "scenic-langyashan": {"entity": "scenic:langyashan", "title": "琅琊山景区2025年国庆假期圆满收官", "body": CLASS("article") + "/div[@class='bd']",
        "published": "2025-10-08", "date": "langya", "grain": "holiday", "period": ("2025-10-01", "2025-10-08"),
        "patterns": [(r"8天内吸引约(?P<number>\d+(?:\.\d+)?)万人次", "约", None)]},
    "rail_station-hefei-south": {"entity": "rail:hefei-south", "title": "合肥南站创春运单日发送旅客新高", "body": "//*[@id='abody']",
        "published": "2025-02-17", "date": "cri", "grain": "day", "period": ("2025-02-15", "2025-02-15"),
        "context": ["2月15日", "合肥直属站", "合肥日报"],
        "patterns": [(r"其中,合肥南站发送旅客(?P<number>\d+)人次", None, None)]},
    "rail_station-fuyang-west": {"entity": "rail:fuyang-west", "title": "高铁出行添便利", "body": "//*[@id='ozoom']",
        "published": "2026-03-01", "date": "people", "grain": "day", "period": ("2026-02-24", "2026-02-24"),
        "patterns": [(r"阜阳西站2月24日发送旅客(?P<number>\d+(?:\.\d+)?)万人次", None, None)]},
    "rail_station-wuhu": {"entity": "rail:wuhu", "title": "芜湖铁路多项出行数据创新高", "body": CLASS("j-fontContent"),
        "published": "2025-04-07", "date": "meta", "grain": "day", "period": ("2025-04-06", "2025-04-06"),
        "context": ["4月6日", "芜湖车务段"],
        "patterns": [(r"其中,芜湖站发送旅客(?P<number>\d+(?:\.\d+)?)万人次", None, None),
                     (r"芜湖站4月4日清明节当天发送旅客(?P<number>\d+(?:\.\d+)?)万人次", None, "2025-04-04"),
                     (r"4月5日,芜湖站发送旅客也超过(?P<number>\d+(?:\.\d+)?)万人次", "超过", "2025-04-05")]},
}


def parse_topic(raw, source_id, entities):
    rule = RULES.get(source_id)
    if rule is None:
        return None
    try:
        tree = html.fromstring(raw.decode("utf-8"))
    except UnicodeDecodeError:
        tree = html.fromstring(raw.decode("gb18030"))
    titles = tree.xpath("//title/text()")
    if len(titles) != 1 or compact(rule["title"]) not in compact(titles[0]):
        return None
    bodies = tree.xpath(rule["body"])
    if len(bodies) != 1:
        raise CrawlerError("Reviewed topic content container changed or ambiguous")
    body = bodies[0]
    if rule["date"] == "meta":
        dates = tree.xpath('//meta[translate(@name,"ABCDEFGHIJKLMNOPQRSTUVWXYZ","abcdefghijklmnopqrstuvwxyz")="pubdate"]/@content')
    elif rule["date"] == "cri":
        dates = tree.xpath('//*[@id="apublishtime"]/text()')
    elif rule["date"] == "people":
        dates = [re.sub(r"年|月", "-", s.strip()).replace("日", "") for s in tree.xpath(CLASS("newstime") + "/text()")]
        dates = [s[:10] for s in dates if re.match(r"\d{4}-\d{2}-\d{2}", s)]
        dates = sorted(set(dates))
    elif rule['date'] == 'ahnews':
        containers = tree.xpath(CLASS('newsdatetit'))
        dates = re.findall(r'(\d{4})年(\d{2})月(\d{2})日', containers[0].text_content()) if len(containers) == 1 else []
        dates = ['-'.join(parts) for parts in dates]
    else:
        dates = re.findall(r"发布日期：(\d{4}-\d{2}-\d{2})", tree.text_content())
    if len(dates) != 1 or dates[0][:10] != rule["published"]:
        raise CrawlerError("Reviewed topic publication date requires review")
    timestamp = datetime.fromisoformat(dates[0].strip()).replace(tzinfo=timezone(timedelta(hours=8))).isoformat()
    entity = next((e for e in entities if e["id"] == rule["entity"] and source_id in e["source_ids"]), None)
    if entity is None:
        raise CrawlerError("Topic entity/source mapping requires review")
    full_text = compact(body.text_content())
    if any(compact(word) not in full_text for word in rule.get("context", [])):
        raise CrawlerError("Topic year/date/scope context changed")
    output = []
    for pattern, qualifier, override in rule["patterns"]:
        found = [(p, m) for p in body.xpath('.//p[not(ancestor::table)]') for m in re.finditer(pattern, compact(p.text_content()))]
        if len(found) != 1:
            raise CrawlerError("Expected one reviewed topic actual; changed wording requires review")
        paragraph, match = found[0]
        # Restrict forecast rejection to the metric's clause, not a later clause
        # reporting an unrelated 40-day forecast in the same paragraph.
        text = compact(paragraph.text_content())
        left = max(text.rfind(s, 0, match.start()) for s in "。;；") + 1
        right = min([i for s in "。;；" if (i := text.find(s, match.end())) >= 0] or [len(text)])
        if any(word in text[left:right] for word in ("预计", "预测", "计划", "力争")):
            raise CrawlerError("Forecast cannot enter topic actuals")
        start, end = (override, override) if override else rule["period"]
        value = Decimal(match["number"]) * (10000 if "万人次" in match[0] else 1)
        scenic = entity["type"] == "scenic"
        output.append({"entity_id": entity["id"], "metric": "scenic_visits" if scenic else "station_passenger_departures",
            "scope": "scenic_reported_visits" if scenic else "single_station_departures", "grain": rule["grain"],
            "period_start": date.fromisoformat(start), "period_end": date.fromisoformat(end), "value": value,
            "unit": "人次", "quality": "candidate" if qualifier or source_id == "scenic-langyashan" else "validated",
            "qualifier": qualifier, "original_value": match["number"] + ("万人次" if "万人次" in match[0] else "人次"),
            "locator": {"article_title": titles[0], "content_selector": rule["body"],
                "xpath": tree.getroottree().getpath(paragraph), "paragraph": paragraph.text_content().strip(),
                "matched_text": match[0], "publication_date_rule": rule["date"],
                "period_evidence": CALENDAR_SOURCE if scenic else "explicit article reporting date and reviewed publication year",
                "normalization": "万人次×10000；不分摊、不由预计值推算", "statistical_status": "published_actual_not_final_audit"},
            "parser_version": VERSION})
        if rule.get('native_grain'):
            output[-1]['locator']['reported_native_grain'] = rule['native_grain']
    return {"published_at": timestamp, "records": output}


def capabilities():
    return {"parser_version": VERSION, "reviewed_articles_only": True,
            "sources": [{"source_id": sid, "title_contains": rule["title"], "period": rule["period"]} for sid, rule in RULES.items()]}
