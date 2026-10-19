from datetime import datetime
from types import SimpleNamespace

import pytest

from app.services import capture_library, counting_captures as captures


@pytest.fixture
def capture_root(tmp_path, monkeypatch):
    video_root = tmp_path / '中文项目' / 'videos'
    video_root.mkdir(parents=True)
    monkeypatch.setattr(captures, 'get_settings', lambda: SimpleNamespace(capture_storage_dir=str(video_root.parent / 'cap_images')))
    return video_root.parent / 'cap_images'


def row(analysis='analysis-a'):
    return dict(id=analysis, video_id='video-a', video_sha256='a' * 64, processed_samples=1)


def login(client):
    result = client.post('/api/v1/auth/login', json={'username': 'viewer1', 'password': 'viewer-pass-123'})
    assert result.status_code == 200


def test_timestamp_flat_directories_same_second_collision_and_retry(capture_root, monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 19, 18, 50, 12, tzinfo=tz)
    monkeypatch.setattr(captures, 'datetime', Clock)
    first = captures.prepare(row())
    second = captures.prepare(row('analysis-b'))
    assert first.name == '2026-10-19T18-50-12'
    assert second.name == '2026-10-19T18-50-13'
    assert first.parent == capture_root == second.parent
    (first / 'frame_000001.jpg').write_bytes(b'a capture')
    captures.finish(row(), 1, first)
    assert captures.complete(row())
    # Corrupt completion data must not lose the stable task-to-folder association.
    (first / 'captures.json').write_text('{"format":"raw"}', encoding='utf-8')
    assert not captures.complete(row())
    assert captures.prepare(row()) == first
    assert captures.directory('video-a', 'analysis-a') == first
    assert len(list(capture_root.iterdir())) == 2


def test_missing_library_is_read_only_and_capture_routes_require_login(capture_root, client):
    for endpoint in ('capture-folders', 'capture-images?folder=missing', 'capture-image?folder=missing&filename=x.jpg'):
        assert client.get('/api/v1/videos/' + endpoint).status_code == 401
    login(client)
    assert client.get('/api/v1/videos/capture-folders').json()['data'] == []
    assert not capture_root.exists()


def test_complete_legacy_uuid_directory_does_not_prevent_timestamp_output(capture_root):
    legacy = capture_root / 'video-a' / 'analysis-a'
    legacy.mkdir(parents=True)
    (legacy / 'frame_000001.jpg').write_bytes(b'old image')
    captures.finish(row(), 1, legacy)
    assert not captures.complete(row())
    current = captures.prepare(row())
    assert current.parent == capture_root
    assert captures.FOLDER_PATTERN.fullmatch(current.name)
    assert (legacy / 'frame_000001.jpg').is_file()


def test_browse_new_and_legacy_folders_and_serve_only_images(capture_root, client):
    from PIL import Image
    current = captures.prepare(row())
    Image.new('RGB', (12, 10), 'red').save(current / '检测结果.jpg')
    legacy = capture_root / 'old-video' / 'old-analysis'
    legacy.mkdir(parents=True)
    Image.new('RGB', (8, 8), 'blue').save(legacy / 'frame_old.png')
    (legacy / 'captures.json').write_text('{}')
    login(client)
    folders = client.get('/api/v1/videos/capture-folders').json()['data']
    assert len(folders) == 2
    new = next(f for f in folders if not f['legacy'])
    assert new['id'] == current.name and new['image_count'] == 1
    assert new['analysis_id'] == 'analysis-a'
    old = next(f for f in folders if f['legacy'])
    assert old['id'] == 'old-video/old-analysis' and old['name'].startswith('历史记录')
    items = client.get('/api/v1/videos/capture-images', params={'folder': new['id']}).json()['data']
    assert [i['name'] for i in items] == ['检测结果.jpg']
    response = client.get(items[0]['url'])
    assert response.status_code == 200 and response.headers['content-type'] == 'image/jpeg'
    assert response.content == (current / '检测结果.jpg').read_bytes()
    assert response.headers['cache-control'] == 'private, no-store'
    assert 'analysis.json' not in [i['name'] for i in items]
    assert client.get('/api/v1/videos/capture-images', params={'folder': old['id']}).json()['data'][0]['name'] == 'frame_old.png'


@pytest.mark.parametrize('folder', ['../outside', '/outside', 'run/../outside', 'a/b/c', '.hidden', 'a\\b', 'C:/Windows'])
def test_capture_folder_traversal_is_rejected(capture_root, client, folder):
    login(client)
    assert client.get('/api/v1/videos/capture-images', params={'folder': folder}).status_code == 404


@pytest.mark.parametrize('name', ['../secret.jpg', 'nested/x.jpg', 'nested\\x.jpg', 'captures.json', 'image.jpg:secret', '.secret.png'])
def test_capture_file_traversal_and_metadata_are_rejected(capture_root, client, name):
    folder = captures.prepare(row())
    login(client)
    assert client.get('/api/v1/videos/capture-image', params={'folder': folder.name, 'filename': name}).status_code == 404


def test_capture_symlinks_cannot_expose_files_outside_library(capture_root, client, tmp_path):
    folder = captures.prepare(row())
    secret = tmp_path / 'private.jpg'
    secret.write_bytes(b'private')
    try:
        (folder / 'linked.jpg').symlink_to(secret)
        (capture_root / 'outside').symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        pytest.skip('Host does not permit symlink creation')
    login(client)
    assert capture_library.images(folder.name) == []
    assert client.get('/api/v1/videos/capture-image', params={'folder': folder.name, 'filename': 'linked.jpg'}).status_code == 404
    assert client.get('/api/v1/videos/capture-images', params={'folder': 'outside'}).status_code == 404
    assert all(f['id'] != 'outside' for f in capture_library.folders())
