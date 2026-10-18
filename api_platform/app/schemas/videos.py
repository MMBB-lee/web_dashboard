"""录像画面人数计数与短时预测模型（23—28）。

依据 2026-10-14 版《03-接口总览》§8：指标改为 `camera_visible_people`
（指定摄像头固定画面区域在某时刻的**估计可见人数**）；不再有虚拟计数线、
进出方向或累计人次概念。

**2026-10-15（E 0.6.0 正式交付）**：E 正式改用 **P2PNet** 点计数，
DM-Count 密度图求和口径退役（不再做 DM/P2P 比较）。本模块随之：
- `COUNT_MODELS` 只接受 `p2pnet`（旧 `dm_count` 报文按 422 诚实拒绝）；
- `weights_version` 默认 `leader-SHTechA-50604773`（E 侧固定权重版本）；
- `confidence_threshold` 固定 0.5，随计数结果**可序列化读回**（溯源用）；
- 推理运行时固定 PyTorch CPU（E 0.6.0 只在 CPU 上验证；GPU/OpenVINO 未验证）。

口径红线：一律为「录像回放」与「指定画面区域估计可见人数」，
不呈现为入口进出人次或全景区实时在园人数。
"""

from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

DEVICES = "^(CPU|GPU|NPU|AUTO)$"
INFERENCE_ENGINES = "^(pytorch|openvino)$"
#: E 0.6.0 正式口径：只接受 P2PNet 点计数模型。
COUNT_MODELS = "^p2pnet$"
#: E 0.6.0 登记的权重版本（``P2PNetCounter.provenance["weights_version"]``）。
COUNT_WEIGHTS_VERSION = "leader-SHTechA-50604773"
#: E 0.6.0 固定的置信度阈值（不是可调参数）。
COUNT_CONFIDENCE_THRESHOLD = 0.5
GRAINS = "^(frame|5min)$"
#: E 0.6.0 ``CountPoint`` 的枚举约束（D 27 响应同源回传，必须一致）。
#: ``unit`` 是中文「人」；``quality_status`` 仅三值，**没有 ``ok``**。
COUNT_UNITS = "^(人)$"
QUALITY_STATUSES = "^(good|degraded|unavailable)$"
COUNT_METRIC = "camera_visible_people"


class VideoOut(BaseModel):
    """23 GET /videos 每项；不返回原始视频内容。"""

    id: str
    scenic_id: str | None = None
    camera_id: str | None = None
    recorded_start_at: str | None = None
    recorded_end_at: str | None = None
    status: str = Field(description="uploaded | processing | ready | failed")
    permission_record_id: str | None = None
    title: str | None = None
    origin: str = 'registered'
    duration_seconds: float | None = None
    file_digest: str | None = None
    source_note: str | None = None


class VideoAccepted(BaseModel):
    """24 POST /videos 的 201 响应。"""

    video_id: str
    status: str = Field(description="uploaded")
    file_digest: str = Field(description="文件校验摘要")


def _normalized_pair(value: list[float]) -> list[float]:
    """ROI 顶点必须是 [x, y] 两个 0—1 的归一化数值。"""
    if len(value) != 2:
        raise ValueError("ROI 顶点必须是 [x, y] 两个归一化数值")
    x, y = value
    if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
        raise ValueError("ROI 顶点坐标必须落在 [0, 1] 归一化范围内")
    return [float(x), float(y)]


NormalizedPoint = Annotated[list[float], AfterValidator(_normalized_pair)]


class Roi(BaseModel):
    """固定画面统计区域（Region of Interest）：归一化多边形 `[[x, y], ...]`。

    形状已与前端（B）联调确认为归一化 `[x, y][]`，至少 3 个顶点；
    顶点必须是两个 0—1 的数值，不做像素坐标。
    """

    points: list[NormalizedPoint] = Field(min_length=3)


