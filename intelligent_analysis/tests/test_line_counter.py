from intelligent_analysis.vision.line_counter import VirtualLineCounter


def test_left_entry_when_crossing_below_to_above():
    counter = VirtualLineCounter((0, 0.5), (1, 0.5), entry_side="left")

    assert counter.update_track("p1", (0.2, 0.6)) is None
    assert counter.update_track("p1", (0.8, 0.4)) == "entry"
    assert counter.in_count == 1
    assert counter.out_count == 0


def test_exit_when_crossing_above_to_below():
    counter = VirtualLineCounter((0, 0.5), (1, 0.5), entry_side="left")

    counter.update_track("p1", (0.2, 0.4))
    assert counter.update_track("p1", (0.8, 0.6)) == "exit"
    assert counter.out_count == 1


def test_right_entry_uses_reverse_direction():
    counter = VirtualLineCounter((0, 0.5), (1, 0.5), entry_side="right")

    counter.update_track("p1", (0.2, 0.4))
    assert counter.update_track("p1", (0.8, 0.6)) == "entry"
    assert counter.in_count == 1


def test_no_crossing_does_not_count():
    counter = VirtualLineCounter((0, 0.5), (1, 0.5), entry_side="left")

    counter.update_track("p1", (0.2, 0.4))
    assert counter.update_track("p1", (0.8, 0.45)) is None
    assert counter.in_count == 0
    assert counter.out_count == 0
