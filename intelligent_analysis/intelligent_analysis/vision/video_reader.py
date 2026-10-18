"""Read authorized recorded videos and expose frame timestamps."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Self


@dataclass
class VideoFrame:
    frame_index: int
    timestamp_seconds: float
    frame: object


class RecordedVideoReader:
    def __init__(self, video_path: str):
        self.video_path = video_path
        self._capture = None

    def __enter__(self) -> Self:
        import cv2

        self._capture = cv2.VideoCapture(self.video_path)
        if not self._capture.isOpened():
            raise ValueError(f"Cannot open video: {self.video_path}")
        return self

    def __exit__(self, exc_type, exc, traceback):
        if self._capture is not None:
            self._capture.release()
        self._capture = None

    def frames(self):
        import cv2

        if self._capture is None:
            raise RuntimeError("Reader must be used as a context manager")
        fps = self._capture.get(cv2.CAP_PROP_FPS)
        if not math.isfinite(fps) or fps <= 0:
            raise ValueError("Video has no valid frame rate; timestamps cannot be inferred")
        frame_index = 0
        while True:
            success, frame = self._capture.read()
            if not success:
                break
            yield VideoFrame(frame_index, frame_index / fps, cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            frame_index += 1
