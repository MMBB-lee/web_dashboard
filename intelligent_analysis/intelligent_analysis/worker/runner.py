"""D owns the sole queue consumer; E only dispatches already-claimed payloads."""
from .knowledge_worker import process_knowledge_job
from .video_worker import process_video_job


def dispatch_analysis_job(job_type: str, payload: dict, *, counter=None, frames=None) -> dict:
    if job_type == "knowledge_index":
        return process_knowledge_job(payload)
    if job_type == "video_analysis":
        if counter is None:
            raise RuntimeError("P2PNet counter is not configured")
        return process_video_job(payload, counter=counter, frames=frames)
    raise ValueError(f"Unsupported E job type: {job_type}")
