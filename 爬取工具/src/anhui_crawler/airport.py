"""Strict monthly passenger-column adapter; never derives December from totals."""
from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal
import re
import unicodedata

from .common import CrawlerError

PARSER_VERSION = "caac-east-airports-monthly-1"
REQUIRED = {"airport:HFE", "airport:TXN", "airport:FUG", "airport:WHA", "airport:AQG", "airport:JUH"}


def parse_pages(pages, period, entities):
    if not re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", period):
        raise CrawlerError("Explicit YYYY-MM statistical period required")
    year, month = map(int, period.split("-"))
    facts = []
    seen = set()
    airport_entities = [e for e in entities if e["type"] == "airport"]
    for page_no, raw in enumerate(pages, 1):
        text = unicodedata.normalize("NFKC", raw)
        header = text[:350]
        if "旅客吞吐量" not in header:
            continue
        if not re.search(r"旅客吞吐量\s*\(人\)", header) or "本期完成" not in header:
            raise CrawlerError("Not a monthly passenger-count table; annual/cumulative values refused")
        if ("货邮吞吐量" in header and header.index("货邮吞吐量") < header.index("旅客吞吐量")
                or "本年累计" in header and header.index("本年累计") < header.index("本期完成")):
            raise CrawlerError("Table column order changed; manual adapter review required")
        months = re.findall(r"(?<!\d)(\d{1,2})月(?:份)?", header.split("旅客吞吐量")[0])
        if not months or any(int(m) != month for m in months):
            raise CrawlerError("PDF month and supplied statistical period disagree")
        years = re.findall(r"(20\d{2})年", header.split("旅客吞吐量")[0])
        if years and any(int(y) != year for y in years):
            raise CrawlerError("PDF year and supplied statistical period disagree")
        for line_no, line in enumerate(text.splitlines(), 1):
            line = line.strip()
            for entity in airport_entities:
                aliases = entity.get("aliases", [])
                matches = [re.match(r"^" + re.escape(alias) + r"\s+(\d{1,3})\s+([\d,]+)(?=\s|$)", line)
                           for alias in aliases]
                matches = [m for m in matches if m]
                if not matches:
                    # Recognized row with an unrecognized first value is a parser error,
                    # not an absent airport or a zero. New/unknown names remain unmapped.
                    if any(re.match(r"^" + re.escape(alias) + r"\s", line) for alias in aliases):
                        raise CrawlerError("Recognized airport row has an unsupported passenger column")
                    continue
                match = matches[0]
                raw_number = match[2]
                if not re.fullmatch(r"(?:\d+|\d{1,3}(?:,\d{3})+)", raw_number) or not 1 <= int(match[1]) <= 200:
                    raise CrawlerError("Invalid rank or passenger integer")
                number = int(raw_number.replace(",", ""))
                if number > 2_000_000_000 or entity["id"] in seen:
                    raise CrawlerError("Duplicate airport row or implausible passenger count")
                seen.add(entity["id"])
                facts.append({"entity_id": entity["id"], "metric": "airport_passenger_throughput",
                    "scope": "airport_all_passengers", "grain": "month",
                    "period_start": date(year, month, 1),
                    "period_end": date(year, month, calendar.monthrange(year, month)[1]),
                    "value": Decimal(number), "unit": "人次", "quality": "validated", "qualifier": None,
                    "original_value": raw_number,
                    "locator": {"page": page_no, "line": line_no, "row": line,
                                "column": "旅客吞吐量(人)/本期完成", "source_unit": "人"},
                    "parser_version": PARSER_VERSION})
    required = REQUIRED & {e["id"] for e in airport_entities}
    if not facts or not required <= seen:
        raise CrawlerError("Monthly table missing expected established-airport rows; quarantined")
    return facts


def parse_pdf(path, period, entities):
    import pdfplumber
    with pdfplumber.open(path) as pdf:
        return parse_pages([p.extract_text() or "" for p in pdf.pages], period, entities)
