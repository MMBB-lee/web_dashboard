"""画面人数（``camera_visible_people``）的完整链路：C 的持久化 + E 的计算。

2026-10-17 装上 C 0.2.1a2 后，P2PNet 的结果**不再是 D 的私表 ``d_video_analyses``
当主账本**，而是走 C 的 ``uow.visible``（``visible_analyses`` /
``visible_count_points`` / ``visible_forecasts`` 三张表）。D 在这里只做三件事：

1. **形状组装**：把 D 的 26 号请求体 + E 的模型溯源拼成 C ``create_analysis``
   要求的 ``config``（8 键）与 ``provenance``（7 必填键）；
2. **编排**：对外 HTTP 只负责「同一事务入队 + 登记」，真正的取文件 / 调 E /
   写结果一律在 **worker** 里持 job lease 完成；
3. **过滤**：27 号按 03 §27「D 对 C 原生结果执行查询过滤」做 grain / 时间过滤
   ——因为 C 的 ``get_counts`` 没有这几个参数。

守住的几条线：
- **不重复实现口径**：5 分钟窗口的中位数/样本数/质量由 **E** 算（``process_visible_frames``），
  C 在 ``complete_analysis`` 里逐点复核。D **不重算也不改写**——改了只会让两处
  口径分叉，然后每次都由 C 抛 Conflict 才被发现。
- **不伪造输入**：录像文件路径只能来自 ``store.get_visible_input``（带授权 +
  内容摘要校验），D 不自己去拼 data_root 路径。
- **完成/失败只能由 C 内部 operator**：``complete_analysis`` / ``fail_analysis``
  内部已经写了 C 的 job 结果，D 外层**绝不再调** ``complete_job`` / ``fail_job``。
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Iterable, Iterator

logger = logging.getLogger("api_platform.visible_pipeline")

# C ``visible.config_and_provenance()`` 要求的 config 键——**多一个少一个都不行**
# （源码里是 ``set(config) != keys`` 判等），所以这里必须逐个凑齐。
C_CONFIG_KEYS: tuple[str, ...] = (
    "count_model",
    "weights_version",
    "roi",
    "sample_interval_seconds",
    "aggregation_minutes",
    "confidence_threshold",
    "inference_engine",
    "device",
)

# E 0.6.0 / C 0.2.1a2 双方都锁死的值：只有 P2PNet + PyTorch + CPU + 阈值 0.5
# 通过了 C 的 ``validate_config``，其它组合（GPU / NPU / OpenVINO / AUTO）一律 422。
P2PNET_CONFIDENCE_THRESHOLD = 0.5
P2PNET_AGGREGATION_MINUTES = 5

# C 要求 ``provenance`` 的必填 7 键；E ``P2PNetCounter.provenance`` 正好就返回这 7 个。
C_PROVENANCE_REQUIRED: tuple[str, ...] = (
    "count_model",
    "weights_version",
    "weights_sha256",
    "model_source_revision",
    "confidence_threshold",
    "inference_engine",
    "device",
)

# C 的单批写入上限（``write_points`` 里 ``len(points) > 1000`` 直接拒）。
POINT_BATCH_SIZE = 1000


class VisiblePipelineError(RuntimeError):
    """画面人数链路上的、需要**诚实暴露给调用方**的失败。

    故意用专用类型：worker 捕获后写 ``fail_analysis``，HTTP 层捕获后转 422/503，
    都**不能**把它当成「查到了空结果」吞掉。
    """


# ---------- 形状组装 ----------


def _normalise_roi(roi: Any) -> dict[str, Any]:
    """把 D/B 侧的 ROI 归一成 C 要求的 ``{"points": [{"x":…,"y":…}, …]}``。

    C 要求 3—128 个顶点、坐标 0—1、顶点唯一、面积非零、不自交。这些几何校验由
    **C** 做（``visible.config_and_provenance``），D 不重复实现——但 D 必须把形状
    喂对，否则 C 会以 400 ``bad_request`` 拒收。
    """
    if isinstance(roi, dict):
        points = roi.get("points")
        if isinstance(points, list):
            return {"points": [_normalise_point(p) for p in points]}
        return roi
    if isinstance(roi, list):
        return {"points": [_normalise_point(p) for p in roi]}
    raise VisiblePipelineError("roi 必须是含 points 的对象或点数组")


def _normalise_point(point: Any) -> dict[str, Any]:
    if isinstance(point, dict) and "x" in point and "y" in point:
        return {"x": float(point["x"]), "y": float(point["y"])}
    if isinstance(point, (list, tuple)) and len(point) == 2:
        return {"x": float(point[0]), "y": float(point[1])}
    raise VisiblePipelineError(f"roi 顶点必须是 [x, y] 或 {{x, y}}：{point!r}")


def build_analysis_config(body: dict[str, Any]) -> dict[str, Any]:
    """把 26 号请求体组装成 C ``create_analysis`` 的 ``config``（恰好 8 键）。

    ``confidence_threshold`` 不在 D 的请求体里（03 §26 也没这个字段），但 C 要求
    config 里有且等于 0.5 —— 这是 E 0.6.0 的固定值，不是可调参数，D 主动注入。
    """
    data = dict(body or {})
    data.pop("openvino_version", None)  # P2PNet 不接受 OpenVINO 字段（带 null 也不行）
    if "roi" in data:
        data["roi"] = _normalise_roi(data["roi"])
    config = {key: data.get(key) for key in C_CONFIG_KEYS}
    config["confidence_threshold"] = P2PNET_CONFIDENCE_THRESHOLD
    config["aggregation_minutes"] = P2PNET_AGGREGATION_MINUTES
    missing = [k for k, v in config.items() if v is None]
    if missing:
        raise VisiblePipelineError(
            "分析配置不完整，缺：" + "、".join(missing)
            + "（C 要求 count_model/weights_version/roi/sample_interval_seconds/"
              "aggregation_minutes/inference_engine/device 齐全）"
        )
    return config


def build_analysis_provenance() -> dict[str, Any]:
    """从 E 的 P2PNet 计数器取模型溯源（C 要求必填 7 键）。

    **拿不到就报错，不编造**：C 要求 ``weights_sha256`` 是 64 位小写十六进制，
    D 若喂假的，C 会以 400 拒——那还算好；若喂得像是真的，链路里就留了一条
    无法追溯到真实权重的「成功」，比失败危险得多。
    """
    from app.services import e_bindings

    provenance = e_bindings.get_p2p_counter_provenance() or {}
    missing = [k for k in C_PROVENANCE_REQUIRED if k not in provenance]
    if missing:
        raise VisiblePipelineError(
            "P2PNet 模型溯源不可用（缺 " + "、".join(missing)
            + "）：模型包未登记或摘要校验失败，请核对 P2PNET_BUNDLE_DIR"
        )
    return {k: provenance[k] for k in C_PROVENANCE_REQUIRED}


def check_provenance_consistency(config: dict[str, Any],
                                 provenance: dict[str, Any]) -> None:
    """C 会比对两处重叠键的值一致；这里**提前自查**，让 400 带着具体键名出在 D 侧。

    不自查的话，冲突会推迟到 worker 异步阶段才炸，而且 C 的报错只说
    「本次模型溯源与分析配置不一致」，不带是哪个键。
    """
    shared = set(config) & set(provenance)
    mismatched = [k for k in sorted(shared) if config[k] != provenance[k]]
    if mismatched:
        raise VisiblePipelineError(
            "分析配置与模型溯源不一致（"
            + "、".join(f"{k}: {config[k]!r} ≠ {provenance[k]!r}" for k in mismatched)
            + "）：同一份分析不能声明两套模型身份"
        )


# ---------- worker 侧：video_analysis 作业 ----------


def run_visible_analysis(store: Any, *, job: dict[str, Any], lease: Any,
                         counter: Any, now=None) -> dict[str, Any]:
    """消费一个 ``video_analysis`` 作业：**取文件 → 调 E → 写回 C**。

    序列严格按 C 0.2.1a2 的仓储语义（``VisibleRepository``）：
    ``get_visible_input`` → ``begin_analysis`` → ``write_points``（分批 ≤1000）
    → ``complete_analysis``。**最后一步内部已完成 C 的 job**，外层不得再碰。

    任一步抛异常 → ``fail_analysis``（同样内部已 fail_job），然后把异常继续向
    上抛给 worker 主循环去记日志；这里**不吞**。
    """
    payload = job.get("payload") or {}
    analysis_id = payload.get("analysis_id")
    if not analysis_id:
        raise VisiblePipelineError("video_analysis 作业缺少 payload.analysis_id")
    if counter is None:
        raise VisiblePipelineError(
            "P2PNet 模型包未登记或校验失败，无法执行画面人数分析"
        )

    try:
        reference = store.get_visible_input(analysis_id, lease)
    except Exception:  # noqa: BLE001 —— 交给下面的统一 fail_analysis
        raise VisiblePipelineError(
            f"无法取回已授权的录像输入（analysis_id={analysis_id}）："
            "录像未授权、原件缺失或内容摘要不符"
        ) from None

    e_payload = {
        "video_id": str(payload.get("video_id") or reference.get("video_id") or ""),
        "analysis_id": analysis_id,
        "file_path": reference.get("file_path"),
        "recorded_start_at": reference.get("recorded_start_at"),
        "recorded_end_at": reference.get("recorded_end_at"),
        "source_ids": list(reference.get("source_ids") or []),
        "config": reference.get("config"),
    }
    if not e_payload["file_path"]:
        raise VisiblePipelineError("录像输入回执里没有可用的 file_path")
    if not e_payload["source_ids"]:
        raise VisiblePipelineError("录像输入回执里没有来源身份，无法写溯源")

    from intelligent_analysis.worker.runner import dispatch_analysis_job

    store.begin_visible_analysis(analysis_id, lease)
    try:
        result = dispatch_analysis_job("video_analysis", e_payload, counter=counter)
    except Exception as exc:  # noqa: BLE001 —— 诚实失败，E 的原因原样带上
        raise VisiblePipelineError(f"E 画面人数分析失败：{exc}") from exc

    points = normalise_points_for_c(result.get("points") or [])
    written = 0
    for batch in _batched(points, POINT_BATCH_SIZE):
        counts = store.write_visible_points(analysis_id, batch, lease)
        written += int(counts.get("inserted", 0)) + int(counts.get("skipped", 0))
    analysis = store.complete_visible_analysis(
        analysis_id, written, lease,
        manual_validation=result.get("manual_validation"),
    )
    return {
        "analysis_id": analysis_id,
        "video_id": e_payload["video_id"],
        "point_count": written,
        "metric": result.get("metric", "camera_visible_people"),
        "count_model": (result.get("analysis") or {}).get("count_model", "p2pnet"),
        "analysis": analysis,
    }


def _batched(items: list[dict[str, Any]], size: int) -> Iterator[list[dict[str, Any]]]:
    for start in range(0, len(items), size):
        yield items[start:start + size]


def normalise_points_for_c(points: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """把 E 的 CountPoint 形状收成 C ``write_points`` 接受的形状。

    C 的允许键是白名单（``required | allowed``）：多一个键就 ``InvalidArgument``。
    E 的 ``CountPoint.model_dump(mode="json")`` 可能带上 C 不认识的键，因此这里
    **按白名单显式挑一遍**，而不是整 dict 透传。

    值本身（含 5 分钟窗口中位数 / 样本数 / 质量）**一律不改**——那是 E 的口径，
    C 在 ``complete_analysis`` 里会逐点重新核算，D 动了反而对不上。
    """
    allowed = (
        "grain", "value", "sample_count", "quality_status", "source_ids",
        "metric", "unit", "recorded_at", "period_start", "period_end",
        "aggregation_rule", "published_at", "ingested_at",
    )
    out: list[dict[str, Any]] = []
    for point in points:
        item = {k: point.get(k) for k in allowed if k in point}
        item.setdefault("metric", "camera_visible_people")
        item.setdefault("unit", "人")
        out.append(item)
    return out


# ---------- worker / HTTP 侧：visible_forecast 作业 ----------


def run_visible_forecast(store: Any, *, job: dict[str, Any], lease: Any,
                         counter: Any) -> dict[str, Any]:
    """消费一个 ``visible_forecast`` 作业并发布预测到 C。

    03 §28 与 26 号文档 §「预测存储」共同要求：预测必须挂在**独立的
    ``visible_forecast`` 作业 lease** 上原子保存（``publish_forecast`` 会拿
    ``lease.job_id`` 回查 job，校验 type / payload 里的 analysis_id·cutoff_at·
    horizon_minutes 三者一致）。

    输入由 C 提供并绑定指纹（``input_sha256``）：E 只负责算，D 不重新挑历史点。
    """
    payload = job.get("payload") or {}
    analysis_id = payload.get("analysis_id")
    cutoff_at = payload.get("cutoff_at")
    horizon_minutes = payload.get("horizon_minutes")
    step_minutes = payload.get("step_minutes", 5)
    if not analysis_id or not cutoff_at or horizon_minutes is None:
        raise VisiblePipelineError(
            "visible_forecast 作业载荷缺 analysis_id / cutoff_at / horizon_minutes"
        )

    expected_video_id = payload.get("video_id")
    if expected_video_id:
        # worker 路径也要核一次归属（HTTP 在开票前已经核过并给 404，这里是
        # 兜底）：预测结果会被 publish 到这条 analysis 上，绝不能让「别的录像」
        # 借分析结果给自己的 video_id 开出一条记录。
        owner_video_id = str(
            (store.get_visible_analysis(analysis_id) or {}).get("video_id") or "")
        if owner_video_id and owner_video_id != str(expected_video_id):
            raise VisiblePipelineError(
                f"分析 {analysis_id} 属于录像 {owner_video_id}，"
                f"与作业的 video_id {expected_video_id} 不一致"
            )

    inputs = store.get_visible_forecast_input(
        analysis_id=analysis_id, cutoff_at=cutoff_at,
    )
    from app.services.e_bridge import forecast_from_input

    forecast = forecast_from_input(
        history=inputs.get("history") or [],
        cutoff_at=cutoff_at,
        horizon_minutes=int(horizon_minutes),
        step_minutes=int(step_minutes),
    )
    row = store.publish_visible_forecast(
        analysis_id=analysis_id,
        cutoff_at=cutoff_at,
        horizon_minutes=int(horizon_minutes),
        input_sha256=inputs["input_sha256"],
        result=forecast,
        lease=lease,
    )
    return {"forecast_id": row.get("id"), "analysis_id": analysis_id,
            "status": forecast.get("status"), "result": forecast}


# ---------- 27 号：D 侧的 grain / 时间过滤 ----------


def filter_counts(counts: dict[str, Any], *, grain: str,
                  start: str | None = None,
                  end: str | None = None) -> dict[str, Any]:
    """按 03 §27 对 C 原生结果做查询过滤（**不跨 analysis 混合**）。

    C 的 ``get_counts`` 一次返回全部 frame + 5min 点、没有过滤参数，所以过滤
    发生在 D。frame 点按 ``recorded_at`` 比时间，5min 点按 ``period_start`` /
    ``period_end`` 比时间。
    """
    points = list(counts.get("points") or [])
    kept = [p for p in points if p.get("grain") == grain]
    kept = [p for p in kept if _in_window(p, start, end, grain)]
    out = dict(counts)
    out["points"] = kept
    out["grain"] = grain
    return out


def check_counts_shape(points: list[dict[str, Any]]) -> None:
    """拒收不是「画面人数」口径的点——**旧的越线进出计数必须在这里挡住**。

    为什么 C 已经验过了，D 还要查一遍：27 号的点最终要喂给
    ``CountPoint``（``quality_status`` / ``source_ids`` **必填**），一旦点脏了
    Pydantic 直接 ValidationError → **500**，而不是一个说得清的错误；而缺
    ``value`` 时它却不报错，会安静返回 ``value: null`` 的半成形数据。
    诚实 422 比含糊的 500 好，比假装成功的空点更好。

    这条校验原本挂在 E 的返回上（2026-10-15 版），2026-10-17 数据源换成 C 的
    ``visible_count_points`` 后**保留了下来**——数据源换了，防线不能跟着撤。
    """
    legacy = {"entries", "exits", "count_line"}
    for index, point in enumerate(points):
        if "value" not in point:
            hit = sorted(set(point) & legacy)
            detail = ("是旧版越线进出计数（entries/exits）" if hit
                      else "缺少 value")
            raise VisiblePipelineError(
                f"第 {index} 个结果点不是画面人数口径：{detail}。"
                "camera_visible_people 只接受带 value 的 "
                "C ``write_points`` 白名单形状点"
            )
        for key in ("quality_status", "source_ids"):
            if not point.get(key):
                raise VisiblePipelineError(
                    f"第 {index} 个结果点缺 {key}：无法溯源的点宁可拒绝，"
                    "也不返回半成形数据"
                )


def _in_window(point: dict[str, Any], start: str | None, end: str | None,
               grain: str) -> bool:
    """判断一个点是否落在 [start, end]（**闭区间**，与 08/09/27 一致）。

    frame 点只有一个时刻 ``recorded_at``，闭区间 ``start ≤ recorded_at ≤ end``。

    5min 点**只保留完整位于查询区间内的窗口**（``period_start >= start 且
    period_end <= end``）。一个 5 分钟点的数值是它自己窗口的中位数，横跨边界的
    窗口若算进来，等于把半个窗口的数值当成完整 5 分钟给出，会让该时段被高估。
    这是 C《致D和E-021a2接收核对与补充答复》§4 明确禁止的（2026-10-17 晚按此
    修改，此前误实现为「有交叠即命中」）。宁可少给，不冒充完整窗口。

    ⚠️ **不能比 ISO 字符串**：``…T00:00:00+08:00`` 与 ``…T01:00:00+09:00`` 是同一
    时刻，但字符串不相等、排序还会反。必须先归一成 UTC ``datetime`` 再比。
    """
    if grain == "frame":
        stamp = _moment(point.get("recorded_at"))
        if stamp is None:
            return True
        lower = _moment(start)
        upper = _moment(end)
        if lower is not None and stamp < lower:
            return False
        if upper is not None and stamp > upper:
            return False
        return True
    period_start = _moment(point.get("period_start"))
    period_end = _moment(point.get("period_end"))
    lower = _moment(start)
    upper = _moment(end)
    # 完整包含，不是「有交叠」：跨边界的窗口会被当成整窗用（见 docstring）。
    # period_start / period_end 缺失时不过滤——宁可多给，不能漏给。
    if lower is not None and period_start is not None and period_start < lower:
        return False
    if upper is not None and period_end is not None and period_end > upper:
        return False
    return True


def _moment(value: Any) -> datetime | None:
    """把（可能带时区、可能是 ISO 字符串）的时间转成 **UTC aware datetime**。

    03 §1 的时间统一带时区；碰到 naive 的补 UTC（与 D 其余链路一致），
    解析不了返回 ``None`` —— 由调用方按「不过滤」处理，宁可多给也不能漏给。
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