class AnalysisConfig(BaseModel):
    """26 POST /videos/{video_id}/analyses 请求体。

    `roi` 固定摄像头画面统计区域；参数须由 E 用实际视频核验（专项方案 §2）。
    旧版 YOLOv8 越线计数字段（count_line / entry_side / interval_minutes 等）
    一律拒收，避免静默回退到已废弃口径；旧 DM-Count 口径（count_model=dm_count）
    同样拒收——E 0.6.0 起只看 P2PNet，不回退密度图求和。
    """

    model_config = ConfigDict(extra="forbid")

    count_model: str = Field(
        default="p2pnet", pattern=COUNT_MODELS, description="首版固定 p2pnet"
    )
    weights_version: str = Field(
        default=COUNT_WEIGHTS_VERSION,
        min_length=1,
        description="P2PNet 权重标识；首版固定 leader-SHTechA-50604773",
    )
    roi: Roi = Field(description="固定画面统计区域的归一化多边形坐标")
    sample_interval_seconds: int = Field(
        ge=1, le=600, description="抽帧间隔（秒），起步 10"
    )
    aggregation_minutes: int = Field(description="聚合窗口（分钟），首版固定 5")
    inference_engine: str = Field(
        pattern=INFERENCE_ENGINES, description="首版固定 pytorch（E 0.6.0 只在 CPU 验证）"
    )
    device: str = Field(pattern=DEVICES, description="必须是部署机实际支持的设备")
    openvino_version: str | None = Field(
        default=None, description="inference_engine=openvino 时必填并记录转换产物版本"
    )

    @model_validator(mode="after")
    def _contract_rules(self) -> "AnalysisConfig":
        if self.aggregation_minutes != 5:
            raise ValueError("aggregation_minutes 首版固定为 5")
        if self.inference_engine == "openvino" and not self.openvino_version:
            raise ValueError("inference_engine=openvino 时必须提供 openvino_version")
        if self.inference_engine == "pytorch" and self.openvino_version:
            raise ValueError("inference_engine=pytorch 时不应提供 openvino_version")
        return self


class ManualValidation(BaseModel):
    """人工抽样对照结果（密集/稀疏样本分别报告）。"""

    sample_count: int = Field(ge=0)
    mae: float = Field(ge=0)


class VideoAnalysisAccepted(BaseModel):
    """26 POST /videos/{video_id}/analyses 的 202 响应。"""

    job_id: str
    analysis_id: str
    status: str = Field(description="queued")
    video_id: str
    count_metric: str = Field(default=COUNT_METRIC)
    inference_engine: str


class CountPoint(BaseModel):
    """27 GET /videos/{video_id}/counts 每项（通用数字字段 + 计数专属字段）。

    `value` 是固定画面区域的估计可见人数，**不是**该时间窗的累计人次。
    缺帧（quality_status=unavailable）时 `value` 为 **null**，不填 0 掩盖
    无样本（E 0.6.0 契约）。

    字段口径**与 E 0.6.0 ``CountPoint`` 严格对齐**（D 的 27 响应也直接回传
    E 的返回，故不能自造枚举值）：
    - ``unit`` 固定中文 ``"人"``（E 是 ``Literal["人"]``）；
    - ``quality_status`` 只能是 ``good | degraded | unavailable``
      （E 是 ``QualityStatus = Literal[...]``，不接受 ``ok``）；
    - ``source_ids`` 必填非空（E 强制「计数必须能溯源到源」）。
    """

    metric: str = Field(default=COUNT_METRIC)
    value: float | None = Field(default=None, ge=0, description="估计可见人数；unavailable 为 null")
    unit: str = Field(default="人", pattern=COUNT_UNITS)
    grain: str = Field(pattern=GRAINS)
    recorded_at: str | None = Field(default=None, description="grain=frame 时的录制时间")
    offset_seconds: float | None = Field(default=None,ge=0)
    period_start_seconds: float | None = Field(default=None,ge=0)
    period_start: str | None = Field(default=None, description="grain=5min 时的时间窗起点")
    period_end: str | None = Field(default=None, description="grain=5min 时的时间窗终点")
    sample_count: int | None = Field(
        default=None, ge=0, description="grain=5min 时的有效帧数"
    )
    aggregation_rule: str | None = Field(
        default=None, description="grain=5min 时为 median"
    )
    quality_status: str = Field(pattern=QUALITY_STATUSES)
    source_ids: list[str] = Field(min_length=1, description="计数来源（源 ID 列表）")
    published_at: str | None = None
    ingested_at: str | None = None


