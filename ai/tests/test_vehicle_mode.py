# -*- coding: utf-8 -*-
"""车型定制模式单测（扫码入口 /mode/confirm，XQE 试点）。

覆盖：
- 手册清单生成（目录扫描 / title / url 拼接 / 空目录容错）
- 模式注册主流程（按 scene 命中已发布行 → metadata 写入 / 查无此码拦截 /
  非 published 拦截 / scene 非法拦截 / 车型以库内为准 / 幂等覆盖）
- 扫码行查询规则（按主键精确取行，不按车型/项目兜底匹配）

DB 用 sqlite 内存库建真实表（不走 MySQL）；memory 用 conftest 的 mock_memory。
"""
import shutil
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

# 收集阶段先 import 真包占位：test_report 的 _preload_database_module 在
# 「sys.modules 里还没有 ai 真包」时会连假 ai/ai.core 空包一起造出来，
# 污染后续所有 ai.api import（ImportError: get_llm_client from 'ai.core'）。
# 真包先进 sys.modules → report 只替换 ai.core.database 模块（不造假包），
# 该替换由下方 fixture 的运行时解析兜住。
import ai.core.database  # noqa: F401


# ================================================================
# 夹具
# ================================================================

@pytest.fixture
def vehicle_db(monkeypatch):
    """sqlite 内存库建 wechat_qrcodes 真实表，并把 database.engine 指过去。

    StaticPool + check_same_thread=False：内存库单连接复用（否则每个新连接
    都是空库）且允许跨线程（_lookup_qrcode 走 asyncio.to_thread）；
    生产 MySQL 无此问题，仅 sqlite 内存库需要。

    ⚠️ 一律从 sys.modules 直取模块（不走 import as 属性链）：test_report 的
    _preload_database_module 会把 sys.modules["ai.core.database"] 换成手工副本，
    而被测函数内是 from-import（命中 sys.modules 副本）——`import as` 走父包
    属性链拿到的是真包旧对象，patch 它对 from-import 不生效（双实例撕裂，
    0904 全量回归同款坑）。sys.modules.get 与 from-import 命中同一实例。"""
    import sys

    db_mod = sys.modules.get("ai.core.database")
    if db_mod is None:
        import ai.core.database as db_mod  # noqa: F811

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    db_mod.Base.metadata.create_all(engine, tables=[db_mod.WechatQrcode.__table__])
    monkeypatch.setattr(db_mod, "engine", engine)
    yield engine
    engine.dispose()


@pytest.fixture
def kb_tmp(tmp_path):
    """临时 kb 目录：company/XQE/ 平铺两篇 + 一个非 md 文件（应被忽略）。

    1010 起车型目录平铺（无 manual 层），sub_domain 即车型串。"""
    xqe = tmp_path / "company" / "XQE"
    xqe.mkdir(parents=True)
    (xqe / "XQE下线调试与部署文档.md").write_text("# 手册A\n", encoding="utf-8")
    (xqe / "故障分叉树.md").write_text("# 手册B\n", encoding="utf-8")
    (xqe / "ignore.txt").write_text("not md", encoding="utf-8")
    return tmp_path


def _seed_qrcode(engine, qr_id=101, model="XQE", project="试点项目",
                 customer="试点客户", location="上海",
                 status="published", project_code="P-001"):
    """插一条录入信息行（行 id 即扫码 scene 值）。"""
    import sys

    from sqlalchemy import insert

    db_mod = sys.modules.get("ai.core.database")
    if db_mod is None:
        import ai.core.database as db_mod  # noqa: F811
    with engine.begin() as conn:
        conn.execute(insert(db_mod.WechatQrcode).values(
            id=qr_id, status=status, project_code=project_code,
            project_name=project, project_location=location,
            customer_name=customer, vehicle_model=model,
        ))


def _make_request(session_id="sess-xqe-1", scene="101", model="XQE",
                  project_name="弹窗项目", customer_name="弹窗客户", vehicle_code=""):
    """构造请求：项目/客户默认特意与库内 seed 值不同，用于验证「以库内为准」。"""
    from ai.api.vehicle_mode import ModeConfirmRequest
    return ModeConfirmRequest(
        session_id=session_id, scene=scene, model=model,
        project_name=project_name, customer_name=customer_name,
        vehicle_code=vehicle_code,
    )


# ================================================================
# 手册清单
# ================================================================

def test_list_manual_docs_scans_dir(kb_tmp):
    from ai.api.vehicle_mode import list_manual_docs

    docs = list_manual_docs("XQE", kb_root=kb_tmp, media_prefix="/api/ai/media")
    titles = [d["title"] for d in docs]
    assert titles == ["XQE下线调试与部署文档", "故障分叉树"]  # sorted 序
    assert docs[0]["url"] == "/api/ai/media/kb/company/XQE/XQE下线调试与部署文档.md"
    assert docs[0]["path"] == "company/XQE/XQE下线调试与部署文档.md"


