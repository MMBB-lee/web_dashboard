"""历史周/月预测：季节性基线模型 + 滚动起点回测（接口 12 的算法核心）。

设计要点（团队文档约束）：
- baseline：短序列使用近期水平和受限漂移，完整双季节序列使用同期中位数；
- 回测：rolling origin，按时间顺序切分，先训练后预测，输出 MAE / SMAPE；
- 门槛：连续10周或3个月，按时间顺序留出验证点并附预测区间；
- 纯 Python 实现，无第三方依赖；输入口径由调用方（C 的仓储）保证。

模型输入统一为 (period_start, value) 序列，period_start 为周期首日
（周为周一，月为自然月 1 号），与 03-接口总览 的时间约定一致。
"""

from app.services.historical_forecast.backtest import rolling_origin_backtest
from app.services.historical_forecast.baseline import (
    DataInsufficient,
    ForecastPoint,
    min_training_points,
    seasonal_median_baseline,
)

MODEL_VERSION = "baseline-adaptive-v2"

__all__ = [
    "DataInsufficient",
    "ForecastPoint",
    "MODEL_VERSION",
    "min_training_points",
    "rolling_origin_backtest",
    "run_history_forecast",
    "seasonal_median_baseline",
]


def run_history_forecast(
    points: list[tuple],
    grain: str,
    horizon: int,
) -> dict:
    """运行一次完整预测：基线预测 + 回测，返回与 ForecastResponse 对齐的 dict。

    points: [(period_start: date, value: float), ...]，任意顺序，内部会排序去重。
    grain: "week" | "month"。
    horizon: 预测步数（周 1—8，月 1—6，由 schema 层校验）。
    """
    series = _normalize(points)
    minimum = min_training_points(grain)

    if len(series) < minimum:
        return {
            "status": "data_insufficient",
            "forecast_points": [],
            "backtest": None,
            "training_window": _window(series),
            "model_version": MODEL_VERSION,
            "source_ids": [],
            "reason": f"训练数据不足：{grain} 粒度至少需要 {minimum} 个连续周期，实际 {len(series)} 个",
        }

    try:
        forecast = seasonal_median_baseline(series, grain, horizon)
    except DataInsufficient as exc:
        return {
            "status": "data_insufficient",
            "forecast_points": [],
            "backtest": None,
            "training_window": _window(series),
            "model_version": MODEL_VERSION,
            "source_ids": [],
            "reason": str(exc),
        }

    backtest = rolling_origin_backtest(series, grain, horizon)

    if backtest["sample_count"] == 0:
        return {
            "status": "data_insufficient",
            "forecast_points": [],
            "backtest": backtest,
            "training_window": _window(series),
            "model_version": MODEL_VERSION,
            "source_ids": [],
            "reason": "历史序列无法构造回测切分（训练段与验证段不满足最短要求）",
        }

    return {
        "status": "ready",
        "forecast_points": [
            {
                "period_start": p[0].isoformat(),
                "period_end": p[1].isoformat(),
                "value": p[2],
                "lower_bound": p[3],
                "upper_bound": p[4],
            }
            for p in forecast
        ],
        "backtest": backtest,
        "training_window": _window(series),
        "model_version": MODEL_VERSION,
        "source_ids": [],
    }


def _normalize(points: list[tuple]) -> list[tuple]:
    """排序并按 period_start 去重（保留最新值）。"""
    by_date: dict = {}
    for period_start, value in points:
        by_date[period_start] = value
    return sorted(by_date.items())


def _min_training_points(grain: str) -> int:
    """最短训练长度：两个完整季节周期。"""
    return min_training_points(grain)


def _window(series: list[tuple]) -> str | None:
    if not series:
        return None
    return f"{series[0][0].isoformat()}~{series[-1][0].isoformat()}"
