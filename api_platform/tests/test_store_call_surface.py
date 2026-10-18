"""静态守卫：**源码里调用的 store 方法必须真实存在且接受所传的关键字参数**。

为什么要有这个测试
------------------
2026-10-17 接 C 0.2.1a2 的 ``uow.visible`` 时，28 号写了
``store.claim_job(..., job_id=job_id)``，而 ``CStore.claim_job`` **没有**
``job_id`` 形参。当时 pytest 全绿——因为 26/27/28 的用例压根没走到 cstore
（都以 503 收场），只有真机 HTTP 才 TypeError → 500。

这类问题有三个共同点，决定了它只能靠静态检查发现：

1. 出问题的分支**不在单元测试覆盖的路径上**（要凑齐替身把它拽进测试，代价极高）；
2. Python 是动态绑定，缺参数只在运行到那一行时才炸；
3. 方法名写错也一样：``getattr`` 风格的调用、或某个 store 实现漏了方法，
   都要到真机才暴露。

所以这里用 AST 把 ``app/`` 下所有 ``<store>.<method>(...)`` 的调用点扫出来，
逐个与 ``CStore`` / ``SqliteJobStore`` 的真实签名比对。**它不替代功能测试，
只是替 function 测试拦住那些「根本跑不到」的错误。**
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

# 同名变量里，只有这些是作业 / 仓储对象；其余（会话库等）在 IGNORE 里排除。
STORE_RECEIVERS = {"store", "cstore", "self.store"}

# 静态分析分不出这些是别的对象上的方法（认证 store），显式登记避免假警。
IGNORE_METHODS = {
    "get_session", "get_user_by_username", "create_session", "delete_session",
    "count_active_sessions",
}

# 只在 **CStore 路径** 上存在的形参（``SqliteJobStore`` 形状不齐）。
# 每一条都必须写明理由——新增条目时请自问：真机到底会用哪个 store？
#
# ``28 短时预测`` 拿的 store 来自 ``get_c_store()``，且拿不到时直接 503，
# 因此 sqlite 回环**不会被走到**；这两个幂等 / 定向领取能力是 C 的作业表现有
# 语义，D 的 sqlite 桥接库（``job_store.py``，待 C 接入后退役）从未实现。
# 若哪天 28 号改成落 sqlite，这里必须变成真实的方法改造，而不是继续加例外。
CSTORE_ONLY: dict[str, set[str]] = {
    "create_job": {"idempotency_key", "reason", "requested_by"},
    "claim_job": {"job_id"},
}


def _accepted_kwargs(cls, method: str) -> set[str] | None:
    """返回该类方法接受的参数名；``{"*"}`` 表示有 **kwargs；None 表示没有此方法。"""
    func = getattr(cls, method, None)
    if func is None or not callable(func):
        return None
    try:
        params = inspect.signature(func).parameters
    except (TypeError, ValueError):  # pragma: no cover
        return None
    if any(p.kind is p.VAR_KEYWORD for p in params.values()):
        return {"*"}
    return set(params)


def _calls_in(repo: Path):
    for path in sorted(repo.glob("app/**/*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:  # pragma: no cover —— 语法错由别处先报
            pytest.fail(f"{path}:{exc.lineno} 语法错误：{exc.msg}")
            continue
        lineno_of = {}
        for node in tree.body:
            lineno_of[id(node)] = getattr(node, "lineno", 0)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute):
                continue
            receiver = func.value
            if isinstance(receiver, ast.Name):
                owner = receiver.id
            elif isinstance(receiver, ast.Attribute):
                owner = f"self.{receiver.attr}"
            else:
                continue
            if owner not in STORE_RECEIVERS:
                continue
            yield path, node.lineno, owner, func.attr, {
                k.arg for k in node.keywords if k.arg is not None
            }


def test_every_store_call_matches_a_real_signature():
    from app.services.c_store import CStore
    from app.services.job_store import SqliteJobStore

    repo = Path(__file__).resolve().parents[1]
    targets = (("CStore", CStore), ("SqliteJobStore", SqliteJobStore))

    problems: list[str] = []
    checked = 0

    for path, lineno, owner, method, kwargs in _calls_in(repo):
        if method in IGNORE_METHODS:
            continue
        checked += 1

        known, unsupported = [], []
        for label, cls in targets:
            accepted = _accepted_kwargs(cls, method)
            if accepted is None:
                known.append(label)
            elif accepted != {"*"}:
                bad = sorted(kwargs - accepted - CSTORE_ONLY.get(method, set()))
                if bad:
                    unsupported.append((label, bad))

        if len(known) == len(targets):
            problems.append(
                f"{path.relative_to(repo)}:{lineno} {owner}.{method}() "
                f"—— 两个 store 实现都没有这个方法"
            )
        for label, bad in unsupported:
            problems.append(
                f"{path.relative_to(repo)}:{lineno} {owner}.{method}("
                f"{', '.join(kwargs)}) —— {label}.{method} 不接受 {bad}"
            )

    # 守卫本身要是空的就说明扫描逻辑坏了（比如 app/ 路径变了）
    assert checked > 40, f"只扫到 {checked} 处调用，扫描逻辑可能失效了"
    assert not problems, "源码里有 store 调用与真实签名不符：\n  " + "\n  ".join(
        sorted(problems)
    )


def test_c_store_surface_test_still_exists():
    """契约清单守卫（``test_c_store_surface.py``）必须在，别被误删。

    那边守的是「CStore 有没有把 C 的方法包出来」，这边守的是「调用的人有没有
    用对」，两者互补。
    """
    sibling = Path(__file__).resolve().parent / "test_c_store_surface.py"
    assert sibling.is_file(), f"缺少契约清单守卫：{sibling}"


class TestJobColumnLimits:
    """**写给 C 表的字符串必须在它的列宽里**——这是第八次「单测全绿、真机炸」。

    2026-10-17 的 28 号短时预测：幂等键直拼成
    ``visible-forecast-{uuid}-{cutoff}-{horizon}`` 共 **82 字符**，而 C 的
    ``jobs.idempotency_key`` 是 ``varchar(64)``。MySQL 抛
    ``DataError 1406 Data too long`` → HTTP 500；而 pytest 用的 sqlite 作业库
    **不检查列宽**，所以 386 项全绿也发现不了。

    防线是两层的：构造处用定长摘要（``e_bridge._forecast_idempotency_key``），
    入库前``CStore`` 再自查一次（``_check_job_column_limits``）。
    """

    def test_forecast_key_is_short_stable_and_distinct(self):
        from app.services.e_bridge import _forecast_idempotency_key
        from app.services.c_store import C_JOB_STRING_LIMITS

        key = _forecast_idempotency_key("a" * 36, "2026-05-01T10:00:00+08:00", 30, 5)
        assert len(key) <= C_JOB_STRING_LIMITS["idempotency_key"]
        # 幂等：同一组入参必须回到同一个作业
        again = _forecast_idempotency_key("a" * 36, "2026-05-01T10:00:00+08:00", 30, 5)
        assert key == again
        # 换跨度 / 换步长 / 换截止时刻，都应是不同的作业
        assert key != _forecast_idempotency_key("a" * 36, "2026-05-01T10:00:00+08:00", 15, 5)
        assert key != _forecast_idempotency_key("a" * 36, "2026-05-01T11:00:00+08:00", 30, 5)
        assert key != _forecast_idempotency_key("a" * 36, "2026-05-01T10:00:00+08:00", 30, 10)

    def test_video_analysis_key_fits(self):
        from app.services.c_store import C_JOB_STRING_LIMITS
        from uuid import uuid4

        # enqueue_visible_analysis 用的键：前缀 + 规范 UUID（36）
        key = f"video-analysis-{uuid4()}"
        assert len(key) <= C_JOB_STRING_LIMITS["idempotency_key"]

    def test_key_longer_than_column_exceeds_limit_is_rejected(self):
        """超长键必须**当场**报错，不能等到 MySQL 抛 DataError。"""
        from app.services.c_store import _check_job_column_limits

        too_long = "x" * (64 + 1)
        with pytest.raises(ValueError) as excinfo:
            _check_job_column_limits(job_type="visible_forecast",
                                     idempotency_key=too_long)
        message = str(excinfo.value)
        # 报错要能指出怎么修，而不是只说「太长了」
        assert "varchar(64)" in message
        assert "摘要" in message

    def test_oversized_job_type_is_rejected_too(self):
        from app.services.c_store import _check_job_column_limits

        with pytest.raises(ValueError) as excinfo:
            _check_job_column_limits(job_type="t" * 49, idempotency_key="ok")
        assert "varchar(48)" in str(excinfo.value)
