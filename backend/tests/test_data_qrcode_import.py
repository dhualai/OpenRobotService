"""二维码数据导入（QRV1 单码 / QRV2 分片）解码与接口口径。

被测：
- `data_import_service.decode_qr_payload / _assemble_qr_payload`：二维码 payload 解码
- `POST /api/admin/data/import-qrcode`（app/modules/admin/api/data.py）：解码 → GroupEfficiency 落库

二维码格式（见 qr_samples 样例包）：
- 完整数据 = QRV1| + Base64(gzip(UTF-8 JSON))
- 分片每片 = QRV2|{片号}/{总片数}| + 片数据（第 1 片数据以 QRV1| 开头）
- 分片需收集齐 n 片（片号 1..n 连续无重复），按片号排序去头拼接恢复完整 QRV1 payload

不连真库：conftest 已把 app.core.database 换成 MagicMock，接口测试用 monkeypatch
替换 DataHandler._insert_data。
"""
import base64
import gzip
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.modules.admin.api import data as data_api
from app.modules.admin.api.data import data_router
from app.modules.admin.services.data_import_service import (
    _assemble_qr_payload,
    decode_qr_payload,
)

METRICS = {
    "effectWorkTime": {"agv-0001": {"diffTimeSeconds": 3044}},
    "dataIndicators": {"taskNumber": {"totalTasks": 460}},
}

# 新格式：数据平台新版导出为与文件导出 /exportData 同构的标准包装结构（指标字段在
# content[0].data 的每天时间戳 value 内层），二维码导入需直接复用文件导入切分链路
PACKAGED = {
    "project": "XZ-Release",
    "indicator": "GroupEfficiency",
    "content": [
        {
            "data": [
                {"2026-03-04T16:00:00Z": dict(METRICS)},
                {"2026-03-05T16:00:00Z": dict(METRICS)},
            ],
            "start_time": "2026-03-04T16:00:00Z",
            "end_time": "2026-03-06T16:00:00Z",
        }
    ],
    "collection_time": "2026-10-21T05:14:46.384421Z",
}


def make_qr_payload(obj) -> str:
    """构造 QRV1 完整 payload：QRV1| + Base64(gzip(UTF-8 JSON))。"""
    compressed = gzip.compress(json.dumps(obj).encode("utf-8"))
    return "QRV1|" + base64.b64encode(compressed).decode("ascii")


def make_fragment(text: str, idx: int, total: int) -> str:
    """把文本包成 QRV2 分片（第 idx/total 片）。"""
    return f"QRV2|{idx}/{total}|{text}"


# ---------- 解码函数 ----------


def test_decode_qrv1_single_payload():
    """QRV1 单码：剥离前缀 → Base64 → gzip → JSON 还原。"""
    data = decode_qr_payload(payload=make_qr_payload(METRICS))
    assert data == METRICS


def test_decode_qrv2_fragments_reordered():
    """QRV2 分片：乱序提交（第 1 片含 QRV1 前缀），按片号排序拼接后解码。"""
    full = make_qr_payload(METRICS)
    half = len(full) // 2
    part1 = full[:half]
    part2 = full[half:]
    # 第 1 片以 QRV1| 开头（样例包即如此），第 2 片是剩余部分；乱序传入
    data = decode_qr_payload(fragments=[make_fragment(part2, 2, 2), make_fragment(part1, 1, 2)])
    assert data == METRICS


def test_decode_qrv2_three_fragments():
    """QRV2 三片（21 天数据场景）同样能拼接解码。"""
    full = make_qr_payload(METRICS)
    cut1 = len(full) // 3
    cut2 = cut1 * 2
    fragments = [
        make_fragment(full[:cut1], 1, 3),
        make_fragment(full[cut1:cut2], 2, 3),
        make_fragment(full[cut2:], 3, 3),
    ]
    assert decode_qr_payload(fragments=fragments) == METRICS


def test_decode_missing_fragment_raises():
    """缺片：提示缺少的分片号。"""
    full = make_qr_payload(METRICS)
    half = len(full) // 2
    with pytest.raises(ValueError, match="缺少第 2 片"):
        decode_qr_payload(fragments=[make_fragment(full[:half], 1, 2)])


def test_decode_duplicate_fragment_raises():
    """重复片号报错，避免拼接出错。"""
    full = make_qr_payload(METRICS)
    half = len(full) // 2
    with pytest.raises(ValueError, match="第 1 片重复"):
        decode_qr_payload(
            fragments=[make_fragment(full[:half], 1, 2), make_fragment(full[:half], 1, 2)]
        )


def test_decode_fragment_total_mismatch_raises():
    """分片总数不一致：可能是两组不同数据的码混扫。"""
    full = make_qr_payload(METRICS)
    half = len(full) // 2
    with pytest.raises(ValueError, match="总数不一致"):
        decode_qr_payload(
            fragments=[make_fragment(full[:half], 1, 2), make_fragment(full[half:], 2, 3)]
        )


def test_decode_unknown_prefix_raises():
    """非数据平台二维码内容直接报错。"""
    with pytest.raises(ValueError, match="无法识别"):
        decode_qr_payload(payload="https://example.com/xxx")


def test_decode_corrupted_base64_raises():
    """QRV1 前缀正确但压缩数据损坏。"""
    with pytest.raises(ValueError, match="解压失败"):
        decode_qr_payload(payload="QRV1|!!!!not-base64!!!!")