def test_list_manual_docs_missing_dir_returns_empty(tmp_path):
    from ai.api.vehicle_mode import list_manual_docs

    # 车型目录不存在 → 空清单（手册可选，不阻塞注册）
    assert list_manual_docs("XQE", kb_root=tmp_path) == []
    # 空车型 → 空清单
    assert list_manual_docs("", kb_root=tmp_path) == []


def test_model_to_domain():
    from ai.api.vehicle_mode import model_to_domain, model_to_subpath

    # 0930 定稿：车型知识挂 company 域子目录（kb/company/{车型}/），不建独立域
    assert model_to_domain("XQE") == "company"
    assert model_to_subpath("XQE") == "XQE"
    assert model_to_subpath(" xqe ") == "XQE"
    assert model_to_subpath("") == ""


# ================================================================
# 扫码行查询规则（按主键精确取行）
# ================================================================

async def test_lookup_qrcode_by_id(vehicle_db):
    from ai.api.vehicle_mode import _lookup_qrcode

    _seed_qrcode(vehicle_db, qr_id=101, project="项目甲")
    _seed_qrcode(vehicle_db, qr_id=202, project="项目乙")

    row = await _lookup_qrcode("101")
    assert row is not None and row.project_name == "项目甲"
    # 同车型多台车各 id 独立：只命中自己那一行，不串车
    row2 = await _lookup_qrcode("202")
    assert row2 is not None and row2.project_name == "项目乙"
    # 查无此码
    assert await _lookup_qrcode("999") is None


# ================================================================
# 模式注册主流程
# ================================================================

async def test_register_mode_success(vehicle_db, kb_tmp, mock_memory, monkeypatch):
    from ai.api import vehicle_mode as vm

    _seed_qrcode(vehicle_db, qr_id=101, model="XQE", project="库内项目", customer="库内客户")
    monkeypatch.setattr(vm, "_default_kb_root", lambda: kb_tmp)
    mock_memory.get_memory  # conftest fixture

    # 请求里的项目/客户故意与库内不同 → 一律以库内那行为准
    resp = await vm.register_mode(_make_request(scene="101"), memory_manager=mock_memory)
    assert resp["code"] == 0
    assert resp["data"]["confirmed"] is True
    assert resp["data"]["model"] == "XQE"
    assert resp["data"]["domain"] == "company"
    # SOP 外显清单排除引导设施（故障分叉树不外显）→ 只剩手册A
    assert len(resp["data"]["manual_docs"]) == 1
    assert resp["data"]["manual_docs"][0]["title"] == "XQE下线调试与部署文档"

    mem = await mock_memory.get_memory("sess-xqe-1")
    mode = mem.metadata["vehicle_mode"]
    assert mode["model"] == "XQE"
    assert mode["domain"] == "company"
    assert mode["scene"] == "101"
    assert mode["project_name"] == "库内项目"
    assert mode["customer_name"] == "库内客户"
    assert mode["location"] == "上海"


async def test_register_mode_unknown_scene_blocks(vehicle_db, mock_memory):
    from ai.api import vehicle_mode as vm

    # 查无此码 → code=1 报错拦住（实验阶段不降级），且不写 metadata
    resp = await vm.register_mode(_make_request(scene="999"), memory_manager=mock_memory)
    assert resp["code"] == 1
    assert "未录入信息或不可用" in resp["message"]
    mem = await mock_memory.get_memory("sess-xqe-1")
    assert "vehicle_mode" not in mem.metadata


async def test_register_mode_non_published_blocks(vehicle_db, mock_memory):
    from ai.api import vehicle_mode as vm

    # 码存在但未发布（未出厂）→ 同样拦住，不降级
    _seed_qrcode(vehicle_db, status="init")
    resp = await vm.register_mode(_make_request(), memory_manager=mock_memory)
    assert resp["code"] == 1
    assert "未录入信息或不可用" in resp["message"]
    mem = await mock_memory.get_memory("sess-xqe-1")
    assert "vehicle_mode" not in mem.metadata


async def test_register_mode_invalid_scene_blocks(mock_memory):
    import pydantic

    from ai.api import vehicle_mode as vm

    # 空串在请求模型层即拦截（FastAPI 层 422）
    with pytest.raises(pydantic.ValidationError):
        _make_request(scene="")
    # 非纯数字（不归一、不查库、不按车型兜底）→ 报错拦住
    for bad in ("abc", "10a", "1.0", "-1", "10 1"):
        resp = await vm.register_mode(_make_request(scene=bad), memory_manager=mock_memory)
        assert resp["code"] == 1
        assert "扫码信息无效" in resp["message"]


