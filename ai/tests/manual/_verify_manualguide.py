"""验证 ManualGuide：递归解析完整平台手册 + 按日志路径路由（不 build 日志索引，秒级）。"""
import sys, json
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))
from dotenv import load_dotenv
load_dotenv(_ROOT / "ai" / ".env")

from ai.agents.AiTaskPlatform.log_analyzer.triage import ManualGuide, _detect_module, _detect_category

# 不显式传 manual_dir：验证从配置 LOG_MANUAL_DIR 自动解析
MANUAL = None


def test_parse_all():
    print("== 1. 全量解析（默认路径取自配置，无日志路径回退全加载） ==")
    g = ManualGuide(MANUAL)
    print(f"  目录: {g.manual_dir}")
    print(f"  加载文件数: {len(g.loaded_files)}")
    print(f"  信号数: {len(g.signals)}")
    print(f"  场景数: {len(g.scenarios)}")
    print(f"  时间线条数: {len(g.normal_timeline)}")
    print("  前 5 信号:", [s['name'] for s in g.signals[:5]])
    print("  前 2 场景:", [s['title'] for s in g.scenarios[:2]])
    assert len(g.loaded_files) > 0, "应至少加载一份手册"
    assert len(g.signals) > 0, "应提取到信号"


def test_route_algorithm():
    print("\n== 2. 路由：算法日志 (DYNAMIC_MAP) ==")
    log = r"/usp_algorithm_logs/DYNAMIC_MAP-USPA-LOGS-/debug_logs.log.29"
    print(f"  _detect_module -> {_detect_module(log)}")
    print(f"  _detect_category -> {_detect_category(log)}")
    g = ManualGuide(MANUAL, log_path=log)
    print(f"  路由结果: {json.dumps(g.routed, ensure_ascii=False, indent=2)}")
    print(f"  实际加载 {len(g.loaded_files)} 份: {g.loaded_files}")
    # 算法日志应命中算法模块或至少相关
    assert g.routed["category"]["name"] == "algorithm", "应判定为算法模块"


def test_route_platform():
    print("\n== 3. 路由：平台日志 (Base) ==")
    log = r"D:\CodeHub\usp_source\Base\logger\USP-Base.log"
    print(f"  _detect_module -> {_detect_module(log)}")
    print(f"  _detect_category -> {_detect_category(log)}")
    g = ManualGuide(MANUAL, log_path=log)
    print(f"  路由结果: {json.dumps(g.routed, ensure_ascii=False, indent=2)}")
    print(f"  实际加载 {len(g.loaded_files)} 份: {g.loaded_files}")


def test_route_taskflow():
    print("\n== 4. 路由：taskFlowPlatform 日志 ==")
    log = r"logs/taskflow-api.log"
    print(f"  _detect_module -> {_detect_module(log)}")
    g = ManualGuide(MANUAL, log_path=log)
    print(f"  路由结果: {json.dumps(g.routed, ensure_ascii=False, indent=2)}")
    print(f"  实际加载 {len(g.loaded_files)} 份: {g.loaded_files}")


if __name__ == "__main__":
    test_parse_all()
    test_route_algorithm()
    test_route_platform()
    test_route_taskflow()
    print("\nManualGuide 验证完成 ✓")
