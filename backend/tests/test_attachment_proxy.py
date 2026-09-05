"""附件代理 Range 支持（iOS <video> 播放必需）与 MIME 推断测试。

背景 bug：摇人吧工单上传 .mov 视频后 iPhone 无法播放、安卓正常——
根因是代理端点不支持 HTTP Range（iOS Safari/WKWebView 强制要求 206 Partial Content）。

端点模块按文件路径用 importlib 直接加载（绕过 app.modules.* 包 __init__ 的连锁导入，
避免踩到测试基建里 create_engine 被 mock 成 MagicMock 破坏 SQLAlchemy 事件系统的问题）。
"""
import importlib.util
import os
import sys
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.utils.attachment_proxy import parse_single_range

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_CALL_ATT = os.path.join(_BACKEND, "app", "modules", "call", "api", "attachment.py")
_TASKS_ATT = os.path.join(_BACKEND, "app", "modules", "tasks", "api", "attachment.py")


def _load_module(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ── parse_single_range 纯函数 ────────────────────────────────────────────


class TestParseSingleRange:
    def test_no_header(self):
        assert parse_single_range(None, 1000) is None
        assert parse_single_range("", 1000) is None

    def test_normal_range(self):
        assert parse_single_range("bytes=0-99", 1000) == (0, 99)

    def test_open_end(self):
        assert parse_single_range("bytes=500-", 1000) == (500, 999)

    def test_suffix_range(self):
        assert parse_single_range("bytes=-100", 1000) == (900, 999)

    def test_suffix_larger_than_size(self):
        assert parse_single_range("bytes=-5000", 1000) == (0, 999)

    def test_end_clamped(self):
        assert parse_single_range("bytes=990-2000", 1000) == (990, 999)

    def test_multi_ranges_unsupported(self):
        assert parse_single_range("bytes=0-1,5-10", 1000) is None

    def test_invalid_format(self):
        assert parse_single_range("bytes=abc", 1000) is None
        assert parse_single_range("bytes=-", 1000) is None
        assert parse_single_range("items=0-10", 1000) is None

    def test_end_before_start(self):
        assert parse_single_range("bytes=900-800", 1000) is None

    def test_start_beyond_size_raises(self):
        with pytest.raises(ValueError):
            parse_single_range("bytes=1000-", 1000)
        with pytest.raises(ValueError):
            parse_single_range("bytes=2000-2100", 1000)


# ── 端点集成测试（mock MinIO，走完整 FastAPI 请求链） ─────────────────────


class _FakeMinioObjectResponse:
    def __init__(self, blob: bytes):
        self.blob = blob
        self.closed = False
        self.conn_released = False

    def stream(self, amt: int):
        for i in range(0, len(self.blob), amt):
            yield self.blob[i:i + amt]

    def close(self):
        self.closed = True

    def release_conn(self):
        self.conn_released = True


class FakeMinioClient:
    """只模拟附件代理用到的接口：get_file_info / check_bucket_exists / get_object。"""

    def __init__(self, data: bytes):
        self.data = data
        self.last_get_object_args = None

    @property
    def client(self):
        return self

    def get_file_info(self, object_path: str):
        return SimpleNamespace(size=len(self.data), content_type="application/octet-stream")

    def check_bucket_exists(self, bucket_name=None):
        return True

    def get_object(self, bucket_name, object_name, offset=0, length=None):
        self.last_get_object_args = (bucket_name, object_name, offset, length)
        end = offset + length if length is not None else len(self.data)
        return _FakeMinioObjectResponse(self.data[offset:end])


DATA = bytes(range(256)) * 4  # 1024 字节伪随机内容


def _mount(monkeypatch, module_path, module_name):
    """加载端点模块并 mock 两处 minio_client：端点自身命名空间 + attachment_proxy 命名空间。"""
    import app.utils.attachment_proxy as ap

    endpoint_mod = _load_module(module_name, module_path)
    fake = FakeMinioClient(DATA)
    monkeypatch.setattr(endpoint_mod, "minio_client", fake)
    monkeypatch.setattr(ap, "minio_client", fake)

    app = FastAPI()
    app.include_router(endpoint_mod.router)
    client = TestClient(app)
    client.fake_minio = fake
    return client


@pytest.fixture()
def call_client(monkeypatch):
    with _mount(monkeypatch, _CALL_ATT, "_call_attachment_under_test") as c:
        yield c


@pytest.fixture()
def tasks_client(monkeypatch):
    with _mount(monkeypatch, _TASKS_ATT, "_tasks_attachment_under_test") as c:
        yield c


class TestCallAttachmentEndpoint:
    URL = "/files/helpdesk-comment/sess_123/278.mov"

    def test_full_response_200_with_accept_ranges(self, call_client):
        resp = call_client.get(self.URL)
        assert resp.status_code == 200
        assert resp.headers["accept-ranges"] == "bytes"
        assert resp.headers["content-length"] == str(len(DATA))
        assert resp.content == DATA

    def test_range_206_partial(self, call_client):
        resp = call_client.get(self.URL, headers={"Range": "bytes=0-99"})
        assert resp.status_code == 206
        assert resp.headers["content-range"] == f"bytes 0-99/{len(DATA)}"
        assert resp.headers["content-length"] == "100"
        assert resp.content == DATA[:100]
        # Range 已透传为 MinIO offset/length
        assert call_client.fake_minio.last_get_object_args == (
            "helpdesk-comment", "sess_123/278.mov", 0, 100,
        )

    def test_range_open_end(self, call_client):
        resp = call_client.get(self.URL, headers={"Range": "bytes=500-"})
        assert resp.status_code == 206
        assert resp.headers["content-range"] == f"bytes 500-{len(DATA) - 1}/{len(DATA)}"
        assert resp.content == DATA[500:]

    def test_range_suffix(self, call_client):
        resp = call_client.get(self.URL, headers={"Range": "bytes=-100"})
        assert resp.status_code == 206
        assert resp.headers["content-range"] == f"bytes {len(DATA) - 100}-{len(DATA) - 1}/{len(DATA)}"
        assert resp.content == DATA[-100:]

    def test_range_multi_returns_full_200(self, call_client):
        resp = call_client.get(self.URL, headers={"Range": "bytes=0-1,5-10"})
        assert resp.status_code == 200
        assert resp.content == DATA

    def test_range_unsatisfiable_416(self, call_client):
        resp = call_client.get(self.URL, headers={"Range": f"bytes={len(DATA)}-"})
        assert resp.status_code == 416
        assert resp.headers["content-range"] == f"bytes */{len(DATA)}"

    def test_mov_mime(self, call_client):
        resp = call_client.get(self.URL)
        # iOS 需要严格的 video/* MIME，.mov 不得落到 application/octet-stream
        assert resp.headers["content-type"] == "video/quicktime"

    def test_bad_path_400(self, call_client):
        assert call_client.get("/files/no-slash").status_code == 400


class TestTasksAttachmentEndpoint:
    URL = "/files/helpdesk-comment/sess_123/video.mp4"

    def test_range_206_partial(self, tasks_client):
        resp = tasks_client.get(self.URL, headers={"Range": "bytes=100-199"})
        assert resp.status_code == 206
        assert resp.headers["content-range"] == f"bytes 100-199/{len(DATA)}"
        assert resp.content == DATA[100:200]

    def test_full_response_200(self, tasks_client):
        resp = tasks_client.get(self.URL)
        assert resp.status_code == 200
        assert resp.headers["accept-ranges"] == "bytes"
        assert resp.content == DATA

    def test_bad_path_400(self, tasks_client):
        assert tasks_client.get("/files/onlybucket").status_code == 400
