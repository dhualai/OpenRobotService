"""扫码跳转卡片的场景值归一化口径（subscribe 事件 qrscene_ 前缀剥离）。

被测：`handle_subscribe_event` / `handle_scan_event` / `_send_scan_redirect_card`
（见 app/wechat/api/wechat.py）。

口径（2026-09-30，微信官方事件行为）：
  - 未关注用户扫带参码 → 关注，微信推 subscribe 事件，EventKey 形如
    `qrscene_<scene>`（微信自动加前缀）；
  - 已关注用户扫同一码，推 SCAN 事件，EventKey 才是纯 `<scene>`。
  两类事件都汇聚到 _send_scan_redirect_card，入口必须统一剥前缀：
  不剥的话按 id 查配置表会抛 ValueError 被吞成 qr_cfg=None（静默降级），
  URL 还带着 `qrscene_` 往外发——前端 /app/call 白名单虽放得下划线，
  但 by-scene 接口 int(scene) 直接 400，「车辆信息确认」弹窗不出现；
  「entering 录入行先去录入页核对」的分流同样失效。车体码的主要受众
  正是第一次扫码的未关注用户，这条链路断不得。

不连真库：替换 wechat 模块的 wechat_service / auth_service / log_operation，
以及 app.core.database.db_manager（函数内部局部 import 拿的就是它）。
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlparse

import pytest

from app.core import database as core_database
from app.core.config import settings
from app.models.wechat_qrcode import QrcodeStatus
from app.wechat.api import wechat as wechat_api


@pytest.fixture
def sent(monkeypatch):
    """拦截所有外发客服消息，返回调用记录列表。"""
    calls = []
    monkeypatch.setattr(
        wechat_api.wechat_service, "send_news_message_to_user", lambda **kw: calls.append(kw)
    )
    monkeypatch.setattr(
        wechat_api.auth_service, "register_wechat_user", lambda *a, **k: None
    )
    monkeypatch.setattr(wechat_api, "log_operation", lambda *a, **k: None)
    return calls


@pytest.fixture
def db(monkeypatch):
    """假 Session：默认查无此码，用例按需覆盖 first() 的返回。"""
    fake = MagicMock()
    fake.query.return_value.filter.return_value.first.return_value = None
    monkeypatch.setattr(
        core_database, "db_manager", SimpleNamespace(get_db=lambda: fake)
    )
    return fake


def _row(**over):
    """一条二维码配置行（覆盖 _send_scan_redirect_card 会读到的字段）。"""
    fields = {
        "id": 7,
        "scene_str": "7",
        "name": None,
        "description": None,
        "qrcode_image_url": None,
        "redirect_url": None,
        "status": QrcodeStatus.PUBLISHED,
        "project_code": None,
    }
    fields.update(over)
    return SimpleNamespace(**fields)


def _scene_of(card_kwargs):
    """从卡片 URL 里取 scene 查询参数。"""
    return parse_qs(urlparse(card_kwargs["url"]).query)["scene"][0]


def test_subscribe_qrscene_prefix_stripped(sent, db):
    """未关注用户扫码关注：EventKey 带 qrscene_ 前缀，卡片 scene 必须是纯场景值。"""
    asyncio.run(wechat_api.handle_subscribe_event({
        "FromUserName": "openid-1",
        "ToUserName": "gh_1",
        "EventKey": "qrscene_123",
        "EventTicket": "TICKET",
    }))

    assert len(sent) == 1
    assert _scene_of(sent[0]) == "123"


def test_scan_event_plain_scene_unchanged(sent, db):
    """已关注用户扫码：EventKey 本来就是纯场景值，行为与修复前一致。"""
    asyncio.run(wechat_api.handle_scan_event({
        "FromUserName": "openid-2",
        "ToUserName": "gh_1",
        "EventKey": "123",
    }))

    assert len(sent) == 1
    assert _scene_of(sent[0]) == "123"


def test_subscribe_prefix_hits_qrcode_config(sent, db):
    """剥前缀后要能按主键查到码配置：entering 录入行分流到录入页核对。"""
    db.query.return_value.filter.return_value.first.return_value = _row(
        status=QrcodeStatus.ENTERING, project_code="P-0001",
    )

    asyncio.run(wechat_api.handle_subscribe_event({
        "FromUserName": "openid-3",
        "ToUserName": "gh_1",
        "EventKey": "qrscene_7",
    }))

    assert len(sent) == 1
    # id 必须占路径（微信 OAuth 回跳会丢 query，前端 QrcodeManage/InfoEntry 契约）。
    # 从 FRONTEND_BASE_URL 起拼、不写死 /app：部署值已带 /app 后缀，本地/CI 不带，
    # 相对 base 断言在两种环境下都成立（曾因写死 /app 与 6465982a 的去重修正互相打架）。
    assert f"{settings.FRONTEND_BASE_URL}/admin/info-entry/7" in sent[0]["url"]
    assert _scene_of(sent[0]) == "7"


def test_subscribe_prefix_with_redirect_url_uses_config(sent, db):
    """剥前缀后配置了自己的 redirect_url 就用配置值（不再静默降级到默认落地页）。"""
    db.query.return_value.filter.return_value.first.return_value = _row(
        redirect_url=f"{settings.FRONTEND_BASE_URL}/app/whatever",
    )

    asyncio.run(wechat_api.handle_subscribe_event({
        "FromUserName": "openid-6",
        "ToUserName": "gh_1",
        "EventKey": "qrscene_7",
    }))

    assert len(sent) == 1
    assert sent[0]["url"].startswith(f"{settings.FRONTEND_BASE_URL}/app/whatever?")
    assert _scene_of(sent[0]) == "7"


def test_subscribe_bare_prefix_sends_no_card(sent, db):
    """EventKey 只有 `qrscene_`（无场景值）→ 无处可跳，不推卡片。"""
    asyncio.run(wechat_api.handle_subscribe_event({
        "FromUserName": "openid-4",
        "ToUserName": "gh_1",
        "EventKey": "qrscene_",
    }))

    assert sent == []


def test_normal_follow_without_event_key_unchanged(sent, db):
    """普通关注（无 EventKey）仍走个人中心卡片，不吃扫码链路。"""
    asyncio.run(wechat_api.handle_subscribe_event({
        "FromUserName": "openid-5",
        "ToUserName": "gh_1",
        "EventKey": "",
    }))

    assert len(sent) == 1
    assert sent[0]["url"] == f"{settings.FRONTEND_BASE_URL}/admin/profile"
