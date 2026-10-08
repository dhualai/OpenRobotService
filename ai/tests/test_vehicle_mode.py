# -*- coding: utf-8 -*-
"""车型定制模式单测（扫码入口 /mode/confirm，XQE 试点）。

覆盖：
- 手册清单生成（目录扫描 / title / url 拼接 / 空目录容错）
- 模式注册主流程（档案命中 → metadata 写入 / 未建档拦截 / 幂等覆盖）
- 车辆档案查询规则（vehicle_code 精确优先；车型+项目匹配；disabled 不认）

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
    """sqlite 内存库建 vehicles 真实表，并把 database.engine 指过去。

    StaticPool + check_same_thread=False：内存库单连接复用（否则每个新连接
    都是空库）且允许跨线程（_lookup_vehicle 走 asyncio.to_thread）；
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
    db_mod.Base.metadata.create_all(engine, tables=[db_mod.Vehicle.__table__])
    monkeypatch.setattr(db_mod, "engine", engine)
    # 建表 flag 同步重置：进程内可能已被前序测试置 True，而本 fixture 换了新内存库
    from ai.api import vehicle_mode as vm_mod
    monkeypatch.setattr(vm_mod, "_ensure_table_done", False)
    yield engine
    engine.dispose()


@pytest.fixture
def kb_tmp(tmp_path):
    """临时 kb 目录：company/XQE/manual/ 两篇 + 一个非 md 文件（应被忽略）。"""
    manual = tmp_path / "company" / "XQE" / "manual"
    manual.mkdir(parents=True)
    (manual / "XQE下线调试与部署文档.md").write_text("# 手册A\n", encoding="utf-8")
    (manual / "故障分叉树.md").write_text("# 手册B\n", encoding="utf-8")
    (manual / "ignore.txt").write_text("not md", encoding="utf-8")
    return tmp_path


def _seed_vehicle(engine, code="XQE-122", model="XQE", project="试点项目",
                  customer="试点客户", status="active"):
    import sys

    from sqlalchemy import insert

    db_mod = sys.modules.get("ai.core.database")
    if db_mod is None:
        import ai.core.database as db_mod  # noqa: F811
    with engine.begin() as conn:
        conn.execute(insert(db_mod.Vehicle).values(
            vehicle_code=code, model=model, project_name=project,
            customer_name=customer, status=status,
        ))


def _make_request(session_id="sess-xqe-1", model="XQE", project_name="试点项目",
                  customer_name="试点客户", vehicle_code=""):
    from ai.api.vehicle_mode import ModeConfirmRequest
    return ModeConfirmRequest(
        session_id=session_id, model=model, project_name=project_name,
        customer_name=customer_name, vehicle_code=vehicle_code,
    )


# ================================================================
# 手册清单
# ================================================================

def test_list_manual_docs_scans_dir(kb_tmp):
    from ai.api.vehicle_mode import list_manual_docs

    docs = list_manual_docs("XQE", kb_root=kb_tmp, media_prefix="/api/ai/media")
    titles = [d["title"] for d in docs]
    assert titles == ["XQE下线调试与部署文档", "故障分叉树"]  # sorted 序
    assert docs[0]["url"] == "/api/ai/media/kb/company/XQE/manual/XQE下线调试与部署文档.md"
    assert docs[0]["path"] == "company/XQE/manual/XQE下线调试与部署文档.md"


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
# 档案查询规则
# ================================================================

async def test_lookup_by_vehicle_code_precise(vehicle_db):
    from ai.api.vehicle_mode import _lookup_vehicle

    _seed_vehicle(vehicle_db, code="XQE-122")
    _seed_vehicle(vehicle_db, code="XQE-9", project="另一个项目")

    v = await _lookup_vehicle("XQE", "试点项目", "XQE-122")
    assert v is not None and v.vehicle_code == "XQE-122"
    # 车号优先：即使项目名对不上也按 code 命中
    v2 = await _lookup_vehicle("XQE", "不存在的项目", "XQE-122")
    assert v2 is not None


