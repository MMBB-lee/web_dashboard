"""D-dispatched video calculation; no queue claims or database writes."""
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..contracts import VideoAnalysisConfig
from ..vision.density_counter import process_visible_frames
from ..vision.video_reader import RecordedVideoReader


@dataclass
class FrameData:
    frame_index: int
    timestamp_seconds: float
    frame: Any


def process_video_job(job_payload: dict, *, counter, frames=None) -> dict:
    config = VideoAnalysisConfig.model_validate(job_payload["config"])
    counter.validate_config(config.model_dump(mode="json", exclude_none=True))
    start = datetime.fromisoformat(job_payload["recorded_start_at"])
    end = datetime.fromisoformat(job_payload["recorded_end_at"])
    if frames is None:
        frames = _read_video_frames(job_payload["file_path"])
    points = process_visible_frames(frames, config, counter, start, end,
                                    job_payload["source_ids"])
    return {"video_id": job_payload["video_id"], "analysis_id": job_payload["analysis_id"],
            "recorded_start_at": start.isoformat(), "recorded_end_at": end.isoformat(),
            "metric": "camera_visible_people", "status": "ready", "points": points,
            "roi": config.roi.model_dump(mode="json"),
            "analysis": config.model_dump(mode="json", exclude_none=True),
            "manual_validation": None}


def _read_video_frames(path):
    with RecordedVideoReader(path) as reader:
        yield from reader.frames()
