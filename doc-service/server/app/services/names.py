from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infra.models import User

DIRECTORY: dict[str, str] = {
    "wuyanqing": "吴彦清",
    "xiazelong": "夏泽龙",
    "xuhaonan": "徐浩南",
    "tianshuzheng": "田树政",
    "hujiannan": "胡健楠",
    "jiashuang": "贾爽",
    "qiziqian": "齐子谦",
    "wangzhuo": "王卓",
    "wanghaibo": "汪海波",
    "chenlianxin": "陈连鑫",
    "liuqingyuan": "刘青源",
    "zhangwenxin": "张文星",
    "wujiaxiu": "吴佳秀",
    "lihongwei": "李宏威",
    "jiangqinyang": "姜钦阳",
    "zhangjunlei": "张俊磊",
    "zhushanshan": "朱姗姗",
    "luohao": "罗昊",
    "maomengqing": "毛梦晴",
    "zhanghuili": "张会丽",
    "genghongxiu": "耿洪秀",
    "baiyongqi": "白永奇",
    "donghualai": "董华来",
}


def label(username: str | None, stored: str | None = None) -> str:
    s = (stored or "").strip()
    if s:
        return s
    u = (username or "").strip()
    return DIRECTORY.get(u, u)


def map_db(db: Session) -> dict[str, str]:
    out = dict(DIRECTORY)
    for u in db.scalars(select(User)):
        out[u.username] = label(u.username, u.display_name)
    return out
