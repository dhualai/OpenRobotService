"""用户画像解析 —— 供所有 Agent 共用。

从 users 表 + departments 表 + user_project_roles/roles 组装用户画像，
注入 prompt 让 LLM 按用户岗位/职责调整回答深浅。
"""

import asyncio
import json
import time
from typing import Dict

from ai.core.logging import get_logger

logger = get_logger("AI")

# 用户名下项目列表缓存：{username: (拉取时间戳, [{"name","code"}, ...])}
# 仅服务提单工具循环的项目预填，TTL 5 分钟，见 AiDiagnosisPlatform._get_user_projects
_USER_PROFILE_CACHE: Dict[str, tuple] = {}

_JOB_LEVEL_CN = {1: "一线工程师", 2: "管理/审核", 3: "最高负责人"}


def _flatten_resp_modules(modules) -> str:
    """responsibility_modules 三层/两层/扁平 JSON → 「产品：界面(功能)」串。

    三层 {产品:{界面:[功能]}} → 产品：界面(功能1、功能2)；两层 {产品:[模块]}
    → 产品：模块1、模块2；旧扁平 list → 「其他」归一；垃圾输入返回空串。
    """
    if isinstance(modules, str):
        try:
            modules = json.loads(modules)
        except Exception:
            return ""
    if isinstance(modules, list):
        modules = {"其他": modules}
    if not isinstance(modules, dict):
        return ""
    parts = []
    for prod, sub in modules.items():
        if isinstance(sub, dict):
            subs = []
            for iface, funcs in sub.items():
                if isinstance(funcs, list) and funcs:
                    subs.append(f"{iface}({'、'.join(str(x) for x in funcs)})")
                else:
                    subs.append(str(iface))
            parts.append(f"{prod}：{'/'.join(subs)}" if subs else str(prod))
        elif isinstance(sub, list) and sub:
            parts.append(f"{prod}：{'、'.join(str(x) for x in sub)}")
    return "；".join(parts)


async def resolve_user_profile(username: str) -> dict:
    """username → users 表画像 {name, department, job_level_cn, modules_text, duty, project_roles}。

    department_id 关联 departments 表取名，ID 空回退旧字符串列（与
    engineers_sync 同一兼容逻辑）。查不到人/任何异常返回 {}——对话照常，
    只是 AI 不知道用户身份。

    正常结果缓存 5 分钟、失败/查无负缓存 60 秒——与项目候选同一套纪律，
    任何降级不阻断对话。
    """
    key = (username or "").strip()
    if not key:
        return {}
    now = time.time()
    cached = _USER_PROFILE_CACHE.get(key)
    if cached and now < cached[0]:
        return cached[1]
    from ai.core.database import SessionLocal
    from sqlalchemy import text
    loop = asyncio.get_running_loop()

    def _query():
        session = SessionLocal()
        try:
            row = session.execute(text(
                "SELECT u.name, d.name, u.department, u.job_level, "
                "       u.responsibility_modules, u.duty_text "
                "FROM users u LEFT JOIN departments d ON u.department_id = d.id "
                "WHERE u.username = :u LIMIT 1"
            ), {"u": key}).fetchone()
            # 项目内角色（0916）：user_project_roles.role_id → roles.name
            # （调度研发/实施/项目经理…）——用户画像缺的受众信号：研发可深入
            # 原理、实施要现场动作、管理要结论优先。跨项目去重聚合。
            roles = session.execute(text(
                "SELECT DISTINCT r.name FROM user_project_roles upr "
                "JOIN roles r ON r.id = upr.role_id "
                "JOIN users u ON u.id = upr.user_id "
                "WHERE u.username = :u AND r.name IS NOT NULL AND r.name <> ''"
            ), {"u": key}).fetchall()
            return row, [r[0] for r in roles if r[0]]
        finally:
            session.close()

    try:
        row, project_roles = await asyncio.wait_for(
            loop.run_in_executor(None, _query), timeout=1.5)
    except Exception as e:
        logger.warning(f"[user_profile] 查询失败(降级无画像): username={key}, err={e}")
        _USER_PROFILE_CACHE[key] = (now + 60, {})
        return {}
    if not row:
        _USER_PROFILE_CACHE[key] = (now + 60, {})
        return {}
    profile = {
        "name": (row[0] or key).strip(),
        "department": ((row[1] or row[2]) or "").strip(),
        "job_level_cn": _JOB_LEVEL_CN.get(row[3], ""),
        "modules_text": _flatten_resp_modules(row[4])[:120],
        "duty": ((row[5] or "").strip())[:80],
        "project_roles": ("、".join(project_roles))[:60],
    }
    _USER_PROFILE_CACHE[key] = (now + 300, profile)
    # 打全五项：低频事件（每用户 5 分钟一次），排障时一眼看出哪些字段空
    logger.info(f"[user_profile] 解析成功: {key} → {profile}")
    return profile


def format_user_profile_block(profile: dict) -> str:
    """【用户】身份块——供各 Agent prompt 注入。无画像返回空串不注入。

    使用规则抽象表述（flash 会把具体示例逐字抄进输出的老毛病），
    且明确「无需每句称呼用户名」防谄媚式回复。
    """
    p = profile or {}
    if not p.get("name"):
        return ""
    seg = [p["name"]]
    if p.get("department"):
        seg.append(p["department"])
    if p.get("job_level_cn"):
        seg.append(p["job_level_cn"])
    if p.get("project_roles"):
        seg.append(p["project_roles"])
    lines = [f"【用户】{'｜'.join(seg)}"]
    _duty_parts = []
    if p.get("modules_text"):
        _duty_parts.append(f"负责：{p['modules_text']}")
    if p.get("duty"):
        _duty_parts.append(p["duty"])
    if _duty_parts:
        lines.append(f"【用户职责】{'｜'.join(_duty_parts)}")
    lines.append(
        "（回答时可结合用户岗位、职责与项目内角色调整针对性与深浅——"
        "偏研发/算法背景的可深入技术细节与原理推导；偏实施/现场的给可执行的"
        "操作步骤与检查动作；偏管理的先给结论与影响面再展开。"
        "按职级适当调整表达的正式程度与内容详略，但保持一致的专业工程师态度，"
        "不因职级谄媚或怠慢；无需每句称呼用户名。"
        "用户问「你认识我吗 / 我是谁 / 你知道我是谁」时，用上面的【用户】信息直接回答，"
        "并说明你能从当前登录认出对方；不要改口自称别的助手，也不要把用户推给「U老师那边」。"
        "用户在问自己是谁，不是在问你（助手）的身份。）"
    )
    return "\n".join(lines) + "\n"