async def test_register_mode_empty_model_blocked(vehicle_db, mock_memory):
    import pydantic

    from ai.api import vehicle_mode as vm

    # 请求模型层即拦截（FastAPI 层 422）
    with pytest.raises(pydantic.ValidationError):
        _make_request(model="")
    # 防御分支：绕过 pydantic 校验直接构造 + 行内车型也为空 → 兜底报错
    _seed_qrcode(vehicle_db, model=None)
    req = vm.ModeConfirmRequest.model_construct(
        session_id="sess-xqe-1", scene="101", model="", project_name="",
        customer_name="", vehicle_code="",
    )
    resp = await vm.register_mode(req, memory_manager=mock_memory)
    assert resp["code"] == 1
    assert "未录入车型信息" in resp["message"]


async def test_register_mode_idempotent_overwrite(vehicle_db, kb_tmp, mock_memory, monkeypatch):
    """刷新页面重复调用 = 覆盖重注册（同一 session 换车也以最后一次为准）。"""
    from ai.api import vehicle_mode as vm

    _seed_qrcode(vehicle_db, qr_id=101, project="项目甲")
    _seed_qrcode(vehicle_db, qr_id=202, project="项目乙")
    monkeypatch.setattr(vm, "_default_kb_root", lambda: kb_tmp)

    r1 = await vm.register_mode(_make_request(scene="101"), memory_manager=mock_memory)
    assert r1["code"] == 0
    r2 = await vm.register_mode(_make_request(scene="202"), memory_manager=mock_memory)
    assert r2["code"] == 0

    mem = await mock_memory.get_memory("sess-xqe-1")
    assert mem.metadata["vehicle_mode"]["scene"] == "202"
    assert mem.metadata["vehicle_mode"]["project_name"] == "项目乙"


async def test_register_mode_model_from_db_and_uppercased(vehicle_db, kb_tmp, mock_memory, monkeypatch):
    """车型以库内那行为准，并归一为大写（请求里的车型不参与）。

    归一不是洁癖：kb 目录名/入库 sub_domain 都是大写，pipeline 按 model 拼
    检索 filter，小写会过滤出空集（0930 实锤：company 域 0+0）。
    """
    from ai.api import vehicle_mode as vm

    _seed_qrcode(vehicle_db, qr_id=101, model="xqe")
    monkeypatch.setattr(vm, "_default_kb_root", lambda: kb_tmp)

    # 请求填别的车型：结果仍取库内的 xqe → 归一 XQE
    resp = await vm.register_mode(_make_request(scene="101", model="OTHER"), memory_manager=mock_memory)
    assert resp["code"] == 0
    assert resp["data"]["model"] == "XQE"
    mem = await mock_memory.get_memory("sess-xqe-1")
    assert mem.metadata["vehicle_mode"]["model"] == "XQE"


async def test_register_mode_falls_back_to_request_model(vehicle_db, kb_tmp, mock_memory, monkeypatch):
    """行内车型为空（异常数据）→ 用弹窗确认值兜底，仍归一为大写。"""
    from ai.api import vehicle_mode as vm

    _seed_qrcode(vehicle_db, qr_id=101, model=None)
    monkeypatch.setattr(vm, "_default_kb_root", lambda: kb_tmp)

    resp = await vm.register_mode(_make_request(scene="101", model="xqe"), memory_manager=mock_memory)
    assert resp["code"] == 0
    assert resp["data"]["model"] == "XQE"


# ================================================================
# 开场大方向引导题（0930：随机 5 + 故障码保底，服务端直出）
# ================================================================

@pytest.fixture
def kb_fork(tmp_path):
    """带 9 大类顶层节点的分叉树（### 子节点不算顶层）。"""
    xqe = tmp_path / "company" / "XQE"
    xqe.mkdir(parents=True)
    (xqe / "故障分叉树.md").write_text(
        "# XQE 故障分叉树\n"
        "## 1. 车辆停着不动\n### 1.1 行驶途中\n"
        "## 2. 取货失败\n## 3. 卸货/放货异常\n## 4. 堆叠/堆垛失败\n"
        "## 5. 界面报故障码\n## 6. 自检失败/开机异常\n## 7. 充电异常\n"
        "## 8. 车体异常（异响/漏油）\n## 9. 多车/调度异常\n",
        encoding="utf-8")
    return tmp_path


