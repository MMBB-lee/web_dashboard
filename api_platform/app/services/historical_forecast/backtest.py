"""滚动起点回测（rolling origin backtest）。

按时间顺序：从后往前取若干个切分点，每个切分点只用其之前的数据
训练基线模型、预测 horizon 步，与真实值比较。汇总全部切分的误差
得到 MAE 与 SMAPE。绝不使用未来数据训练（防泄漏）。
"""

from app.services.historical_forecast.baseline import (
    DataInsufficient,
    seasonal_median_baseline,
)


def rolling_origin_backtest(
    series: list[tuple],
    grain: str,
    horizon: int,
    max_folds: int = 3,
) -> dict:
    """返回 {method, mae, smape, sample_count}，样本不足时 mae/smape 为 None。"""
    errors: list[tuple[float, float]] = []  # (abs_err, smape_term)
    folds = 0

    # Earlier prefixes have at least 5 weeks/2 months and are validated against
    # up to horizon following values; no future value enters each fit.
    first_cut = 5 if grain == 'week' else 2
    last_cut = len(series) - 1
    cut = last_cut
    while cut >= first_cut and folds < max_folds:
        train = series[:cut]
        test = series[cut : cut + horizon]
        try:
            predictions = seasonal_median_baseline(train, grain, horizon)
        except DataInsufficient:
            cut -= 1
            continue
        # 预测的 period_start 必须与真实值对齐；未对齐（序列有洞）则丢弃该折
        by_date = {p[0]: p[2] for p in predictions}
        for (period_start, actual), (pred_start, _end, _v, _lo, _hi) in zip(test, predictions):
            if pred_start != period_start or period_start not in by_date:
                continue
            pred = by_date[period_start]
            errors.append((abs(actual - pred), _smape_term(actual, pred)))
        folds += 1
        cut -= 1

    if not errors:
        return {"method": "rolling_origin", "mae": None, "smape": None, "sample_count": 0}

    n = len(errors)
    return {
        "method": "rolling_origin",
        "mae": round(sum(e[0] for e in errors) / n, 2),
        "smape": round(sum(e[1] for e in errors) / n, 4),
        "sample_count": n,
    }


def _smape_term(actual: float, pred: float) -> float:
    denom = abs(actual) + abs(pred)
    if denom == 0:
        return 0.0
    return 2.0 * abs(actual - pred) / denom
