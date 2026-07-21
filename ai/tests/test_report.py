"""日报/周报功能测试脚本

测试内容：
  1. 数据库连通性
  2. 各表数据采集（project / risk / ticket / task）
  3. 完整报告生成（需 LLM 服务可用）
"""
import asyncio
import json
import sys
import os
from pathlib import Path
from datetime import date, datetime

# 确保项目根目录在 sys.path 中
_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

# 加载 .env
from dotenv import load_dotenv
_env_file = _project_root / "ai" / ".env"
if _env_file.exists():
    load_dotenv(_env_file)
    print(f"[OK] 已加载 {_env_file}")


def _preload_database_module():
    """直接从文件加载 ai.core.database，绕过 ai/core/__init__.py 的 qdrant 依赖。"""
    import importlib.util
    import types

    # 1. 创建 ai / ai.core 空包，避免触发 __init__.py
    if "ai" not in sys.modules:
        ai_pkg = types.ModuleType("ai")
        ai_pkg.__path__ = [str(_project_root / "ai")]
        sys.modules["ai"] = ai_pkg
    if "ai.core" not in sys.modules:
        core_pkg = types.ModuleType("ai.core")
        core_pkg.__path__ = [str(_project_root / "ai" / "core")]
        sys.modules["ai.core"] = core_pkg

    # 2. 从文件加载 database.py
    db_file = _project_root / "ai" / "core" / "database.py"
    spec = importlib.util.spec_from_file_location("ai.core.database", db_file)
    db_mod = importlib.util.module_from_spec(spec)
    sys.modules["ai.core.database"] = db_mod
    spec.loader.exec_module(db_mod)
    return db_mod


def _load_report_modules():
    """直接从文件加载报告模块，绕过 agents/__init__.py 的完整导入链。"""
    import importlib.util
    import types

    # 确保 ai.agents 和 ai.agents.AiDataAnalysisPlatform 作为空包存在
    for mod_name, sub_dir in [
        ("ai.agents", "agents"),
        ("ai.agents.AiDataAnalysisPlatform", "agents/AiDataAnalysisPlatform"),
    ]:
        if mod_name not in sys.modules:
            pkg = types.ModuleType(mod_name)
            pkg.__path__ = [str(_project_root / "ai" / sub_dir)]
            sys.modules[mod_name] = pkg

    # 按依赖顺序加载模块
    module_files = [
        ("ai.agents.AiDataAnalysisPlatform.report_schemas", "agents/AiDataAnalysisPlatform/report_schemas.py"),
        ("ai.agents.AiDataAnalysisPlatform.config", "agents/AiDataAnalysisPlatform/config.py"),
        ("ai.agents.AiDataAnalysisPlatform.llm_client", "agents/AiDataAnalysisPlatform/llm_client.py"),
        ("ai.agents.AiDataAnalysisPlatform.report_prompts", "agents/AiDataAnalysisPlatform/report_prompts.py"),
        ("ai.agents.AiDataAnalysisPlatform.report_generator", "agents/AiDataAnalysisPlatform/report_generator.py"),
    ]
    loaded = {}
    for mod_name, rel_path in module_files:
        if mod_name in sys.modules:
            loaded[mod_name.split(".")[-1]] = sys.modules[mod_name]
            continue
        file_path = _project_root / "ai" / rel_path
        spec = importlib.util.spec_from_file_location(mod_name, file_path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)
        loaded[mod_name.split(".")[-1]] = mod
    return loaded


