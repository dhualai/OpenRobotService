"""转发图保存端点测试：qa/forward_image 存文件、返 URL、鉴权 401。"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ai.api.router import save_forward_image


def _upload_file(filename: str = "forward.png") -> MagicMock:
    f = MagicMock()
    f.filename = filename
    f.read = AsyncMock(return_value=b"\x89PNG\r\n\x1a\nfake")
    return f


def test_save_and_url(tmp_path, monkeypatch):
    import ai.api.router as router
    import ai.config as config

    monkeypatch.setattr(config, "get_ai_config", lambda: MagicMock(
        upload_dir=str(tmp_path), media_url_prefix="/api/ai/media"))
    monkeypatch.setattr(router, "get_ai_config", config.get_ai_config)
    monkeypatch.setattr(router, "_current_user_from_header", lambda h: ("user1", None))

    res = asyncio.run(save_forward_image(_upload_file("摇人吧对话记录.png"), "Bearer t"))

    saved = list((tmp_path / "forward").glob("*.png"))
    assert len(saved) == 1
    assert saved[0].read_bytes() == b"\x89PNG\r\n\x1a\nfake"
    assert res["url"].startswith("/api/ai/media/forward/")
    assert res["url"].endswith(".png")


def test_filename_sanitized(tmp_path, monkeypatch):
    import ai.api.router as router
    import ai.config as config

    monkeypatch.setattr(config, "get_ai_config", lambda: MagicMock(
        upload_dir=str(tmp_path), media_url_prefix="/api/ai/media"))
    monkeypatch.setattr(router, "get_ai_config", config.get_ai_config)
    monkeypatch.setattr(router, "_current_user_from_header", lambda h: ("user1", None))

    res = asyncio.run(save_forward_image(_upload_file("../../etc/passwd"), "Bearer t"))
    saved = list((tmp_path / "forward").iterdir())
    assert len(saved) == 1
    # 路径穿越成分被清洗，文件落在 forward 目录内
    assert (tmp_path / "forward" / res["filename"]).exists()
    assert ".." not in res["filename"]
    assert "/" not in res["filename"]


def test_unauthorized(tmp_path, monkeypatch):
    import ai.api.router as router
    from fastapi import HTTPException

    monkeypatch.setattr(router, "_current_user_from_header", lambda h: (None, None))
    with pytest.raises(HTTPException) as ei:
        asyncio.run(save_forward_image(_upload_file(), ""))
    assert ei.value.status_code == 401