async def test_lookup_by_model_and_project(vehicle_db):
    from ai.api.vehicle_mode import _lookup_vehicle

    _seed_vehicle(vehicle_db, code="XQE-122")
    v = await _lookup_vehicle("XQE", "试点项目", "")
    assert v is not None and v.project_name == "试点项目"
    # 0930 放宽：项目名仅展示不参与匹配（车型是白名单键）——项目名不一致也放行
    v2 = await _lookup_vehicle("XQE", "别的项目", "")
    assert v2 is not None and v2.vehicle_code == "XQE-122"
    # 大小写归一：小写 xqe 等价 XQE（用户实锤：录入小写被「未建档」拦）
    assert await _lookup_vehicle("xqe", "", "") is not None


async def test_lookup_case_insensitive_code(vehicle_db):
    from ai.api.vehicle_mode import _lookup_vehicle

    _seed_vehicle(vehicle_db, code="XQE-122")
    # 车号大小写归一
    v = await _lookup_vehicle("XQE", "", "xqe-122")
    assert v is not None and v.vehicle_code == "XQE-122"


async def test_lookup_ignores_disabled(vehicle_db):
    from ai.api.vehicle_mode import _lookup_vehicle

    _seed_vehicle(vehicle_db, code="XQE-122", status="disabled")
    assert await _lookup_vehicle("XQE", "试点项目", "XQE-122") is None
    assert await _lookup_vehicle("XQE", "试点项目", "") is None


# ================================================================
# 模式注册主流程
# ================================================================

async def test_register_mode_success(vehicle_db, kb_tmp, mock_memory, monkeypatch):
    from ai.api import vehicle_mode as vm

    _seed_vehicle(vehicle_db)
    monkeypatch.setattr(vm, "_default_kb_root", lambda: kb_tmp)
    mock_memory.get_memory  # conftest fixture

    resp = await vm.register_mode(_make_request(), memory_manager=mock_memory)
    assert resp["code"] == 0
    assert resp["data"]["confirmed"] is True
    assert resp["data"]["domain"] == "company"
    # SOP 外显清单排除引导设施（故障分叉树不外显）→ 只剩手册A
    assert len(resp["data"]["manual_docs"]) == 1
    assert resp["data"]["manual_docs"][0]["title"] == "XQE下线调试与部署文档"

    mem = await mock_memory.get_memory("sess-xqe-1")
    mode = mem.metadata["vehicle_mode"]
    assert mode["model"] == "XQE"
    assert mode["domain"] == "company"
    assert mode["vehicle_code"] == "XQE-122"
    assert mode["project_name"] == "试点项目"
    assert mode["customer_name"] == "试点客户"


async def test_register_mode_unregistered_blocks(vehicle_db, mock_memory):
    from ai.api import vehicle_mode as vm

    # 未建档 → code=1 报错拦住（实验阶段不降级）
    resp = await vm.register_mode(_make_request(), memory_manager=mock_memory)
    assert resp["code"] == 1
    assert "未建档" in resp["message"]
    mem = await mock_memory.get_memory("sess-xqe-1")
    assert "vehicle_mode" not in mem.metadata


async def test_register_mode_empty_model_blocked(vehicle_db, mock_memory):
    import pydantic

    from ai.api import vehicle_mode as vm

    # 请求模型层即拦截（FastAPI 层 422）
    with pytest.raises(pydantic.ValidationError):
        _make_request(model="")
    # 防御分支：绕过 pydantic 校验直接构造，编程调用方兜底
    req = vm.ModeConfirmRequest.model_construct(
        session_id="sess-xqe-1", model="", project_name="",
        customer_name="", vehicle_code="",
    )
    resp = await vm.register_mode(req, memory_manager=mock_memory)
    assert resp["code"] == 1