def test_parse_fork_tree_top_levels(kb_fork):
    from ai.api.vehicle_mode import parse_fork_tree_choices

    levels = parse_fork_tree_choices("XQE", kb_root=kb_fork)
    assert len(levels) == 9
    assert levels[0] == "车辆停着不动"
    assert levels[4] == "界面报故障码"  # 序号前缀已剥
    assert all("1.1" not in lv for lv in levels)  # ### 子节点不算


def test_parse_fork_tree_missing_returns_empty(tmp_path):
    from ai.api.vehicle_mode import parse_fork_tree_choices

    # 无分叉树文件/无域目录 → 空（开场题可选，空库退化为纯输入框）
    assert parse_fork_tree_choices("XQE", kb_root=tmp_path) == []
    assert parse_fork_tree_choices("", kb_root=tmp_path) == []


def test_build_opening_random5_keeps_code():
    # 故障码保底 + 随机抽满 5；30 次内 9 类全部有出场（随机性不被固定前 5 垄断）
    import random

    from ai.api.vehicle_mode import build_opening

    levels = [f"类目{i}" for i in range(9)]
    levels[4] = "界面报故障码"
    rng = random.Random(42)
    seen = set()
    for _ in range(30):
        op = build_opening(levels, rng=rng)
        assert op is not None
        assert len(op["choices"]) == 4 and len(op["collapsed_choices"]) == 5
        assert "界面报故障码" in op["choices"]          # 保底在常驻
        assert not set(op["choices"]) & set(op["collapsed_choices"])  # 不重叠
        assert op["question"] and op["hint"]
        seen.update(op["choices"] + op["collapsed_choices"])
    assert seen == set(levels)


def test_build_opening_insufficient():
    from ai.api.vehicle_mode import build_opening

    assert build_opening([]) is None
    assert build_opening(["只有一类"]) is None


async def test_register_mode_returns_opening(vehicle_db, kb_fork, mock_memory, monkeypatch):
    from ai.api import vehicle_mode as vm

    _seed_qrcode(vehicle_db)
    monkeypatch.setattr(vm, "_default_kb_root", lambda: kb_fork)
    resp = await vm.register_mode(_make_request(), memory_manager=mock_memory)
    assert resp["code"] == 0
    op = resp["data"]["opening"]
    assert op is not None
    assert len(op["choices"]) == 4 and len(op["collapsed_choices"]) == 5
    assert "界面报故障码" in op["choices"]
    assert op["hint"]


async def test_register_mode_opening_none_without_tree(vehicle_db, kb_tmp, mock_memory, monkeypatch):
    # 无分叉树（kb_tmp 的分叉树无 ## 顶层）→ opening=None，注册照常成功
    from ai.api import vehicle_mode as vm

    _seed_qrcode(vehicle_db)
    monkeypatch.setattr(vm, "_default_kb_root", lambda: kb_tmp)
    resp = await vm.register_mode(_make_request(), memory_manager=mock_memory)
    assert resp["code"] == 0
    assert resp["data"]["opening"] is None


# ================================================================
# 开场题折叠 + 车型资料引导（1012 v2，本分支新增功能）
# ================================================================

def test_build_opening_collapse_arrow_data():
    # 常驻 4 + 折叠剩余的数据形态：前端「展开全部」点击后补渲染 collapsed
    from ai.api.vehicle_mode import build_opening

    cats = ["1. 界面报故障码", "2. 车辆停着不动", "3. 取货失败", "4. 卸货/放货异常",
            "5. 堆叠失败", "6. 自检失败", "7. 充电异常", "8. 车体异常", "9. 多车调度"]
    op = build_opening(cats, rng=__import__("random").Random(1))
    assert len(op["choices"]) == 4 and len(op["collapsed_choices"]) == 5
    assert set(op["choices"]) | set(op["collapsed_choices"]) == set(cats)
    assert not set(op["choices"]) & set(op["collapsed_choices"])


def test_build_vehicle_guide_scans_model_pages(tmp_path):
    from ai.api.vehicle_mode import build_vehicle_guide

    xqe = tmp_path / "company" / "XQE"
    xqe.mkdir(parents=True)
    (xqe / "XQE122.md").write_text("# x", encoding="utf-8")
    (xqe / "XQE151.md").write_text("# x", encoding="utf-8")
    (xqe / "05_车型概览.md").write_text("# x", encoding="utf-8")   # 非型号页排除
    (xqe / "90_问题清单分类.md").write_text("# x", encoding="utf-8")

    assert build_vehicle_guide("XQE", kb_root=tmp_path) == {
        "question": "想了解 XQE 车型信息？点击查看型号介绍",
        "choices": ["XQE122 车型介绍", "XQE151 车型介绍"]}
    assert build_vehicle_guide("XQE", kb_root=tmp_path / "nope") is None
