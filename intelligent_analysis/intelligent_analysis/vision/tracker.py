"""Lightweight centroid tracking for detected persons."""

from __future__ import annotations

from dataclasses import dataclass

BBox = tuple[float, float, float, float]


@dataclass
class Track:
    track_id: int
    bbox: BBox
    frame_index: int
    lost_frames: int = 0

    @property
    def centroid(self) -> tuple[float, float]:
        return centroid(self.bbox)


@dataclass
class CentroidTracker:
    max_distance: float = 80.0
    max_lost_frames: int = 15
    _tracks: dict[int, Track] = None
    _next_id: int = 1

    def __post_init__(self):
        if self._tracks is None:
            self._tracks = {}

    def update(self, detections: list[BBox], frame_index: int) -> dict[int, Track]:
        candidates = {track.track_id: track for track in self._tracks.values() if track.lost_frames == 0}
        unmatched_detections = set(range(len(detections)))
        assignments = self._assign_tracks(candidates, detections, unmatched_detections)

        for track_id, detection_index in assignments:
            self._tracks[track_id] = Track(
                track_id=track_id,
                bbox=detections[detection_index],
                frame_index=frame_index,
            )

        active_ids = {track_id for track_id, _ in assignments}
        for track_id, track in self._tracks.items():
            if track_id not in active_ids:
                track.lost_frames += 1

        for detection_index in unmatched_detections:
            track_id = self._next_id
            self._next_id += 1
            self._tracks[track_id] = Track(track_id, detections[detection_index], frame_index)

        self._remove_expired_tracks()
        return {track_id: track for track_id, track in self._tracks.items() if track.lost_frames == 0}

    def _assign_tracks(self, candidates, detections, unmatched_detections):
        possible = []
        for track_id, track in candidates.items():
            for index, bbox in enumerate(detections):
                distance = euclidean(track.centroid, centroid(bbox))
                if distance <= self.max_distance:
                    possible.append((distance, track_id, index))

        assignments = []
        used_tracks: set[int] = set()
        for _, track_id, detection_index in sorted(possible):
            if track_id in used_tracks or detection_index not in unmatched_detections:
                continue
            used_tracks.add(track_id)
            unmatched_detections.remove(detection_index)
            assignments.append((track_id, detection_index))
        return assignments

    def _remove_expired_tracks(self):
        expired = [track_id for track_id, track in self._tracks.items() if track.lost_frames > self.max_lost_frames]
        for track_id in expired:
            del self._tracks[track_id]


def centroid(bbox: BBox) -> tuple[float, float]:
    x1, y1, x2, y2 = bbox
    return ((x1 + x2) / 2, (y1 + y2) / 2)


def euclidean(a: tuple[float, float], b: tuple[float, float]) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