async def test_register_mode_idempotent_overwrite(vehicle_db, kb_tmp, mock_memory, monkeypatch):
    """刷新页面重复调用 = 覆盖重注册（同一 session 换车也以最后一次为准）。"""
    from ai.api import vehicle_mode as vm

    _seed_vehicle(vehicle_db, code="XQE-122")
    _seed_vehicle(vehicle_db, code="XQE-9", project="另一个项目")
    monkeypatch.setattr(vm, "_default_kb_root", lambda: kb_tmp)

    r1 = await vm.register_mode(_make_request(vehicle_code="XQE-122"), memory_manager=mock_memory)
    assert r1["code"] == 0
    r2 = await vm.register_mode(
        _make_request(model="XQE", project_name="另一个项目", vehicle_code="XQE-9"),
        memory_manager=mock_memory,
    )
    assert r2["code"] == 0

    mem = await mock_memory.get_memory("sess-xqe-1")
    assert mem.metadata["vehicle_mode"]["vehicle_code"] == "XQE-9"


async def test_register_mode_normalizes_model_case(vehicle_db, kb_tmp, mock_memory, monkeypatch):
    """档案表里存小写录入串 xqe → 注册后 model 归一为 XQE（大写）。

    归一不是洁癖：kb 目录名/入库 sub_domain 都是 XQE/manual，pipeline 按
    model 拼检索 filter，小写会过滤出空集（0930 实锤：company 域 0+0）。
    """
    from ai.api import vehicle_mode as vm

    _seed_vehicle(vehicle_db, model="xqe")
    monkeypatch.setattr(vm, "_default_kb_root", lambda: kb_tmp)

    resp = await vm.register_mode(_make_request(model="xqe"), memory_manager=mock_memory)
    assert resp["code"] == 0
    assert resp["data"]["model"] == "XQE"
    mem = await mock_memory.get_memory("sess-xqe-1")
    assert mem.metadata["vehicle_mode"]["model"] == "XQE"


# ================================================================
# 开场大方向引导题（0930：随机 5 + 故障码保底，服务端直出）
# ================================================================

@pytest.fixture
def kb_fork(tmp_path):
    """带 9 大类顶层节点的分叉树（### 子节点不算顶层）。"""
    manual = tmp_path / "company" / "XQE" / "manual"
    manual.mkdir(parents=True)
    (manual / "故障分叉树.md").write_text(
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
        assert op is not None and len(op["choices"]) == 5
        assert "界面报故障码" in op["choices"]
        assert op["question"] and op["hint"]
        seen.update(op["choices"])
    assert seen == set(levels)


def test_build_opening_insufficient():
    from ai.api.vehicle_mode import build_opening

    assert build_opening([]) is None
    assert build_opening(["只有一类"]) is None


async def test_register_mode_returns_opening(vehicle_db, kb_fork, mock_memory, monkeypatch):
    from ai.api import vehicle_mode as vm

    _seed_vehicle(vehicle_db)
    monkeypatch.setattr(vm, "_default_kb_root", lambda: kb_fork)
    resp = await vm.register_mode(_make_request(), memory_manager=mock_memory)
    assert resp["code"] == 0
    op = resp["data"]["opening"]
    assert op is not None
    assert len(op["choices"]) == 5
    assert "界面报故障码" in op["choices"]
    assert op["hint"]


async def test_register_mode_opening_none_without_tree(vehicle_db, kb_tmp, mock_memory, monkeypatch):
    # 无分叉树（kb_tmp 的分叉树无 ## 顶层）→ opening=None，注册照常成功
    from ai.api import vehicle_mode as vm

    _seed_vehicle(vehicle_db)
    monkeypatch.setattr(vm, "_default_kb_root", lambda: kb_tmp)
    resp = await vm.register_mode(_make_request(), memory_manager=mock_memory)
    assert resp["code"] == 0
    assert resp["data"]["opening"] is None
