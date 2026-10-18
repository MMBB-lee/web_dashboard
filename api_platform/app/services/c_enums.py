"""C 的枚举字典：一律以 C 导出的常量为准。

背景（两次事故）：

1. 2026-10-17 之前，D 按 03 契约写死 holidays.kind 只有 4 个值、
   events.kind 只有 5 个值。真机库里 holiday 有 ``new_year``、event 有
   ``closure`` / ``expired``——按契约硬校验会把**真实存在的**查询挡成 400。
2. 于是 D 改成「一律透传不校验」。但 C 021a2 收口时明确：枚举 owner 是 C，
   C 已经把常量正式导出（``HOLIDAY_KINDS`` / ``EVENT_KINDS`` /
   ``EVENT_STATUSES`` / ``EVENT_SEVERITIES``），内容与根 03 同步。

所以这里的规则是：**C 有就用 C 的，C 没有（装的是 < 0.2.1a2 的旧包）才用
这份同步副本**。副本取值与 C 021a2 收口文档第 1 条一致，不是 D 自己发明的。

升级到 C 0.2.1a2 之后本文件无需改动——取值会自动跟随 C。
"""
from __future__ import annotations

# ---------- 同步副本（C 021a2《致D和E-今日确认与接收清单》第 1 条） ----------

_FALLBACK_HOLIDAY_KINDS = frozenset({
    "new_year", "spring_festival", "qingming", "may_day", "dragon_boat",
    "mid_autumn", "national_day", "summer", "winter",
})
_FALLBACK_EVENT_KINDS = frozenset({
    "weather", "scenic_closure", "closure", "rail_adjustment",
    "flight_adjustment", "other",
})
_FALLBACK_EVENT_STATUSES = frozenset({"active", "resolved", "expired"})
_FALLBACK_EVENT_SEVERITIES = frozenset({"info", "notice", "warning", "critical"})

_HOLIDAY_KINDS = _FALLBACK_HOLIDAY_KINDS
_EVENT_KINDS = _FALLBACK_EVENT_KINDS
_EVENT_STATUSES = _FALLBACK_EVENT_STATUSES
_EVENT_SEVERITIES = _FALLBACK_EVENT_SEVERITIES


def _load() -> None:
    """装得上新 C 包就把取值换成 C 导出的常量。

    任何异常（包缺失、版本不足、属性改名）都吞掉继续用同步副本——D 不该因为
    字典口径取不到而整体崩溃；但**绝不静默放宽成空集合**（那等于不校验）。
    """
    global _HOLIDAY_KINDS, _EVENT_KINDS, _EVENT_STATUSES, _EVENT_SEVERITIES
    try:
        import data_foundation.contracts as contracts
    except Exception:  # noqa: BLE001
        return
    got = []
    for name, fallback in (
        ("HOLIDAY_KINDS", _FALLBACK_HOLIDAY_KINDS),
        ("EVENT_KINDS", _FALLBACK_EVENT_KINDS),
        ("EVENT_STATUSES", _FALLBACK_EVENT_STATUSES),
        ("EVENT_SEVERITIES", _FALLBACK_EVENT_SEVERITIES),
    ):
        value = getattr(contracts, name, None)
        got.append(frozenset(value) if value else fallback)
    _HOLIDAY_KINDS, _EVENT_KINDS, _EVENT_STATUSES, _EVENT_SEVERITIES = got


_load()


def holiday_kinds() -> frozenset[str]:
    return _HOLIDAY_KINDS


def event_kinds() -> frozenset[str]:
    return _EVENT_KINDS


def event_statuses() -> frozenset[str]:
    return _EVENT_STATUSES


def event_severities() -> frozenset[str]:
    return _EVENT_SEVERITIES


def pattern(values: frozenset[str]) -> str:
    """给 pydantic ``Field(pattern=...)`` 用的正则串（排列稳定，便于快照测试）。"""
    body = "|".join(sorted(v.replace("\\", "\\\\").replace("|", "\\|") for v in values))
    return "^(" + body + ")$"


def hint(values: frozenset[str], limit: int = 3) -> str:
    """给字段说明用的列举，如 ``closure | new_year | weather | … 共 9 项``。"""
    ordered = sorted(values)
    if len(ordered) <= limit:
        return " | ".join(ordered)
    return " | ".join(ordered[:limit]) + f" | … 共 {len(ordered)} 项"
