from datetime import date

import pytest

from anhui_crawler.annual import parse_pages, REPORTS
from anhui_crawler.common import CrawlerError, write_json
from anhui_crawler.pipeline import load_registry
from anhui_crawler.topics import parse_topic
from anhui_crawler.reporting import matrix
from anhui_crawler.updating import save_state


ENTITIES = load_registry()['entities']
URL = next(url for url, (year, _) in REPORTS.items() if year == 2024)
FIGURES = '报告期内，黄山景区累计接待进山游客492.24万人，较去年同期457.46万人增加34.78万人；索道运送981.49万人次。'


def test_annual_and_explicit_comparison_are_separate_from_cableways_and_months():
    parsed = parse_pages(['黄山旅游发展股份有限公司2024年年度报告', FIGURES, FIGURES], URL, ENTITIES)
    assert [r['value'] for r in parsed['records']] == [4922400, 4574600]
    assert [r['period_start'] for r in parsed['records']] == [date(2024, 1, 1), date(2023, 1, 1)]
    assert all(r['unit'] == '人' and r['grain'] == 'year' for r in parsed['records'])
    assert all(r['value'] != 9814900 for r in parsed['records'])


@pytest.mark.parametrize('pages', [
    ['黄山旅游发展股份有限公司2024年年度报告', FIGURES],
    ['黄山旅游发展股份有限公司2024年年度报告摘要', FIGURES, FIGURES],
    ['黄山旅游发展股份有限公司2023年年度报告', FIGURES, FIGURES],
    ['黄山旅游发展股份有限公司2024年年度报告', FIGURES, FIGURES.replace('492.24', '493.24')],
    ['黄山旅游发展股份有限公司2024年年度报告', FIGURES.replace('492.24万人', '约492.24万人'), FIGURES],
])
def test_annual_year_summary_qualifier_and_cross_section_disagreement_refused(pages):
    with pytest.raises(CrawlerError):
        parse_pages(pages, URL, ENTITIES)


def test_half_year_station_actual_does_not_take_six_station_total_or_arrivals():
    raw = '''<html><head><title>轨道上的文旅协奏曲</title></head><body>
    <div class="newsdatetit">轨道上的文旅协奏曲 2025年07月21日</div><div class="content"><p>
    今年1月至6月，黄山市的6个高铁站共发送旅客364.2万人次，其中，黄山北站共发送旅客308.8万人次；到达旅客305.6万人次。
    </p></div></body></html>'''.encode()
    row = parse_topic(raw, 'rail_station-huangshan-north-ahnews', ENTITIES)['records'][0]
    assert row['value'] == 3088000 and row['grain'] == 'cumulative'
    assert row['period_end'] == date(2025, 6, 30)
    assert row['locator']['reported_native_grain'] == 'half_year'
