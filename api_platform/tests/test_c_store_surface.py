"""守卫：路由层调的 ``CStore`` 方法必须真的存在。

2026-10-17 真机事故：给 ``CStore`` 加 ``save_history_forecast`` /
``enqueue_ingestion`` 时，我把它们插到了**模块级**（类的缩进已经结束），
结果单测全绿——测试用的是 fake 替身，fake 上有这两个方法；真机一跑
``AttributeError`` → 500。

单测替身天然盖不住这类问题：**替身只保证「被测代码调的方法我实现了」，
不保证「真类上也实现了」。** 所以这里直接从源码里把路由层对
``cstore.xxx`` 的调用点扫出来，逐条对着真 ``CStore`` 断言。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services.c_store import CStore

# 只认路由层的 ``cstore.xxx``：业务层（app/services）里还有会话仓储、用户
# 仓储等**同名的 store 变量**，放宽到 ``store.`` 会把它们误收进来。
_CALL = re.compile(r"\bcstore\.([a-z_][a-z0-9_]*)")
_SOURCES = (Path("app/api/v1"),)


def _called_methods() -> set[str]:
    found: set[str] = set()
    for root in _SOURCES:
        for path in sorted(root.rglob("*.py")):
            found.update(_CALL.findall(path.read_text(encoding="utf-8")))
    return found


def test_every_called_method_exists_on_c_store():
    """源码里出现的每个 ``cstore.xxx`` 都必须在真 ``CStore`` 上找得到。"""
    missing = sorted(m for m in _called_methods() if not hasattr(CStore, m))
    assert not missing, (
        "这些方法被路由/业务层调用，但 CStore 上没有——"
        "多半是新增方法时插错了缩进（插到类外去了）：" + ", ".join(missing)
    )


def test_contract_methods_are_all_present():
    """按 03 契约分接口列出必需方法；缺一个就说明某条接口没真正接上。"""
    required = {
        "05/06/07 实体目录·指标字典·来源详情":
            ["list_entities", "list_metrics", "get_source"],
        "08/09/11 时序与比较": ["query_series"],
        "10 驾驶舱总览": ["get_latest_verified"],
        "12 历史预测": ["save_history_forecast"],
        # 14 的假期元信息（名称 / scope_key）由 query_holiday_values 的
        # ``holiday`` 键一并带回，所以不需要单独的 get_holiday。
        "13/14 假期与假期比较": ["list_holidays", "query_holiday_values"],
        "15/16/17/18 事件": ["list_events", "get_event", "create_event",
                             "update_event"],
        "20/21/22 知识库": [
            "register_document", "register_source", "find_document",
            "get_document", "write_chunks", "get_chunks",
            "get_index_artifact_ref", "ready_document_ids",
            "snapshot_for_e_publisher",
        ],
        "23/24/25 录像": [
            "list_videos", "get_permission", "persist_file", "register_asset",
            "register_uploaded_video", "get_playback_ref",
        ],
        "26/27 录像分析": [
            "create_video_analysis", "write_count_points", "fail_video_analysis",
            "get_video_analysis", "load_analysis_counts", "get_analysis_audit",
        ],
        "29/30 采集作业": ["enqueue_ingestion", "get_job", "create_job", "fail_job"],
    }
    flat = [m for names in required.values() for m in names]
    missing = sorted(m for m in flat if not hasattr(CStore, m))
    assert not missing, f"CStore 缺少契约所需的门面方法：{', '.join(missing)}"


def test_called_but_unlisted_are_visible():
    """反向守卫：源码调用了但上面清单没登记的，要显式列出来让人看见。

    不直接判失败——有些方法（``read`` / ``write`` / ``doctor`` / ``heartbeat``）
    是基础设施，本来就该存在。这条用例的作用是让新增调用**无法悄悄溜过去**。
    """
    known = {
        "create_event", "create_job", "create_video_analysis", "doctor",
        "enqueue_ingestion",
        "fail_job", "fail_video_analysis", "find_document", "get_analysis_audit",
        "get_chunks", "get_document", "get_event",
        "get_index_artifact_ref", "get_job", "get_latest_verified",
        "get_permission", "get_playback_ref", "get_source", "get_video_analysis",
        "heartbeat", "list_entities", "list_events", "list_holidays",
        "list_metrics", "list_videos", "load_analysis_counts", "persist_file",
        "query_holiday_values", "query_series", "read", "ready_document_ids",
        "register_asset", "register_document", "register_source",
        "register_uploaded_video", "save_history_forecast",
        "snapshot_for_e_publisher", "update_event", "write", "write_chunks",
        "write_count_points",
    }
    unknown = sorted(_called_methods() - known)
    assert not unknown, (
        "源码里出现了清单外的新调用，请确认它是有意为之并补进 known："
        + ", ".join(unknown)
    )


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
