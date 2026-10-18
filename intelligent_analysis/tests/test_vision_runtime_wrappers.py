from intelligent_analysis.vision.detector import YoloPersonDetector
from intelligent_analysis.vision.openvino_runtime import is_openvino_available
from intelligent_analysis.vision.video_reader import RecordedVideoReader


def test_vision_wrappers_import_without_heavy_dependencies():
    detector = YoloPersonDetector("yolov8n.pt")
    reader = RecordedVideoReader("sample.mp4")

    assert detector.model_path == "yolov8n.pt"
    assert reader.video_path == "sample.mp4"
    assert is_openvino_available() in {True, False}
