"""Save sampled frames with P2PNet head points and the matching ROI count."""
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from app.core.config import get_settings

FORMAT = 'p2pnet-head-points-v1'


def directory(video_id: str, analysis_id: str) -> Path:
    return Path(get_settings().video_storage_dir).parent / 'cap_images' / video_id / analysis_id


def complete(row: dict) -> bool:
    """Old cached analyses must also produce captures when started again."""
    folder = directory(row['video_id'], row['id'])
    try:
        metadata = json.loads((folder / 'captures.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return False
    if not isinstance(metadata, dict) or metadata.get('format') != FORMAT or metadata.get('sample_count') != row['processed_samples']:
        return False
    files = list(folder.glob('frame_*.jpg'))
    return (row['processed_samples'] > 0 and len(files) == row['processed_samples']
            and all(path.is_file() and path.stat().st_size > 0 for path in files))


def annotate(frame, points, roi, threshold: float, value: float):
    import cv2
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    from intelligent_analysis.vision.benchmark import point_count

    points = np.asarray(points, dtype=float).reshape(-1, 3)
    # Use E's exact threshold and ROI rule so red dots match the stored count.
    selected = [point for point in points if point_count(point.reshape(1, 3), roi, threshold)]
    if len(selected) != value:
        raise ValueError('Capture point count differs from analysis result')
    image = frame.copy()
    height, width = image.shape[:2]
    radius = max(2, min(6, round(min(width, height) / 180)))
    for x, y, _ in selected:
        position = (min(width - 1, round(x * width)), min(height - 1, round(y * height)))
        cv2.circle(image, position, radius, (0, 0, 255), -1, cv2.LINE_AA)

    canvas = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(canvas)
    font_size = max(16, min(48, round(height * .045)))
    font_path = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / 'msyh.ttc'
    if font_path.is_file():
        font = ImageFont.truetype(str(font_path), font_size)
        label = f'人数：{int(value)}'
    else:
        font = ImageFont.load_default(size=font_size)
        label = f'People: {int(value)}'
    margin = max(4, round(min(width, height) * .02))
    # Fit the badge even in small input frames.
    while draw.textbbox((0, 0), label, font=font)[2] + 4 * margin > width and font_size > 8:
        font_size -= 1
        font = ImageFont.truetype(str(font_path), font_size) if font_path.is_file() else ImageFont.load_default(size=font_size)
    left, top, right, bottom = draw.textbbox((0, 0), label, font=font)
    badge_width, badge_height = right - left + 2 * margin, bottom - top + 2 * margin
    x, y = max(0, width - margin - badge_width), margin
    draw.rounded_rectangle((x, y, width - margin, y + badge_height), radius=margin, fill=(12, 24, 40))
    draw.text((x + margin - left, y + margin - top), label, font=font, fill=(255, 255, 255))
    return cv2.cvtColor(np.asarray(canvas), cv2.COLOR_RGB2BGR)


def save(frame, *, points, roi, threshold: float, value: float,
         video_id: str, analysis_id: str, index: int, offset: float) -> Path:
    import cv2

    image = annotate(frame, points, roi, threshold, value)
    ok, encoded = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 95])
    if not ok:
        raise OSError('Cannot encode counting capture')
    folder = directory(video_id, analysis_id)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f'frame_{index + 1:06d}_{round(offset * 1000):09d}ms.jpg'
    _write_atomic(target, encoded.tobytes())
    return target


def finish(row: dict, sample_count: int) -> None:
    target = directory(row['video_id'], row['id']) / 'captures.json'
    metadata = dict(format=FORMAT, sample_count=sample_count, video_sha256=row['video_sha256'])
    _write_atomic(target, json.dumps(metadata).encode('utf-8'))


def _write_atomic(target: Path, data: bytes) -> None:
    # Use Python file IO for Chinese Windows paths and atomic replacement on retries.
    temporary = None
    try:
        with NamedTemporaryFile(dir=target.parent, prefix='.capture-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