async def test_data_collection():
    """测试数据采集（不需要 LLM）。"""
    print("\n" + "=" * 60)
    print("  测试 1：数据库连通性 & 数据采集")
    print("=" * 60)

    # 使用预加载的数据库模块
    db_mod = _preload_database_module()
    SessionLocal = db_mod.SessionLocal
    Ticket = db_mod.Ticket
    Task = db_mod.Task
    ProjectDelivery = db_mod.ProjectDelivery
    Risk = db_mod.Risk

    # 1. 连通性
    db = SessionLocal()
    try:
        from sqlalchemy import text
        db.execute(text("SELECT 1"))
        print("[OK] 数据库连接成功")
    except Exception as e:
        print(f"[FAIL] 数据库连接失败: {e}")
        db.close()
        return False

    # 2. 各表行数
    tables = {
        "project (ProjectDelivery)": ProjectDelivery,
        "risk (Risk)": Risk,
        "tickets (Ticket)": Ticket,
        "tasks (Task)": Task,
    }
    for label, model in tables.items():
        try:
            count = db.query(model).count()
            print(f"  [OK] {label}: {count} 行")
        except Exception as e:
            print(f"  [WARN] {label} 查询失败: {e}")
    db.close()

    # 3. 用 ReportDataCollector 采集
    print("\n--- ReportDataCollector 采集测试 ---")
    mods = _load_report_modules()
    ReportDataCollector = mods["report_generator"].ReportDataCollector

    today = date.today()
    start = datetime.combine(today, datetime.min.time())
    end = datetime.combine(today, datetime.max.time())
    date_range_str = today.strftime("%Y-%m-%d")

    collector = ReportDataCollector(project_code=None)
    try:
        data = collector.collect_all(start, end, date_range_str)
        print(f"[OK] 采集完成: {data.date_range}")
        print(f"  项目: total={data.project.total}, active={data.project.active}")
        print(f"  风险: total={data.risk.total}, new={data.risk.new_risks}, closed={data.risk.closed_risks}")
        print(f"  工单: total={data.ticket.total}, new={data.ticket.new_tickets}, resolved={data.ticket.resolved}")
        print(f"  任务: total={data.task.total}, new={data.task.new_tasks}, resolved={data.task.resolved}, overdue={data.task.overdue}")

        # 输出 JSON 预览（截断）
        data_json = json.dumps(data.model_dump(), ensure_ascii=False, indent=2, default=str)
        preview = data_json[:1500]
        if len(data_json) > 1500:
            preview += "\n  ... (已截断)"
        print(f"\n--- 采集数据 JSON 预览 ---\n{preview}")
        return True
    except Exception as e:
        print(f"[FAIL] 数据采集失败: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_report_generation():
    """测试完整报告生成（需要 LLM 服务）。"""
    print("\n" + "=" * 60)
    print("  测试 2：完整日报生成（调用 LLM）")
    print("=" * 60)

    # 检查 LLM 配置
    provider = os.getenv("LLM_PROVIDER", "")
    api_base = os.getenv("AI_API_BASE_URL", "")
    if not provider:
        print("[SKIP] 未配置 LLM_PROVIDER，跳过 LLM 报告生成测试")
        print("  如需测试完整报告，请在 ai/.env 中添加：")
        print("    LLM_PROVIDER=deepseek")
        print("    AI_API_BASE_URL=http://localhost:8401")
        return

    try:
        mods = _load_report_modules()
        generate_report = mods["report_generator"].generate_report
        result = await generate_report(period="daily", date=date.today().strftime("%Y-%m-%d"))
        print(f"[OK] 日报生成成功")
        print(f"  周期: {result.period}")
        print(f"  日期范围: {result.date_range}")
        print(f"  章节数: {len(result.sections)}")
        for sec in result.sections:
            print(f"    - {sec.title} (指标: {list(sec.metrics.keys())})")
        print(f"  摘要: {result.summary[:200]}...")
        print(f"  生成时间: {result.generated_at}")
    except Exception as e:
        print(f"[FAIL] 报告生成失败: {e}")
        import traceback
        traceback.print_exc()


async def main():
    print("=" * 60)
    print("  日报/周报功能测试")
    print(f"  日期: {date.today().isoformat()}")
    print(f"  DATABASE_URL: {os.getenv('DATABASE_URL', '(默认)')[:60]}...")
    print("=" * 60)

    ok = await test_data_collection()
    if ok:
        await test_report_generation()

    print("\n" + "=" * 60)
    print("  测试完成")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
