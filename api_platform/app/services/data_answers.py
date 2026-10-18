"""Resolve ordinary data questions to verified original statistics, without SQL generation."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from decimal import Decimal
import re
from uuid import uuid4

from app.core.providers import get_c_store
from app.services.public_data import entity_data


def aliases(name: str, kind: str) -> set[str]:
    names = {name, name.replace("国际", "")}
    if name == "合肥新桥机场":
        names.update({"合肥机场", "新桥机场", "合肥新桥国际机场"})
    if name.endswith("风景区主景区"):
        base = name.removesuffix("风景区主景区")
        names.update({base + "景区", base + "风景区", base})
    return names


def answer_from_data(message: str, conversation_id: str | None, context: dict | None) -> dict | None:
    if context and context.get("metric"):
        return None  # Explicit numerical windows retain the existing completeness checks.
    response = {"conversation_id": str(conversation_id or uuid4()), "status": "answered",
                "citations": [], "used_metric_queries": [], "limitations": [], "metric_result": None}
    if re.fullmatch(r"\s*(你好|您好|嗨|hello|hi)[！!。.，,\s]*", message, re.I):
        return response | {"answer": "你好！可以询问机场吞吐量、车站发送量、景区历史接待数据，也可以通过右上角“知识库管理”补充资料。请尽量写明地点和统计时间。"}
    if re.fullmatch(r"\s*(好的|好|好呀|好啊|嗯|嗯嗯|哦|哦哦|明白了?|知道了|收到|行|可以|ok|okay|谢谢(你)?|多谢|感谢)[！!。.，,～~\s]*", message, re.I):
        return response | {"answer": "好的。需要查询时，告诉我地点、时间和想了解的内容即可。"}
    if not any(token in message for token in ("客流", "吞吐", "发送", "接待", "进山", "多少", "人数", "游客")):
        return None
    store = get_c_store()
    if store is None:
        return None
    from data_foundation.contracts import Page
    with store.read() as uow:
        entities = uow.catalog.list_entities(page=Page(page=1, page_size=100))["items"]
    ids = (context or {}).get("entity_ids") or []
    matches = [e for e in entities if (not ids or e["id"] in ids) and
               (any(alias in message for alias in aliases(e["name"], e["type"])) or bool(ids))]
    if not matches:
        return None
    years = {int(y) for y in re.findall(r"(?<!\d)(20\d{2})(?!\d)", message)}
    month_match = re.search(r"(20\d{2})\s*(?:年|[-/])\s*(\d{1,2})\s*(?:月|$)", message)
    grain_names = {"year": "年度", "quarter": "季度", "half_year": "半年", "month": "月度", "week": "周", "day": "日", "holiday": "节假日", "cumulative": "累计"}
    now = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    current = not years and any(token in message for token in ("现在", "实时", "今天", "当前"))
    lines, citations = [], []
    for entity in matches[:5]:
        facts = entity_data(entity["id"])["facts"]
        allowed = {"airport": {"airport_throughput", "airport_passenger_throughput"},
                   "rail_station": {"rail_departures", "station_passenger_departures"},
                   "scenic": {"scenic_visits", "scenic_mountain_entries"}}.get(entity["type"], set())
        facts = [f for f in facts if f["metric_code"] in allowed]
        if years:
            facts = [f for f in facts if f["period_start"].year in years]
        if month_match:
            year, month = map(int, month_match.groups())
            facts = [f for f in facts if f["grain"] == "month" and
                     (f["period_start"].year, f["period_start"].month) == (year, month)]
        if context and context.get("grain"):
            facts = [f for f in facts if f["grain"] == context["grain"]]
        for key, field, operator in (("period_start", "period_start", "ge"), ("period_end", "period_end", "le")):
            if context and context.get(key):
                from datetime import date
                try:
                    boundary = date.fromisoformat(context[key][:10])
                except ValueError:
                    return response | {"status": "insufficient_evidence", "answer": "时间格式无法识别，请使用 YYYY-MM-DD。"}
                facts = [f for f in facts if (f[field] >= boundary if operator == "ge" else f[field] <= boundary)]
        if current:
            facts = [f for f in facts if f["grain"] == "day" and str(f["period_start"]) == now]
        if "进山" in message:
            facts = [f for f in facts if f["metric_code"] == "scenic_mountain_entries"]
        if "春节" in message:
            facts = [f for f in facts if f["grain"] == "holiday" and f["period_start"].month in (1, 2)]
        elif "五一" in message:
            facts = [f for f in facts if f["grain"] == "holiday" and f["period_start"].month == 5]
        elif not month_match and facts:
            preferred = ("year" if years else "month") if entity["type"] == "airport" else ("day" if entity["type"] == "rail_station" else "year")
            same = [f for f in facts if f["grain"] == preferred]
            if same:
                facts = same
        # Never sum alternate sources, scopes or overlapping periods.
        count = 6 if any(word in message for word in ("趋势", "变化", "几个月", "历年")) else 1
        for fact in facts[:count]:
            number = format(Decimal(str(fact["value"])), ',f').rstrip('0').rstrip('.') if '.' in str(fact["value"]) else str(fact["value"])
            lines.append(f"{entity['name']}：{fact['period_start']} 至 {fact['period_end']}，{fact['metric_name']} {number} {fact['unit']}（{grain_names.get(fact['grain'], fact['grain'])}，已核验）。")
            citations.append({"type": "metric", "id": fact["id"], "title": fact["source_title"],
                              "url": fact["source_url"], "date": str(fact["published_at"]) if fact["published_at"] else None})
    if not lines:
        return response | {"status": "insufficient_evidence", "answer": "没有找到该地点在所询问时间、统计粒度下的已核验客流数据。历史年度、节假日或累计值不能作为当前实时人数。请在景区或交通页面查看原始统计资料和覆盖时间。"}
    return response | {"answer": "\n".join(lines), "citations": citations,
                       "limitations": ["以上为已导入资料中的历史统计值；“最近”表示库中最新可用统计期，不代表实时客流。不同周期和口径不合计。"]}
