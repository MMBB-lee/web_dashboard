from intelligent_analysis.vision.tracker import CentroidTracker


def test_tracker_keeps_id_for_nearby_detection():
    tracker = CentroidTracker(max_distance=50)

    first = tracker.update([(10, 10, 30, 30)], frame_index=0)
    second = tracker.update([(12, 12, 32, 32)], frame_index=1)

    assert list(first) == [1]
    assert list(second) == [1]


def test_tracker_creates_new_id_for_far_detection():
    tracker = CentroidTracker(max_distance=20)

    tracker.update([(10, 10, 30, 30)], frame_index=0)
    second = tracker.update([(200, 200, 220, 220)], frame_index=1)

    assert set(second) == {2}


def test_tracker_removes_long_lost_track():
    tracker = CentroidTracker(max_distance=20, max_lost_frames=1)

    tracker.update([(10, 10, 30, 30)], frame_index=0)
    tracker.update([], frame_index=1)
    result = tracker.update([], frame_index=2)

    assert result == {}
