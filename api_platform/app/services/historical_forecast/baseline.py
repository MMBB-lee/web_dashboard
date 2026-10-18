"""季节性中位数基线模型。

思路：客流有明显的周内/年内季节性。对每个未来周期，取其
「季节位置」（月粒度=月份，周粒度=ISO 周号）在全部历史同期取值的
中位数作为点预测；用样本内残差标准差构造约 95% 预测区间。
中位数对异常值稳健，且不引入任何伪造趋势。
"""

import calendar
import math
import statistics
from collections import defaultdict
from datetime import date, timedelta

SEASON_LENGTH = {"week": 52, "month": 12}
_INTERVAL_Z = 1.96  # 约 95% 区间

MIN_TRAINING_POINTS = {"week": 10, "month": 3}


def min_training_points(grain: str) -> int:
    return MIN_TRAINING_POINTS[grain]

ForecastPoint = tuple  # (period_start, period_end, value, lower, upper)


class DataInsufficient(Exception):
    """季节位置样本不足，无法给出诚实的基线预测。"""


def season_key(period_start: date, grain: str) -> int:
    """周期的季节位置：月粒度用月份，周粒度用 ISO 周号。"""
    if grain == "month":
        return period_start.month
    return period_start.isocalendar().week


def next_period(period_start: date, grain: str) -> date:
    """下一个周期的首日：周=+7 天（周一对齐由输入保证），月=下月 1 号。"""
    if grain == "month":
        if period_start.month == 12:
            return date(period_start.year + 1, 1, 1)
        return date(period_start.year, period_start.month + 1, 1)
    return period_start + timedelta(days=7)


def period_end(period_start: date, grain: str) -> date:
    """周期末日：周=周日，月=自然月末（文档约定周为周一至周日）。"""
    if grain == "month":
        return date(period_start.year, period_start.month, calendar.monthrange(period_start.year, period_start.month)[1])
    return period_start + timedelta(days=6)


def seasonal_median_baseline(series: list[tuple], grain: str, horizon: int) -> list[tuple]:
    """对已排序的 (period_start, value) 序列做季节中位数预测。

    返回 [(period_start, period_end, value, lower, upper), ...] 共 horizon 项。
    """
    if not series:
        raise DataInsufficient("输入序列为空")
    if any(next_period(a[0], grain) != b[0] for a,b in zip(series,series[1:])):
        raise DataInsufficient("历史训练周期不连续")
    if len(series) < SEASON_LENGTH[grain] * 2:
        # With less than two seasons, use recent level and capped local drift;
        # three months cannot identify annual seasonality.
        values=[value for _,value in series[-(5 if grain=='week' else 3):]]
        level=values[-1]
        changes=[b-a for a,b in zip(values,values[1:])]
        drift=statistics.median(changes) if changes else 0
        cap=max(1,statistics.median(values))*0.10
        drift=max(-cap,min(cap,drift))
        residual=_std(changes) if changes else 0
        points=[];cursor=series[-1][0]
        for step in range(1,horizon+1):
            cursor=next_period(cursor,grain)
            value=max(0,level+drift*sum(0.8**i for i in range(step)))
            width=max(residual,0.05*level)*_INTERVAL_Z*math.sqrt(step)
            points.append((cursor,period_end(cursor,grain),round(value,2),round(max(0,value-width),2),round(value+width,2)))
        return points

    by_season: dict[int, list[float]] = defaultdict(list)
    for period_start, value in series:
        by_season[season_key(period_start, grain)].append(value)

    # 任一将预测到的季节位置都至少要有样本；否则诚实拒绝
    last_date = series[-1][0]
    future_keys = []
    cursor = last_date
    for _ in range(horizon):
        cursor = next_period(cursor, grain)
        future_keys.append(season_key(cursor, grain))
    missing = [k for k in set(future_keys) if not by_season.get(k)]
    if missing:
        raise DataInsufficient(f"以下季节位置无历史样本，无法预测：{sorted(missing)}")

    # 样本内残差（实际值 - 同期中位数拟合值）估计区间宽度
    fitted_residuals = [
        value - statistics.median(by_season[season_key(period_start, grain)])
        for period_start, value in series
    ]
    residual_std = _std(fitted_residuals)
    half_width = _INTERVAL_Z * residual_std

    points: list[tuple] = []
    cursor = last_date
    for _ in range(horizon):
        cursor = next_period(cursor, grain)
        value = statistics.median(by_season[season_key(cursor, grain)])
        lower = max(0.0, value - half_width)
        upper = value + half_width
        points.append((cursor, period_end(cursor, grain), round(value, 2), round(lower, 2), round(upper, 2)))
    return points


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))
