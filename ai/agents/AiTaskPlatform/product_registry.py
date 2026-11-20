"""多产品日志手册注册表 — 服务器优先、本地兜底。

产品手册目录按产品登记「服务器地址 + 本地地址」，使用原则：
  1. 服务器地址优先（权威来源，服务器 Linux 路径可直接访问）
  2. 服务器不可访问（不存在/不可达）时，回退到本地路径
  3. 按日志路径的特征（match 正则）自动命中对应产品手册

配置来源:
  - env LOG_MANUALS（JSON，见 ai.config._parse_log_manuals）
  - 取用顺序: 日志路径 → 命中产品 → server(优先) → local(兜底)
  - 服务器手册目录为 `help_manuals`（/data/apps/OpenRobotService_Data/help_manuals/{产品}/）；
    注意它不同于代码包内旧的本地兜底目录 `log_manual`（log_analyzer/log_manual/）。
  - 若 LOG_MANUALS 未配置，返回 None，由调用方（ManualGuide._resolve_dir）走其旧的
    单目录候选链（代码包内 log_manual、旧默认 D:\\CodeHub\\Algorithm\\日志分析指南 等）。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional, List, Dict

from ai.core.logging import get_logger

logger = get_logger("TASK_AGENT")


def _match_product(products: Dict[str, dict], log_path: str) -> Optional[str]:
    """按日志路径命中产品。match 为正则串，命中任一即选中。"""
    if not log_path or not products:
        return None
    up = Path(log_path).as_posix().upper()
    scores = []
    for prod, info in products.items():
        mstr = (info.get("match") or "").strip()
        if not mstr:
            continue
        try:
            if re.search(mstr, up, re.IGNORECASE):
                scores.append((prod, len(mstr)))  # 匹配串越长越特异
        except re.error:
            continue
    if scores:
        scores.sort(key=lambda x: -x[1])
        return scores[0][0]
    return None


def _dir_accessible(p: str) -> bool:
    if not p:
        return False
    try:
        return Path(p).is_dir()
    except Exception:
        return False


def resolve_product_dir(prod: str, products: Dict[str, dict]) -> Optional[str]:
    """解析单个产品手册的实际目录：**服务器优先**、服务器不可用则本地兜底。"""
    info = products.get(prod) or {}
    # 1) 服务器优先
    server = (info.get("server") or "").strip()
    if server and _dir_accessible(server):
        return server
    # 2) 本地兜底
    local = (info.get("local") or "").strip()
    if local and _dir_accessible(local):
        return local
    if server or local:
        logger.warning(f"产品[{prod}]手册地址均不可访问: server={server!r} local={local!r}")
    return None


def pick_manual_dir(log_path: str = "") -> Optional[str]:
    """根据日志路径选产品并解析其手册目录（服务器优先/本地兜底）。

    若 log_path 能命中产品，返回该产品手册目录；否则返回 None（由调用方走旧逻辑）。
    """
    from ai.config import get_ai_config
    products = get_ai_config().log_manuals or {}
    if not products:
        return None
    if log_path:
        prod = _match_product(products, log_path)
        if prod:
            d = resolve_product_dir(prod, products)
            if d:
                logger.info(f"product_registry: 日志命中产品[{prod}] → {d} (server优先)")
                return d
    # 日志无法命中产品时：尝试任一可用产品的服务器地址（优先级：配置顺序）
    for prod, info in products.items():
        d = resolve_product_dir(prod, products)
        if d:
            return d
    return None


def list_products() -> List[str]:
    from ai.config import get_ai_config
    products = get_ai_config().log_manuals or {}
    return list(products.keys())
