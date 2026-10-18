"""YOLOv8 person detection wrapper with lazy dependency loading."""

from __future__ import annotations

from dataclasses import dataclass

BBox = tuple[float, float, float, float]


@dataclass(frozen=True)
class PersonDetection:
    bbox: BBox
    confidence: float


class YoloPersonDetector:
    def __init__(
        self,
        model_path: str,
        confidence: float = 0.25,
        iou_threshold: float = 0.5,
        device: str = "AUTO",
    ):
        self.model_path = model_path
        self.confidence = confidence
        self.iou_threshold = iou_threshold
        self.device = device
        self._model = None

    def load(self):
        from ultralytics import YOLO

        self._model = YOLO(self.model_path)
        return self

    def detect(self, frame) -> list[PersonDetection]:
        if self._model is None:
            self.load()
        results = self._model.predict(
            frame,
            classes=[0],
            conf=self.confidence,
            iou=self.iou_threshold,
            device=self.device,
            verbose=False,
        )
        detections: list[PersonDetection] = []
        for result in results:
            for bbox_data, confidence in zip(result.boxes.xyxy.cpu().tolist(), result.boxes.conf.cpu().tolist()):
                detections.append(PersonDetection(tuple(bbox_data), float(confidence)))
        return detections
