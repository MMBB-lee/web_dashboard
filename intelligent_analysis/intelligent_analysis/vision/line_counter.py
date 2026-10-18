"""Virtual-line entry and exit counting with entrance-specific direction rules."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Point = tuple[float, float]
CrossingKind = Literal["entry", "exit"] | None


def orientation(a: Point, b: Point, c: Point) -> float:
    """Cross product (b-a) x (c-a)."""
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def segments_intersect(a: Point, b: Point, c: Point, d: Point) -> bool:
    """Strict segment intersection (handles general crossings)."""
    o1 = orientation(a, b, c)
    o2 = orientation(a, b, d)
    o3 = orientation(c, d, a)
    o4 = orientation(c, d, b)
    return o1 * o2 < 0 and o3 * o4 < 0


def segment_crosses_line(line_a: Point, line_b: Point, p: Point, q: Point) -> bool:
    """Check if segment p→q crosses the line, including boundary cases.

    Returns True if the endpoints are on opposite sides, or if one endpoint
    is on the line and the other is on either side.
    """
    s1 = _sign(orientation(line_a, line_b, p))
    s2 = _sign(orientation(line_a, line_b, q))
    if s1 == 0 and s2 == 0:
        return False  # both on the line, no crossing
    return s1 != s2


@dataclass
class VirtualLineCounter:
    """Count crossings of one virtual line for one specified entrance.

    Image coordinates use x right and y down. Looking from line point A toward B,
    the left side therefore has negative orientation. If ``entry_side='left'``,
    movement from the right side to the left side is an entry; the reverse is an exit.

    Tracks that pass exactly through the line (orientation=0) are handled by
    remembering the last known side and detecting the side change.
    """

    line_start: Point
    line_end: Point
    entry_side: Literal["left", "right"]
    in_count: int = 0
    out_count: int = 0
    _last_positions: dict[str, Point] = field(default_factory=dict)
    _last_sides: dict[str, int] = field(default_factory=dict)

    def update_track(self, track_id: str, position: Point) -> Literal["entry", "exit"] | None:
        previous = self._last_positions.get(track_id)
        self._last_positions[track_id] = position
        if previous is None:
            side = _sign(orientation(self.line_start, self.line_end, position))
            if side != 0:
                self._last_sides[track_id] = side
            return None

        kind = self.classify_crossing(track_id, previous, position)
        if kind == "entry":
            self.in_count += 1
        elif kind == "exit":
            self.out_count += 1
        return kind

    def classify_crossing(
        self, track_id: str, previous: Point, current: Point
    ) -> CrossingKind:
        current_side = _sign(orientation(self.line_start, self.line_end, current))
        previous_side = _sign(orientation(self.line_start, self.line_end, previous))

        # Use remembered side if previous point was on the line
        effective_previous = previous_side
        if effective_previous == 0:
            effective_previous = self._last_sides.get(track_id, 0)

        # No crossing if we can't establish opposite sides
        if current_side == 0 or effective_previous == 0:
            # Update remembered side if we have a non-zero reading
            if current_side != 0:
                self._last_sides[track_id] = current_side
            return None

        if current_side == effective_previous:
            self._last_sides[track_id] = current_side
            return None

        # Sides differ — verify the segment actually crosses
        if not segment_crosses_line(self.line_start, self.line_end, previous, current):
            self._last_sides[track_id] = current_side
            return None

        target_side = -1 if self.entry_side == "left" else 1

        if effective_previous == -target_side and current_side == target_side:
            result = "entry"
        elif effective_previous == target_side and current_side == -target_side:
            result = "exit"
        else:
            result = None

        self._last_sides[track_id] = current_side
        return result


def _sign(value: float) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0