def test_decode_empty_input_raises():
    """空内容报错。"""
    with pytest.raises(ValueError, match="缺少二维码内容"):
        decode_qr_payload(payload=None, fragments=[])
    with pytest.raises(ValueError, match="缺少二维码内容"):
        decode_qr_payload(payload="   ")


def test_assemble_payload_prefers_full_qrv1():
    """同时混入完整码与分片时，直接返回完整码（防混扫）。"""
    full = make_qr_payload(METRICS)
    assembled = _assemble_qr_payload([make_fragment("tail", 1, 2), full])
    assert assembled == full


# ---------- 接口 ----------


@pytest.fixture
def captured():
    """捕获 DataHandler._insert_data 收到的落库数据（替换成带 self 的假实现，实例调用会绑定）。"""
    holder = {}

    async def fake_insert(self, batch_data):
        holder["batch"] = batch_data
        return {"code": 0, "message": "批量插入成功", "data": {"record_ids": [1], "total_count": 1}}

    original = data_api.DataHandler._insert_data
    data_api.DataHandler._insert_data = fake_insert
    yield holder
    data_api.DataHandler._insert_data = original


@pytest.fixture
def client(captured):
    application = FastAPI()
    application.include_router(data_router, prefix="/api/admin")
    with TestClient(application) as test_client:
        yield test_client


def test_import_qrcode_single_payload(client, captured):
    """单码导入：解码 → GroupEfficiency 数据包（切分到当天）→ 落库成功。"""
    resp = client.post(
        "/api/admin/data/import-qrcode",
        json={"project": "P-0001", "payload": make_qr_payload(METRICS)},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["indicator"] == "GroupEfficiency"
    assert body["chunk_count"] == 1

    batch = captured["batch"]
    assert batch["project"] == "P-0001"
    assert batch["indicator"] == "GroupEfficiency"
    content = batch["content"]
    assert len(content) == 1
    today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    assert content[0]["start_time"].startswith(today)
    # 指标 JSON 完整落库（供搬运效率分析页 _find_metrics 读取）
    assert content[0]["data"][0]["effectWorkTime"] == METRICS["effectWorkTime"]


def test_import_qrcode_packaged_new_format(client, captured):
    """新格式（数据平台新版导出）：标准包装结构直接走文件导入切分链路，按天切分落库。"""
    resp = client.post(
        "/api/admin/data/import-qrcode",
        json={"project": "P-0001", "payload": make_qr_payload(PACKAGED)},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["indicator"] == "GroupEfficiency"
    # 两天数据切分成两个 chunk（UTC 时间戳转东八区后按天切分）
    assert body["chunk_count"] == 2

    batch = captured["batch"]
    assert batch["project"] == "P-0001"
    content = batch["content"]
    assert len(content) == 2
    # 2026-03-04T16:00:00Z == 东八区 2026-03-05 00:00 → 切到 03-05 当天
    assert content[0]["start_time"].startswith("2026-03-05")
    assert content[1]["start_time"].startswith("2026-03-06")
    # 指标 JSON 完整落库（供搬运效率分析页 _find_metrics 读取）
    assert content[0]["data"][0]["effectWorkTime"] == METRICS["effectWorkTime"]


def test_import_qrcode_packaged_project_fallback(client, captured):
    """新格式：页面选定项目为空串时回退用包内 project。"""
    resp = client.post(
        "/api/admin/data/import-qrcode",
        json={"project": "", "payload": make_qr_payload(PACKAGED)},
    )
    assert resp.status_code == 200
    assert captured["batch"]["project"] == "XZ-Release"


def test_import_qrcode_fragments(client, captured):
    """分片导入：后端拼接后走同一落库链路。"""
    full = make_qr_payload(METRICS)
    half = len(full) // 2
    resp = client.post(
        "/api/admin/data/import-qrcode",
        json={
            "project": "P-0001",
            "fragments": [
                make_fragment(full[:half], 1, 2),
                make_fragment(full[half:], 2, 2),
            ],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["chunk_count"] == 1
    assert captured["batch"]["content"][0]["data"][0]["dataIndicators"] == METRICS["dataIndicators"]


def test_import_qrcode_not_metrics_raises_400(client):
    """合法二维码但不是搬运效率指标数据 → 400。"""
    resp = client.post(
        "/api/admin/data/import-qrcode",
        json={"project": "P-0001", "payload": make_qr_payload({"hello": "world"})},
    )
    assert resp.status_code == 400
    assert "不是搬运效率指标数据" in resp.json()["detail"]


def test_import_qrcode_bad_payload_raises_400(client):
    """非法二维码内容 → 400。"""
    resp = client.post(
        "/api/admin/data/import-qrcode",
        json={"project": "P-0001", "payload": "not-a-qr-payload"},
    )
    assert resp.status_code == 400


def test_import_qrcode_missing_project_returns_422(client):
    """缺少 project 字段由 Pydantic 拦截。"""
    resp = client.post(
        "/api/admin/data/import-qrcode",
        json={"payload": make_qr_payload(METRICS)},
    )
    assert resp.status_code == 422


def test_import_qrcode_insert_failure_raises_500(client, monkeypatch):
    """落库失败（code != 0）→ 500。"""
    async def fake_insert_fail(self, batch_data):
        return {"code": 500, "message": "插入数据失败", "data": None}

    monkeypatch.setattr(data_api.DataHandler, "_insert_data", fake_insert_fail)
    resp = client.post(
        "/api/admin/data/import-qrcode",
        json={"project": "P-0001", "payload": make_qr_payload(METRICS)},
    )
    assert resp.status_code == 500
    assert "插入数据失败" in resp.json()["detail"]
