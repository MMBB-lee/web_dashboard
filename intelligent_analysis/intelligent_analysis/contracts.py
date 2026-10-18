"""Pydantic contracts shared by service-level functions."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

QualityStatus = Literal["good", "degraded", "unavailable"]
ForecastStatus = Literal["ready", "data_insufficient", "quality_insufficient"]
Direction = Literal["entries", "exits"]
Device = Literal["CPU", "GPU", "NPU", "AUTO"]
EntrySide = Literal["left", "right"]
IndexStatus = Literal["pending", "ready", "failed"]


class Citation(BaseModel):
    type: Literal["document", "metric", "event"]
    id: str
    title: str
    url: str | None = None
    date: datetime | None = None
    chunk_id: str | None = None


class ChatResponse(BaseModel):
    conversation_id: UUID
    answer: str
    status: Literal["answered", "insufficient_evidence", "metric_tool_unavailable"]
    citations: list[Citation] = Field(default_factory=list)
    used_metric_queries: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class DocumentMetadata(BaseModel):
    title: str
    publisher: str
    source_url: str | None = None
    published_at: datetime | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    permission_note: str
    applicability_note: str | None = None

    @model_validator(mode="after")
    def aware_dates(self):
        if any(t is not None and t.utcoffset() is None
               for t in (self.published_at, self.valid_from, self.valid_until)):
            raise ValueError("document dates must be timezone-aware")
        if self.valid_from and self.valid_until and self.valid_until < self.valid_from:
            raise ValueError("invalid document applicability range")
        return self


class TextChunk(BaseModel):
    chunk_id: str
    document_id: str
    order: int
    text: str


class IndexAccepted(BaseModel):
    document_id: str
    index_status: Literal["pending"] = "pending"
    job_id: UUID


class ReindexAccepted(BaseModel):
    job_id: UUID
    pending_count: int = Field(ge=1)


class CountLine(BaseModel):
    x1: float = Field(ge=0, le=1)
    y1: float = Field(ge=0, le=1)
    x2: float = Field(ge=0, le=1)
    y2: float = Field(ge=0, le=1)


class LegacyVideoAnalysisConfig(BaseModel):
    detector_model_version: str
    openvino_version: str
    device: Device
    count_line: CountLine
    entry_side: EntrySide
    interval_minutes: Literal[1, 5, 15]
    tracker_config_id: str | None = None


class ROIPoint(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


class ROI(BaseModel):
    model_config = ConfigDict(extra="forbid")
    points: list[ROIPoint] = Field(min_length=3)

    @model_validator(mode="after")
    def nonzero_area(self):
        if len({(p.x, p.y) for p in self.points}) != len(self.points):
            raise ValueError("ROI vertices must be unique; do not repeat the first vertex")
        pairs = list(zip(self.points, self.points[1:] + self.points[:1]))
        area = sum(a.x * b.y - b.x * a.y for a, b in pairs)
        if abs(area) < 1e-10:
            raise ValueError("ROI must have nonzero area")
        def cross(a, b, c):
            return (b.x-a.x)*(c.y-a.y) - (b.y-a.y)*(c.x-a.x)
        for i, (a, b) in enumerate(pairs):
            for j, (c, d) in enumerate(pairs):
                if j <= i + 1 or (i == 0 and j == len(pairs) - 1):
                    continue
                ab_c, ab_d = cross(a,b,c), cross(a,b,d)
                cd_a, cd_b = cross(c,d,a), cross(c,d,b)
                overlap = (max(min(a.x,b.x), min(c.x,d.x)) <= min(max(a.x,b.x), max(c.x,d.x))
                           and max(min(a.y,b.y), min(c.y,d.y)) <= min(max(a.y,b.y), max(c.y,d.y)))
                if overlap and ab_c * ab_d <= 0 and cd_a * cd_b <= 0:
                    raise ValueError("ROI must not self-intersect")
        return self


class BaseVideoAnalysisConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    weights_version: str = Field(min_length=1)
    roi: ROI
    sample_interval_seconds: int = Field(default=10, ge=1, le=600, strict=True)
    aggregation_minutes: Literal[5] = 5
    inference_engine: Literal["pytorch", "openvino"]
    device: Device
    openvino_version: str | None = None

    @model_validator(mode="after")
    def runtime_version(self):
        if self.inference_engine == "openvino" and not self.openvino_version:
            raise ValueError("OpenVINO requires openvino_version")
        if self.inference_engine == "pytorch" and "openvino_version" in self.model_fields_set:
            raise ValueError("PyTorch must omit openvino_version")
        return self


class VideoAnalysisConfig(BaseVideoAnalysisConfig):
    count_model: Literal["p2pnet"]
    confidence_threshold: Literal[0.5] = 0.5


class LegacyDMCountConfig(BaseVideoAnalysisConfig):
    """Historical offline utilities only; never accepted by the formal service."""
    count_model: Literal["dm_count"]


class JobAccepted(BaseModel):
    job_id: UUID
    analysis_id: UUID
    status: Literal["queued"] = "queued"
    inference_engine: Literal["pytorch", "openvino"]


class CountPoint(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    metric: Literal["camera_visible_people"] = "camera_visible_people"
    value: float | None = Field(default=None, ge=0)
    unit: Literal["人"] = "人"
    grain: Literal["frame", "5min"]
    recorded_at: datetime | None = None
    period_start: datetime | None = None
    period_end: datetime | None = None
    sample_count: int = Field(default=1, ge=0)
    aggregation_rule: Literal["median"] | None = None
    quality_status: QualityStatus
    source_ids: list[str] = Field(default_factory=list)
    published_at: datetime | None = None
    ingested_at: datetime | None = None

    @model_validator(mode="after")
    def time_and_quality(self):
        if not self.source_ids:
            raise ValueError("count source_ids are required")
        timestamps = [self.recorded_at, self.period_start, self.period_end,
                      self.published_at, self.ingested_at]
        if any(t is not None and t.utcoffset() is None for t in timestamps):
            raise ValueError("timestamps must be timezone-aware")
        if self.grain == "frame":
            if self.recorded_at is None or self.period_start or self.period_end:
                raise ValueError("frame requires recorded_at only")
            if self.aggregation_rule is not None:
                raise ValueError("frame is not an aggregation")
        else:
            if self.period_start is None or self.period_end is None or self.recorded_at:
                raise ValueError("5min requires period_start and period_end")
            if (self.period_end - self.period_start).total_seconds() != 300:
                raise ValueError("5min windows must be exactly five minutes")
            if self.aggregation_rule != "median":
                raise ValueError("5min requires median aggregation")
        if self.value is None and self.quality_status != "unavailable":
            raise ValueError("missing counts must be unavailable")
        if self.quality_status == "unavailable" and self.value is not None:
            raise ValueError("unavailable counts must have null value")
        if self.value is not None and self.sample_count < 1:
            raise ValueError("count requires valid samples")
        return self


class ManualValidation(BaseModel):
    sample_count: int = Field(ge=0)
    mae: float = Field(ge=0)


class CountsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    video_id: UUID
    analysis_id: UUID
    recorded_start_at: datetime | None = None
    recorded_end_at: datetime | None = None
    metric: Literal["camera_visible_people"] = "camera_visible_people"
    status: Literal["ready", "analysis_not_ready"]
    points: list[CountPoint] = Field(default_factory=list)
    manual_validation: ManualValidation | None = None
    roi: ROI | None = None
    analysis: VideoAnalysisConfig | None = None

    @model_validator(mode="after")
    def ready_metadata(self):
        if any(t is not None and t.utcoffset() is None
               for t in (self.recorded_start_at, self.recorded_end_at)):
            raise ValueError("recording timestamps must be timezone-aware")
        if self.recorded_start_at and self.recorded_end_at:
            if self.recorded_end_at <= self.recorded_start_at:
                raise ValueError("recording end must follow start")
            for p in self.points:
                start = p.recorded_at or p.period_start
                end = p.recorded_at or p.period_end
                if start < self.recorded_start_at or end > self.recorded_end_at:
                    raise ValueError("count lies outside recording")
        if self.status == "ready" and (self.roi is None or self.analysis is None):
            raise ValueError("ready counts require ROI and analysis provenance")
        if self.status != "ready" and self.points:
            raise ValueError("unready analysis cannot expose partial results")
        if self.analysis and self.roi != self.analysis.roi:
            raise ValueError("ROI does not match analysis configuration")
        return self


class ForecastPoint(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    target_time: datetime
    value: float = Field(ge=0)
    is_forecast: Literal[True] = True
    lower: float | None = Field(default=None, ge=0)
    upper: float | None = Field(default=None, ge=0)


class ForecastResponse(BaseModel):
    metric: Literal["camera_visible_people"] = "camera_visible_people"
    status: ForecastStatus
    forecast_points: list[ForecastPoint] = Field(default_factory=list)
    training_cutoff: datetime | None = None
    model_version: str
    input_window_minutes: int | None = None
    validation_error: dict[str, object] | None = None
    reason: str | None = None
