"""AI 数据分析平台 · 风险推算引擎（确定性规则）

风险口径改造：风险分析不再读取 risk 表（人工登记），改为按规则从
项目信息与工单数据综合推算每个项目的风险分数与等级。

信号输入（每项目，由采集层从项目信息与工单表汇总）：
- 未关闭工单数 / 近30天新增工单数（报障、Bug 类工单按 problem_factor 加权）
- AGV 数量 / AGV 种类（车型信息节点）
- 项目类型（基础信息 > 项目类型节点）
- 人工风险点（项目特性 > 风险点节点）

输出：分数（0~100）、等级（低/中/高）、风险因素清单。

权重与阈值全部来自同目录 risk_assessment.yaml（代码内置同构默认值兜底），
业务调参不碰代码；纯函数实现，可脱离数据库单测。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger("RiskAssessor")

_CONFIG_PATH = Path(__file__).resolve().parent / "risk_assessment.yaml"

# 代码内置兜底配置（与 yaml 同构）：yaml 缺失/损坏/字段缺失时按项回退，
# 保证服务在任何环境下都能给出评估结果。
_DEFAULT_CONFIG: dict = {
    "levels": {"low_max": 40, "high_min": 70},
    "ticket": {
        "open_weight": 4,
        "open_cap": 40,
        "new_30d_weight": 6,
        "new_30d_cap": 30,
        "problem_factor": 1.5,
    },
    "agv": {"scale_weight": 1, "scale_cap": 15, "multi_model_bonus": 8},
    "project_type_scores": {
        "受关注项目": 15,
        "大客户项目": 10,
        "展会/演示项目": 0,
        "展厅项目": 0,
        "PK项目": 25,
        "试点项目": 20,
        "试用项目": 10,
        "内部/测试项目": 0,
    },
    "manual_risk_scores": {
        "数据同步错误": 20,
        "公司评审不通过": 30,
        "缺前置承接": 25,
        "高风险承接": 40,
        "中风险承接": 20,
        "低风险承接": 5,
    },
}

_LEVEL_LOW = "低"
_LEVEL_MID = "中"
_LEVEL_HIGH = "高"


@dataclass
class ProjectRiskSignals:
    """单个项目的风险信号（采集层从项目信息与工单表汇总）。"""

    project_id: str
    project_name: str
    open_tickets: int = 0             # 未关闭工单数（含报障/bug）
    open_problem_tickets: int = 0     # 其中报障/bug 类数量（加权用）
    new_30d_tickets: int = 0          # 近30天新增工单数（含报障/bug）
    new_30d_problem_tickets: int = 0  # 其中报障/bug 类数量（加权用）
    agv_count: int = 0                # AGV 数量
    agv_model_count: int = 0          # AGV 种类（数量 > 0 的车型数）
    project_type: str = ""            # 项目类型节点值
    manual_risk: str = ""             # 人工风险点节点值


def _deep_merge(default: dict, override: dict) -> dict:
    """以 default 为底，override 中 dict 逐层合并、标量覆盖（不修改入参）。"""
    merged = {k: (v.copy() if isinstance(v, dict) else v) for k, v in default.items()}
    for key, val in override.items():
        if isinstance(val, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], val)
        elif val is not None:
            merged[key] = val
    return merged


def load_config() -> dict:
    """加载 yaml 配置；缺失/损坏/缺字段时逐项回退内置默认值。"""
    if _CONFIG_PATH.exists():
        try:
            raw = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
            return _deep_merge(_DEFAULT_CONFIG, raw)
        except Exception:
            logger.exception("风险评分配置加载失败，回退内置默认值")
    return _deep_merge(_DEFAULT_CONFIG, {})


def _level_of(score: int, cfg: dict) -> str:
    levels = cfg["levels"]
    if score >= levels["high_min"]:
        return _LEVEL_HIGH
    if score <= levels["low_max"]:
        return _LEVEL_LOW
    return _LEVEL_MID


def _effective_count(total: int, problem: int, factor: float) -> float:
    """报障/bug 类工单按系数加权后的等效数量。"""
    return total + max(problem, 0) * (factor - 1.0)


def assess_project(signals: ProjectRiskSignals, cfg: dict | None = None) -> dict:
    """对单个项目按规则推算风险分数、等级与风险因素。

    返回中文字段字典，直接作为「风险评估明细」条目喂 LLM 或渲染：
    {项目代码, 项目名称, 风险分数, 风险等级, 未关闭工单数, 近30天新增工单数,
     AGV数量, AGV种类, 项目类型, 人工风险点, 风险因素}
    """
    cfg = cfg or load_config()
    t, a = cfg["ticket"], cfg["agv"]

    open_eff = _effective_count(
        signals.open_tickets, signals.open_problem_tickets, t["problem_factor"]
    )
    new_eff = _effective_count(
        signals.new_30d_tickets, signals.new_30d_problem_tickets, t["problem_factor"]
    )
    open_score = min(open_eff * t["open_weight"], t["open_cap"])
    new_score = min(new_eff * t["new_30d_weight"], t["new_30d_cap"])

    agv_score = min(signals.agv_count * a["scale_weight"], a["scale_cap"])
    if signals.agv_model_count > 1:
        agv_score += a["multi_model_bonus"]

    type_score = int(cfg["project_type_scores"].get(signals.project_type, 0) or 0)
    manual_score = int(cfg["manual_risk_scores"].get(signals.manual_risk, 0) or 0)

    score = int(round(min(open_score + new_score + agv_score + type_score + manual_score, 100)))
    level = _level_of(score, cfg)

    factors: list[str] = []
    if open_eff > 0:
        factors.append(f"未关闭工单 {signals.open_tickets} 条")
    if new_eff > 0:
        factors.append(f"近30天新增工单 {signals.new_30d_tickets} 条")
    if signals.agv_count > 0:
        factors.append(f"AGV 数量 {signals.agv_count} 台")
    if signals.agv_model_count > 1:
        factors.append(f"多车型混跑（{signals.agv_model_count} 种车型）")
    if type_score > 0:
        factors.append(f"项目类型为「{signals.project_type}」")
    if signals.manual_risk:
        factors.append(f"人工标记风险点「{signals.manual_risk}」")
    if not factors:
        factors.append("暂无显著风险信号")

    return {
        "项目代码": signals.project_id,
        "项目名称": signals.project_name,
        "风险分数": score,
        "风险等级": level,
        "未关闭工单数": signals.open_tickets,
        "近30天新增工单数": signals.new_30d_tickets,
        "AGV数量": signals.agv_count,
        "AGV种类": signals.agv_model_count,
        "项目类型": signals.project_type or "未填写",
        "人工风险点": signals.manual_risk or "未标记",
        "风险因素": factors,
    }


def assess_projects(
    signals: list[ProjectRiskSignals], cfg: dict | None = None
) -> list[dict]:
    """批量推算（同一份配置），返回与输入同序的评估明细列表。"""
    cfg = cfg or load_config()
    return [assess_project(s, cfg) for s in signals]
