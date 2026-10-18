"""Reviewed Huangshan annual mountain-entry counts, separate from cableways.

Both report sections must agree. Prior-year values are explicitly printed
comparisons, never arithmetic differences or estimates of missing months.
"""
from datetime import date
from decimal import Decimal
import re

from .bulletin import compact
from .common import CrawlerError

VERSION = "huangshan-annual-entries-1"
REPORTS = {
    "https://static.cninfo.com.cn/finalpage/2025-04-19/1223151684.PDF": (2024, "2025-04-19"),
    "https://static.cninfo.com.cn/finalpage/2026-04-18/1225118314.PDF": (2025, "2026-04-18"),
}
PATTERN = r"黄山景区累计接待进山游客(?P<current>\d+(?:\.\d+)?)万人,较去年同期(?P<previous>\d+(?:\.\d+)?)万人"


def parse_pages(pages, url, entities):
    if url not in REPORTS:
        return None
    year, published = REPORTS[url]
    if not pages or f"黄山旅游发展股份有限公司{year}年年度报告" not in compact(pages[0]) or "年度报告摘要" in compact(pages[0]):
        raise CrawlerError("Expected the reviewed full annual report and exact reporting year")
    entity = next((e for e in entities if e['id'] == 'scenic:huangshan-main' and e['type'] == 'scenic' and
                   'scenic-huangshan-main' in e['source_ids']), None)
    if entity is None:
        raise CrawlerError("Huangshan annual report mapping requires review")
    found = [(number, match) for number, text in enumerate(pages, 1) for match in re.finditer(PATTERN, compact(text))]
    pairs = {(m['current'], m['previous']) for _, m in found}
    if len(found) != 2 or len(pairs) != 1 or len({number for number, _ in found}) != 2:
        raise CrawlerError("Annual entry figures must agree in two distinct report sections")
    primary, previous = next(iter(pairs))
    records = []
    for reporting_year, raw_value, role in [(year, primary, 'reported_current_year'), (year - 1, previous, 'explicit_prior_year_comparison')]:
        records.append({'entity_id': entity['id'], 'metric': 'scenic_mountain_entries', 'scope': 'scenic_mountain_entries',
            'grain': 'year', 'period_start': date(reporting_year, 1, 1), 'period_end': date(reporting_year, 12, 31),
            'value': Decimal(raw_value) * 10000, 'unit': '人', 'quality': 'validated', 'qualifier': None,
            'original_value': raw_value + '万人', 'parser_version': VERSION,
            'locator': {'report_year': year, 'value_role': role,
                'pages': [{'page': number, 'matched_text': match[0]} for number, match in found],
                'normalization': '万人乘10000；保留原文进山游客口径及人单位，不混入索道量、不声明去重游客',
                'publication_date_evidence': url, 'statistical_status': 'published_business_volume_not_independent_measurement'}})
    return {'published_at': published + 'T00:00:00+08:00', 'records': records}


def parse_pdf(path, source_id, entities, url):
    if source_id != 'scenic-huangshan-main' or url not in REPORTS:
        return None
    import pdfplumber
    with pdfplumber.open(path) as pdf:
        return parse_pages([page.extract_text() or '' for page in pdf.pages[:20]], url, entities)


def capabilities():
    return {'parser_version': VERSION, 'reviewed_full_reports_only': True,
            'metric': 'scenic_mountain_entries', 'unit': '人', 'reports': [
                {'url': url, 'report_year': year, 'publication_date': published} for url, (year, published) in REPORTS.items()]}
