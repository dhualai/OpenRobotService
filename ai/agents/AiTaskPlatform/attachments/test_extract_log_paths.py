"""附件归一化 / 日志路径提取（不连 MinIO）。"""
import os
import zipfile
from pathlib import Path

from ai.agents.AiTaskPlatform.attachments.utils import (
    extract_log_paths,
    is_archive_filename,
    is_log_filename,
    normalize_attachment,
    strip_localproxy,
)


def test_normalize_object_path_only_dict():
    n = normalize_attachment({"object_path": "comment-bucket/a/车端日志.zip", "filename": "车端日志.zip"})
    assert n["path"] == "comment-bucket/a/车端日志.zip"
    assert n["filename"] == "车端日志.zip"


def test_normalize_string_and_localproxy_name():
    n = normalize_attachment("ors/tmp/robot.log.localproxy")
    assert n["filename"].endswith("robot.log.localproxy")
    assert is_log_filename(n["filename"])
    assert strip_localproxy("x.zip.localproxy") == "x.zip"
    assert is_archive_filename("dump.zip.localproxy")


def test_extract_zip_without_log_extension(tmp_path):
    zpath = tmp_path / "车端日志.zip"
    inner = tmp_path / "robot_dump"
    inner.write_text("ERROR lock wait\n", encoding="utf-8")
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.write(inner, arcname="robot_dump")
    paths, _ = extract_log_paths(
        [{"filename": "车端日志.zip", "path": str(zpath)}],
        task_id=None,
    )
    assert paths, "zip 内无 .log 扩展名时也应收录正文"
    assert os.path.isfile(paths[0])
    assert "lock wait" in Path(paths[0]).read_text(encoding="utf-8")


def test_extract_plain_log_file(tmp_path):
    log = tmp_path / "task.log"
    log.write_text("INFO start\n", encoding="utf-8")
    paths, _ = extract_log_paths([{"filename": "task.log", "path": str(log)}])
    assert paths == [str(log)]
