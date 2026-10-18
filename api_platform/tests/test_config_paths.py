"""路径配置解析的回归测试（2026-10-15）。

背景：曾出现「uvicorn 与 worker 落到不同 data 根」的事故，表现为
21 号接口读到 pending、19 号问答 500「索引产物不存在」。两个根因：
① ``rag_index_path`` 是**文件**路径，却走了目录探测 ``mkdir`` —— 文件已存在时
   ``mkdir`` 抛 ``FileExistsError``（属 OSError）→ 被静默解析成 %TEMP% 下的目录；
② 探针文件用固定名 ``.write_probe``，两个进程并发探测时后完成者的 ``unlink()``
   抛 ``FileNotFoundError`` → 该进程被误判为「目录不可写」而回退 %TEMP%。

这些用例不碰仓库目录，全部在 ``tmp_path`` 内完成。
"""

from pathlib import Path

from app.core import config as config_module
from app.core.config import _resolve_dir_field, _resolve_file_field


def test_file_field_keeps_filename_and_creates_parent(tmp_path: Path) -> None:
    """文件型配置：解析后仍指向**文件**，父目录被创建，不会被当成目录回退。"""
    target = tmp_path / "nested" / "rag_index.json"

    resolved = Path(_resolve_file_field(str(target)))

    assert resolved.name == "rag_index.json"
    assert resolved.parent.is_dir()
    assert resolved == target


def test_file_field_still_works_when_file_already_exists(tmp_path: Path) -> None:
    """文件已存在时不得触发 %TEMP% 回退（旧实现的 FileExistsError 分支）。"""
    target = tmp_path / "rag_index.json"
    target.write_text("{}", encoding="utf-8")

    resolved = Path(_resolve_file_field(str(target)))

    assert resolved == target
    assert "api_platform_data" not in str(resolved)


def test_dir_field_returns_directory(tmp_path: Path) -> None:
    """目录型配置：解析为目录本身并确保存在。"""
    target = tmp_path / "data_dir"

    resolved = Path(_resolve_dir_field(str(target)))

    assert resolved == target
    assert resolved.is_dir()


def test_probe_names_are_unique_per_call(tmp_path: Path) -> None:
    """可写探测必须用**唯一**探针名，避免并发进程互相删除对方的探针。"""
    seen: list[str] = []

    original_unlink = Path.unlink

    def _spy_unlink(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        seen.append(self.name)
        return original_unlink(self, *args, **kwargs)

    import pytest

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(Path, "unlink", _spy_unlink)
    try:
        for _ in range(3):
            config_module._writable_or_fallback(tmp_path)
    finally:
        monkeypatch.undo()

    probes = [name for name in seen if name.startswith(".write_probe")]
    assert len(probes) == 3
    assert len(set(probes)) == 3, f"探针名重复，会引发并发竞态回退：{probes}"


def test_missing_parent_is_created_before_probe(tmp_path: Path) -> None:
    """父目录不存在时应被创建，而不是直接回退到 %TEMP%。"""
    target = tmp_path / "a" / "b" / "jobs.db"

    resolved = Path(_resolve_file_field(str(target)))

    assert resolved == target
    assert resolved.parent.is_dir()