class CountsResponse(BaseModel):
    """27 响应。

    分析元数据（count_model / weights_version / inference_engine / device /
    可选 openvino_version / 可选 sample_interval_seconds）按前端（B）联调约定
    放在**顶层**，不嵌套分析子对象。

    E 0.6.0 新增溯源字段（同样放顶层，不许嵌套）：
    `confidence_threshold`（固定 0.5）、`weights_sha256`、
    `model_source_revision`。它们直接取自 ``P2PNetCounter.provenance``，
    只暴露权重摘要与源修订号，**绝不暴露权重文件路径或服务器目录**。
    """

    analysis_id: str
    time_basis: str = 'calendar'
    duration_seconds: float | None = None
    video_sha256: str | None = None
    metric: str = Field(default=COUNT_METRIC)
    roi: Roi | None = None
    grain: str = Field(pattern=GRAINS)
    points: list[CountPoint] = Field(default_factory=list)
    count_model: str | None = None
    weights_version: str | None = None
    weights_sha256: str | None = Field(
        default=None, description="E 0.6.0 权重摘要（溯源用，不含路径）"
    )
    model_source_revision: str | None = Field(
        default=None, description="E 0.6.0 模型源码修订号（溯源用）"
    )
    confidence_threshold: float | None = Field(
        default=None, ge=0, le=1, description="E 0.6.0 固定 0.5"
    )
    inference_engine: str | None = None
    device: str | None = None
    openvino_version: str | None = None
    sample_interval_seconds: int | None = None
    manual_validation: ManualValidation | None = None


class ShortForecastRequest(BaseModel):
    """28 POST /videos/{video_id}/short-forecast 请求体。

    预测指标固定 `camera_visible_people`，**不接受进出方向参数**（03 §28）。
    """

    model_config = ConfigDict(extra="forbid")

    analysis_id: str
    cutoff_at: str = Field(description="录制片段内的 ISO 时间；只使用其之前的计数")
    horizon_minutes: int = Field(description="15 | 30 | 60")
    step_minutes: int = Field(default=5, description="首版固定 5")

    @model_validator(mode="after")
    def _steps_allowed(self) -> "ShortForecastRequest":
        if self.horizon_minutes not in (15, 30, 60):
            raise ValueError("horizon_minutes 只能是 15、30 或 60")
        if self.step_minutes != 5:
            raise ValueError("step_minutes 首版固定为 5")
        if self.horizon_minutes % self.step_minutes != 0:
            raise ValueError("horizon_minutes 须为 step_minutes 的整数倍")
        return self


class SpanValidation(BaseModel):
    """短时预测某一跨度（15 / 30 / 60 分钟）的回测误差；无回测结果时为 null。

    `baseline_mae` 对应 E 0.5.0 的 persistence_mae（基线对照，不丢失）；
    `fold_count` 为滚动起点回测的折数（可选）。
    """

    method: str = Field(default="rolling_origin")
    mae: float | None = Field(default=None, ge=0)
    baseline_mae: float | None = Field(default=None, ge=0)
    sample_count: int = Field(ge=0)
    fold_count: int | None = Field(default=None, ge=0)


class ShortForecastPoint(BaseModel):
    """预测点：通用数字字段 + 未来时间点 + 可用区间（前端 B 联调约定）。

    预测的是未来某时刻**画面区域的估计可见人数**；示例中 `period_start=period_end`
    （同一时刻，不是时间窗）。区间统一命名 `lower_bound` / `upper_bound`。
    """

    metric: str = Field(default=COUNT_METRIC)
    value: float = Field(ge=0)
    unit: str = Field(default="人")
    grain: str = Field(default="5min", pattern=GRAINS)
    period_start: str
    period_end: str
    is_forecast: bool = True
    lower_bound: float | None = Field(default=None, ge=0)
    upper_bound: float | None = Field(default=None, ge=0)


class ShortForecastResponse(BaseModel):
    metric: str = Field(default=COUNT_METRIC)
    status: str = Field(description="ready | data_insufficient | quality_insufficient")
    forecast_points: list[ShortForecastPoint] = Field(default_factory=list)
    training_cutoff: str | None = None
    input_window_minutes: int | None = None
    model_version: str | None = None
    validation_error: dict[str, SpanValidation] | None = Field(
        default=None, description="按 15/30/60 分钟跨度索引的回测误差；无回测时为 null"
    )
    reason: str | None = Field(default=None, description="未就绪时的原因说明")
