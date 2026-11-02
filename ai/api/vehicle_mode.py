# -*- coding: utf-8 -*-
"""车型定制模式：扫码入口的模式确认接口（XQE 试点）。

链路：车体二维码（跳转 URL 带 scene = str(qrcodes.id)）→ 前端「我要摇人」界面
弹信息确认弹窗（项目名/客户名/车型取扫码查到的录入信息行）→ 用户确认后前端调
POST /mode/confirm → 此处按 scene 精确取 wechat_qrcodes 里的那一行：
  - 该行存在且 status=published → 该 session 注册为车型定制模式
    （memory.metadata["vehicle_mode"]），返回该车型 SOP 手册文档清单
    （md 链接，前端拉正文渲染）
  - scene 非法 / 查无此码 / 非 published → code=1 报错拦住
    （实验阶段不降级常规模式）

车型/项目名/客户名一律以库内那行为准（请求里的同名字段只作兜底）；同车型多台车
的 scene（行 id）各不相同，按主键精确取行才不串车。

铁律：定制模式是「加法」——常规链路（不调本接口的会话）一个字节不变；
后续 pipeline 的检索域限定 / prompt 注入 / 错误码直查全部以
metadata["vehicle_mode"] 存在为唯一开关。

幂等：同一 session 重复调用 = 覆盖重注册（前端刷新即重调，天然安全）。
"""
import time
import asyncio
import random
import re
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from ai.core.logging import get_logger
from ai.core.memory import MemoryManager

logger = get_logger(__name__)

# ---- 开场大方向引导题（0930 定稿：服务端直出，不经 LLM）----
OPENING_QUESTION = "您遇到了什么问题？"
OPENING_HINT = "若是其他情况，请在下方输入框描述"
_OPENING_KEEP_SUBSTR = "故障码"   # 保底类目（重要大类，随机时必选）
_OPENING_LIMIT = 5


def parse_fork_tree_choices(model: str, kb_root: Optional[Path] = None,
                             subpath: str = "") -> List[str]:
    """读 kb/company/{车型}/ 下文件名含「分叉树」的 md，解析顶层 ## 标题。

    只收**带数字序号前缀**的顶层节点（`## 1. xxx`）——分叉树大方向类目天然
    编号；「附：…」「待用户确认」等文档管理章节无序号，天然排除（v0.1 实测
    混进过「待用户确认」）。剥序号后返回类目列表；文件不存在/无标题返回 []
    （开场题是可选能力，空库自动退化为纯输入框，不阻塞模式注册）。
    kb_root 参数供测试注入临时目录。
    subpath：已解析的知识目录名（磁盘原名，ORS-1082）。
    """
    sub = (subpath or "").strip() or model_to_subpath(model)
    if not sub:
        return []
    root = (kb_root or _default_kb_root()) / "company" / sub
    tree = None
    if root.is_dir():
        for f in sorted(root.glob("*.md")):
            if "分叉树" in f.stem:
                tree = f
                break
    if tree is None:
        return []
    try:
        lines = tree.read_text(encoding="utf-8").splitlines()
    except Exception as e:
        logger.warning(f"[vehicle_mode] 分叉树读取失败: {tree.name}: {e}")
        return []
    out: List[str] = []
    for ln in lines:
        m = re.match(r"^##\s+(\d+)\s*[.、．]\s*(.+?)\s*$", ln)
        if not m:
            continue
        t = m.group(2).strip()
        if t and t not in out:
            out.append(t)
    return out


def build_opening(top_levels: List[str], rng: Optional[random.Random] = None) -> Optional[Dict]:
    """开场题数据：故障码类保底 + 其余随机抽满 5（0930 定稿）。

    随机是用户拍板（固定前 5 会让其余类永无曝光）；故障码是重要大类保底
    不随机。不足 2 类不出题（None）。
    """
    if not top_levels:
        return None
    rng = rng or random
    keep = [c for c in top_levels if _OPENING_KEEP_SUBSTR in c]
    rest = [c for c in top_levels if _OPENING_KEEP_SUBSTR not in c]
    rng.shuffle(rest)
    # 1012 v2：常驻 4（故障码保底优先）+ 折叠剩余——「展开全部」后前端补渲染，
    # 九大类全部可达（产品经理要全列）而首屏不爆。类目不足 4 时全进常驻。
    all_cats = keep + rest
    choices = all_cats[:4]
    collapsed = all_cats[4:]
    if len(choices) < 2:
        return None
    return {
        "question": OPENING_QUESTION,
        "choices": choices,
        "collapsed_choices": collapsed,
        "hint": OPENING_HINT,
    }


class ModeConfirmRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=128, description="会话 ID（前端先建会话再调本接口绑定模式）")
    scene: str = Field(..., min_length=1, max_length=32, description="扫码场景值（= str(wechat_qrcodes.id)，纯数字），据此精确取扫的那一行")
    model: str = Field(..., min_length=1, max_length=64, description="车型（用户已在弹窗确认的展示值；实际以库内行为准）")
    project_name: str = Field(default="", max_length=128, description="项目名（弹窗展示值；库内非空时以库内为准）")
    customer_name: str = Field(default="", max_length=128, description="客户名（弹窗展示值；库内非空时以库内为准）")
    vehicle_code: str = Field(default="", max_length=64, description="唯一车号（扫码链路不使用，保留字段兼容其他调用方）")


# ============================================================
# 扫码行查询（数据源：wechat_qrcodes 录入信息行）
# ============================================================
# scene 严格为纯数字串（= str(qrcodes.id)，0930 起的微信号场景值口径）：非数字
# 直接拒绝，不做归一也不做模糊匹配——只按主键命中「扫的那一行」。同车型多台车各
# 自 id 独立，按车型/项目名兜底匹配会让不同车串味，一律不做。
_SCENE_RE = re.compile(r"^\d+$")

# 只有已发布（= 车辆已出厂）的码才允许进入定制模式
_STATUS_PUBLISHED = "published"


async def _lookup_qrcode(scene: str):
    """按主键精确取 scene 对应的二维码行；查无此码返回 None。

    只按 id 匹配，车型/项目/客户仅用于展示、不参与匹配。status 校验留给调用方，
    便于把「查无此码」与「码不可用」报成不同文案（两者都不降级）。
    """
    from ai.core.database import WechatQrcode, engine
    from sqlalchemy.orm import Session as DBSession

    qr_id = int(scene)

    def _query():
        with DBSession(engine) as s:
            return s.query(WechatQrcode).filter(WechatQrcode.id == qr_id).first()

    return await asyncio.to_thread(_query)


# ============================================================
# 车型 → 知识库目录映射 + 手册清单
# ============================================================
def model_to_subpath(model: str) -> str:
    """车型 → company 域下的知识库子目录名。XQE → XQE；未来新车型零改动。

    车型知识不建独立顶层域（0930 用户定稿）：kb/company/{车型}/ 入库归
    company 域，payload sub_domain="{车型}"，检索按此过滤（车型目录平铺，无子层级）。

    ⚠️ 这是**精确**目录口径（只去空格+大写）：录入串与目录名不逐字符相等就落空
    （生产实证：XQE122 / xqe122-2 / XQC 全部命中不到，详见 resolve_model_dirs）。
    模糊匹配走 resolve_model_dirs（ORS-1082），本函数保留为兜底。"""
    return (model or "").strip().upper()


def model_to_domain(model: str) -> str:
    """车型 → 所属 kb 域名。车型知识统一挂 company 域。"""
    return "company"


# ============================================================
# 车型 → 知识目录解析（ORS-1082：按车型的模糊匹配）
# ============================================================
# 生产实况（2026-11-02 实查 helpdesk_724 + 生产 KB）：
#   kb/company/ 下只有 XQE/ 有车系级 SOP 资产（33 篇：分叉树 + 故障码表 +
#   排查手册 + 2 型号页）；XQC/XFL/XCD… 的资料在 product_catalog/ 下**平铺**的
#   66 篇车型资料页里（XQC161/XQC163/XQC201）。扫码录入值形态混乱：
#   XQE122(16) / xqe122-2..4(3) / XQE151(2) / XQE(2) / XQC(2) / Xcd0051-01…
#   旧口径（仅 upper）让除「恰好等于 XQE」之外的全部落空 → 手册清单空 +
#   分叉树空 + 开场题空 + 检索 filter 空集 = 整条扫码 SOP 失效。
#
# 解析口径改为「字母族键」：型号串开头的连续字母段即车系键
#   （XQE122 / xqe122-2 → XQE，XQC163 → XQC，Xcd0051-05 → XCD）
# 分级：
#   L1/L2 目录名精确 / 归一化相等（XQE-151 → XQE151 目录）
#   L3    族键同名目录 → 车系手册全集（XQE）
#   L4    族键无同名目录 → 找「{族}{数字}」形态文件所在目录（XQC →
#        product_catalog），并带文件名白名单（平铺目录 66 款车型互相串味）
#   L5    兜底：原值当目录名（KB 没建该车系时行为与旧版一致）

# 归一化：去分隔符（空格/-/_）+ 大写。XQE-151 → XQE151；xqe122-2 → XQE1222
_NORM_STRIP_RE = re.compile(r"[\s\-_]+")
# 车型族键 = 型号串开头的连续字母段（XQE122→XQE，XQC163→XQC）
_FAMILY_RE = re.compile(r"^([A-Za-z]+)")


def _norm_model(model: str) -> str:
    """车型串归一化（仅用于比较）：去分隔符 + 大写。"""
    return _NORM_STRIP_RE.sub("", (model or "")).upper()


def model_family(model: str) -> str:
    """车型族键：型号串开头的连续字母段（大写）。XQE122→XQE；无字母前缀→空串。"""
    m = _FAMILY_RE.match((model or "").strip())
    return m.group(1).upper() if m else ""


# company 域一级目录名缓存：root → (mtime, [目录名...])
_MODEL_DIRS_CACHE: Dict[str, tuple] = {}


def _company_model_dirs(kb_root: Optional[Path] = None) -> List[str]:
    """列 kb/company/ 下的一级目录名（车系目录候选），带 mtime 缓存。

    目录不存在 → 空列表（KB 未初始化时手册是可选能力，不阻塞模式注册）。"""
    root = (kb_root or _default_kb_root()) / "company"
    try:
        mtime = root.stat().st_mtime
    except OSError:
        return []
    key = str(root)
    cached = _MODEL_DIRS_CACHE.get(key)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        dirs = sorted(p.name for p in root.iterdir() if p.is_dir())
    except OSError:
        dirs = []
    _MODEL_DIRS_CACHE[key] = (mtime, dirs)
    return dirs


def resolve_model_dirs(model: str, kb_root: Optional[Path] = None) -> Dict[str, object]:
    """车型 → 知识目录解析（ORS-1082 模糊匹配）。

    返回 {"family", "dirs", "files", "primary"}：
    - family：字母族键（XQE / XQC / …，无字母前缀时为空串）
    - dirs：命中的知识目录名（相对 company 域，**磁盘原名**），车系目录优先
    - files：{目录名: [文件名白名单]}——只约束平铺目录（如 product_catalog 下
      的 XQC161/XQC163/XQC201），车系目录不在表里（不收窄）
    - primary：主目录（分叉树/开场题/车型介绍页读它），无命中时为原值大写
    """
    raw = (model or "").strip()
    if not raw:
        return {"family": "", "dirs": [], "files": {}, "primary": ""}
    norm = _norm_model(raw)
    family = model_family(raw)
    dirs = _company_model_dirs(kb_root)
    root = (kb_root or _default_kb_root()) / "company"

    # L1/L2：目录名精确 / 归一化相等
    exact = [d for d in dirs if d.upper() == raw.upper()]
    if not exact:
        exact = [d for d in dirs if _norm_model(d) == norm]

    if family:
        # L3：族键同名目录 = 车系手册全集（XQE）
        fam_dirs = [d for d in dirs if d.upper() == family]
        if fam_dirs:
            hit = list(dict.fromkeys(fam_dirs + exact))
            return {"family": family, "dirs": hit, "files": {}, "primary": hit[0]}

        # L4：族键无同名目录 → 找「{族}{数字}」形态型号文件所在目录
        # （XQC → product_catalog）。文件名白名单随行返回，防 66 款车型串味。
        pat = re.compile(rf"^{re.escape(family)}\d+", re.IGNORECASE)
        flat_dirs: List[str] = []
        flat_files: Dict[str, List[str]] = {}
        for d in dirs:
            sub = root / d
            if not sub.is_dir():
                continue
            hits = [f.name for f in sorted(sub.glob("*.md")) if pat.match(f.stem)]
            if not hits:
                continue
            flat_dirs.append(d)
            flat_files[d] = hits
        if flat_dirs:
            hit = list(dict.fromkeys(flat_dirs + exact))
            files = {d: flat_files[d] for d in flat_dirs if d in flat_files}
            return {"family": family, "dirs": hit, "files": files, "primary": hit[0]}

    # L5：只精确/归一化命中（族键无对应目录）
    if exact:
        return {"family": family, "dirs": exact, "files": {}, "primary": exact[0]}
    # 兜底：原值当目录名——KB 未建该车系时行为与旧版一致（不阻塞注册，只是
    # 手册空 + 检索 0 召回，AI 如实说未收录）
    up = raw.upper()
    return {"family": family, "dirs": [up] if up else [], "files": {}, "primary": up}


# 引导设施文档（不走 SOP 外显按钮）：分叉树=对话引导题的内容源、故障码表=
# 输码直查的内容源——它们的入口在对话里，不作为文档气泡重复露出（0930 定稿：
# 除引导设施外，一个文件一个 SOP 气泡按钮）
_GUIDE_DOC_KEYWORDS = ("分叉树", "故障码表")


def is_guide_doc(name: str) -> bool:
    """是否引导设施文档（分叉树/故障码表）——入口在对话里，不作为手册外显。"""
    return any(k in (name or "") for k in _GUIDE_DOC_KEYWORDS)


def list_manual_docs_multi(dirs: List[str], files: Optional[Dict[str, List[str]]] = None,
                            kb_root: Optional[Path] = None,
                            media_prefix: str = "/api/ai/media",
                            exclude_guide_docs: bool = False) -> List[Dict[str, str]]:
    """按目录（+ 文件白名单）生成手册文档清单（ORS-1082）。

    dirs：company 域下的目录名列表（磁盘原名）；files：{目录名: [文件名白名单]}，
    白名单目录只出白名单文件——平铺目录（product_catalog 下 66 款车型平铺）
    靠它防车型互相串味。
    kb 目录/车型目录不存在 → 返回空清单（手册是可选能力，不阻塞模式注册）。
    URL 走 run.py 已挂的静态路由 {media_prefix}/kb/**（前端 fetch 后自行渲染）。
    kb_root 参数供测试注入临时目录。
    """
    root = (kb_root or _default_kb_root()) / "company"
    docs: List[Dict[str, str]] = []
    seen = set()
    for d in dirs or []:
        sub = root / d
        if not sub.is_dir():
            continue
        allow = set((files or {}).get(d) or [])
        for f in sorted(sub.glob("*.md")):
            if allow and f.name not in allow:
                continue
            if exclude_guide_docs and is_guide_doc(f.stem):
                continue
            path = f"company/{d}/{f.name}"
            if path in seen:
                continue
            seen.add(path)
            docs.append({
                "title": f.stem,
                "path": path,
                "url": f"{media_prefix}/kb/company/{d}/{f.name}",
            })
    return docs


def list_manual_docs(model: str, kb_root: Optional[Path] = None,
                     media_prefix: str = "/api/ai/media",
                     exclude_guide_docs: bool = False) -> List[Dict[str, str]]:
    """扫 kb/company/{车型}/*.md 生成手册文档清单（title + 正文 URL）。

    单目录精确口径（兼容入口）；多目录 + 文件白名单走 list_manual_docs_multi。
    exclude_guide_docs=True 时排除引导设施文档（分叉树/故障码表）——confirm
    响应的 manual_docs 用于 SOP 外显气泡，只出可在线查看的操作文档。
    """
    return list_manual_docs_multi([model_to_subpath(model)], None, kb_root=kb_root,
                                  media_prefix=media_prefix,
                                  exclude_guide_docs=exclude_guide_docs)


_VEHICLE_GUIDE_QUESTION = "想了解 {model} 车型信息？点击查看型号介绍"


def build_vehicle_guide(model: str, kb_root: Optional[Path] = None,
                        files: Optional[List[str]] = None,
                        subpath: str = "") -> Optional[Dict]:
    """车型基础信息引导（独立于开场故障题，1012）：扫车型目录下的型号详情页
    （XQE122.md / XQE151.md 形态）生成按钮组——新增型号详情页即自动出现。

    点击按钮 = 用户发出「{型号} 车型介绍」消息 → 走检索命中概览/详情页，
    带整车图与参数回答。无型号详情页 → None（不渲染引导条）。

    subpath：已解析的知识目录名（磁盘原名，ORS-1082）——平铺目录名是小写
    （product_catalog），不能再走 model_to_subpath 的大写口径。
    files：文件名白名单（ORS-1082）——平铺目录 66 款车型页面平铺，不收窄
    会把全系列塞进按钮组（XQC 会话只该出 XQC 三款）。
    """
    sub = (subpath or "").strip() or model_to_subpath(model)
    if not sub:
        return None
    root = (kb_root or _default_kb_root()) / "company" / sub
    if not root.is_dir():
        return None
    allow = set(files or [])
    models = []
    for f in sorted(root.glob("*.md")):
        if allow and f.name not in allow:
            continue
        m = re.fullmatch(r"([A-Z]+\d+)", f.stem)
        if m:
            models.append(m.group(1))
    if not models:
        return None
    return {
        "question": _VEHICLE_GUIDE_QUESTION.format(model=(model or "").strip() or sub),
        "choices": [f"{m} 车型介绍" for m in models],
    }


def _default_kb_root() -> Path:
    from ai.config import _KB_DIR
    return _KB_DIR


# ============================================================
# 分叉树全树直出（1014 ORS-980）：选项点击走树不走 LLM
# ============================================================
# 用户点引导按钮（类目/场景/现象选项）= 树节点文案的确定性回放——机械匹配
# 后直接返回树里的下一层选项或该行解决方案，零检索零 LLM 秒回（点选项本来
# 就不产生新信息，让 LLM 转几秒只是把人工写的表重新排版）。自由输入不命中
# 树路径，自然落到正常链路上 AI。
#
# 树结构（kb/company/{车型}/NN_故障分叉树.md）：
#   ## N. 类目            ← 开场题按钮（build_opening 的数据源）
#   **引导问题：…**        ← 挂最近节点
#   ### N.M 场景           ← 部分类目有（如「车辆停着不动」按位置分）
#   | 选项 | 现象 | 判断 | 解决方案 | 处置 |   ← 选项表（第2列=按钮文案与匹配键）
#   **X 深化分叉——引导问题：…** ← 场景内的二级选项组（如 1.1-F 的 F1/F2/F3）

_TREE_CACHE: Dict[str, tuple] = {}


def parse_fork_tree(model: str, kb_root: Optional[Path] = None,
                    subpath: str = "") -> List[Dict]:
    """解析分叉树全树：[{name, question, scenes:[{name,question,rows,deep...}], rows, deep_*}]。

    只收带数字序号的 ## 类目（与 parse_fork_tree_choices 同规则，管理章节天然
    排除）。文件不存在/解析异常返回 []（直出是增强能力，缺树时按钮消息落到
    正常 AI 链路）。带 mtime 缓存（每轮 ask 都要解析，树文件 16KB 级别）。
    subpath：已解析的知识目录名（磁盘原名，ORS-1082）。
    """
    sub = (subpath or "").strip() or model_to_subpath(model)
    if not sub:
        return []
    root = (kb_root or _default_kb_root()) / "company" / sub
    tree_file = None
    if root.is_dir():
        for f in sorted(root.glob("*.md")):
            if "分叉树" in f.stem:
                tree_file = f
                break
    if tree_file is None:
        return []
    try:
        mtime = tree_file.stat().st_mtime
        cache_key = (sub, str(root))
        cached = _TREE_CACHE.get(cache_key)
        if cached and cached[0] == mtime:
            return cached[1]
        cats: List[Dict] = []
        cur_cat: Optional[Dict] = None
        cur_scene: Optional[Dict] = None
        # 深化分叉组挂在最近的场景/类目上：deep_q=引导问题，deep_rows=F1/F2/F3 行
        in_deep = False

        def _container() -> Optional[Dict]:
            return cur_scene if cur_scene is not None else cur_cat

        for ln in tree_file.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^##\s+(\d+)\s*[.、．]\s*(.+?)\s*$", ln)
            if m:
                cur_cat = {"name": m.group(2).strip(), "question": "",
                           "scenes": [], "rows": [], "deep_q": "", "deep_rows": []}
                cats.append(cur_cat)
                cur_scene, in_deep = None, False
                continue
            if ln.startswith("## "):
                # 无数字序号的管理章节（「附：…」「待用户确认」）：停止收集，
                # 其内容不漏进上一个类目
                cur_cat, cur_scene, in_deep = None, None, False
                continue
            if cur_cat is None:
                continue
            m = re.match(r"^###\s+(.+?)\s*$", ln)
            if m:
                cur_scene = {"name": m.group(1).strip(), "question": "",
                             "rows": [], "deep_q": "", "deep_rows": []}
                cur_cat["scenes"].append(cur_scene)
                in_deep = False
                continue
            m = re.match(r"^\*\*(.+?)深化分叉——(.+?)\*\*", ln)
            if m:
                c = _container()
                if c is not None:
                    c["deep_q"] = re.sub(r"^引导问题：", "", m.group(2)).strip()
                    in_deep = True
                continue
            m = re.match(r"^\*\*引导问题：(.+?)\*\*", ln)
            if m:
                c = _container()
                if c is not None:
                    if in_deep and c["deep_rows"]:
                        c["deep_q"] = m.group(1).strip()
                    else:
                        c["question"] = m.group(1).strip()
                continue
            # 选项表行：| A | 现象 | 判断 | 方案 | 处置 |
            if ln.startswith("|"):
                cells = [c.strip() for c in ln.strip().strip("|").split("|")]
                if len(cells) < 5 or set(cells[0]) <= {"-", " ", ":"}:
                    continue
                if cells[0] == "选项":
                    continue  # 表头行
                row = {"opt": cells[0], "phen": cells[1],
                       "judge": cells[2], "sol": cells[3], "disp": cells[4]}
                c = _container()
                if c is None:
                    continue
                (c["deep_rows"] if in_deep else c["rows"]).append(row)
        cats = [c for c in cats if c["scenes"] or c["rows"]]
        _TREE_CACHE[cache_key] = (mtime, cats)
        return cats
    except Exception as e:
        logger.warning(f"[vehicle_mode] 分叉树全树解析失败: {tree_file.name}: {e}")
        return []


# 承接话术池（1014 用户要求：固定句式显得呆，多点变化让人觉得是 AI 在说）。
# 抽象表述不带设备示例（flash 照抄 prompt 示例的教训）；池子随机 + 与上一条
# 不同（tree_phrase 记账），引导问题原文保留（它是准确信息）。
_TREE_LEADS = {
    "category": [
        "收到，这个方向咱们一步步确认。",
        "好，先把范围缩小一点。",
        "明白，我问几个关键表现来定位。",
        "行，按这个思路往下走。",
    ],
    "scene": [
        "好，再看细一点。",
        "嗯，继续收敛。",
        "收到，那具体是什么提示？",
        "OK，最后确认一下表现。",
    ],
    "option": [
        "这种情况常见处理如下：",
        "按经验先这么处理：",
        "这个表现对应的方案是：",
        "找到了，对应的处理是：",
    ],
}


def _pick_phrase(kind: str, rng: random.Random, last: str) -> str:
    pool = _TREE_LEADS[kind]
    p = rng.choice(pool)
    if p == last and len(pool) > 1:
        p = rng.choice([x for x in pool if x != last])
    return p


def _match_tree_step(query: str, tree: List[Dict], nav: Dict) -> Optional[Dict]:
    """按钮文案 → 树节点匹配（纯精确字符串比对，无 LLM）。

    匹配序：类目名（全局）→ 当前类目下场景名 → 当前位置（场景或类目直属）
    的选项现象列 + 深化分叉列。跨类目跳转由类目名全局匹配兜住。
    """
    q = (query or "").strip()
    if not q:
        return None
    for cat in tree:
        if cat["name"] == q:
            return {"kind": "category", "node": cat}
    cur_cat = next((c for c in tree if c["name"] == nav.get("category")), None)
    if cur_cat is None:
        return None
    for sc in cur_cat["scenes"]:
        if sc["name"] == q:
            return {"kind": "scene", "node": sc, "cat": cur_cat}
    container = next((s for s in cur_cat["scenes"] if s["name"] == nav.get("scene")),
                     None) or cur_cat
    for row in container["deep_rows"] + container["rows"]:
        if row["phen"] == q:
            return {"kind": "option", "node": row, "container": container, "cat": cur_cat}
    return None


def tree_direct_reply(query: str, vm_meta: Dict, model: str,
                      kb_root: Optional[Path] = None,
                      rng: Optional[random.Random] = None,
                      subpath: str = "") -> Optional[Dict]:
    """车型会话分叉树直出入口：命中返回组装好的回复（pipeline 直接下发）。

    返回 {kind, message, action, choices, nav}；未命中返回 None（走 AI）。
    vm_meta 是 memory.metadata["vehicle_mode"] 的引用：nav/话术记账直接写回，
    调用侧随后 save_memory 即持久（树导览轮次由调用侧 turns 机制记录）。
    subpath：已解析的知识目录名（磁盘原名，ORS-1082）——model 是**录入车型**
    （XQE122），车系目录是 XQE，不传 subpath 会按 XQE122 找分叉树 → 找不到，
    点选项就落不到直出（退回 LLM 慢路径）。
    """
    tree = parse_fork_tree(model, kb_root, subpath=subpath)
    if not tree:
        return None
    nav = vm_meta.get("tree_nav") or {}
    hit = _match_tree_step(query, tree, nav)
    if hit is None:
        return None
    rng = rng or random
    last_phrase = vm_meta.get("tree_phrase") or ""

    def _opt_reply(row: Dict) -> str:
        return "\n".join(filter(None, [
            f"**现象**：{row['phen']}",
            f"**原因**：{row['judge']}" if row["judge"] not in ("", "—") else "",
            f"**处理**：{row['sol']}",
            f"**处置**：{row['disp']}" if row["disp"] not in ("", "—") else "",
        ]))

    kind, node = hit["kind"], hit["node"]
    if kind == "category":
        lead = _pick_phrase("category", rng, last_phrase)
        if node["scenes"]:
            msg = f"{lead}\n\n**{node['name']}**——{node['question'] or '车是在什么情况下出现的？'}"
            choices = [sc["name"] for sc in node["scenes"]]
        else:
            msg = f"{lead}\n\n{node['question'] or '具体是什么表现？'}"
            choices = [r["phen"] for r in node["rows"]]
        vm_meta["tree_nav"] = {"category": node["name"], "scene": None}
        vm_meta["tree_phrase"] = lead
        return {"kind": kind, "message": msg, "action": "ask", "choices": choices}

    if kind == "scene":
        lead = _pick_phrase("scene", rng, last_phrase)
        msg = f"{lead}\n\n{node['question'] or '界面上有什么提示？'}"
        vm_meta["tree_nav"] = {"category": hit["cat"]["name"], "scene": node["name"]}
        vm_meta["tree_phrase"] = lead
        return {"kind": kind, "message": msg, "action": "ask",
                "choices": [r["phen"] for r in node["rows"]]}

    # 选项行：深化分叉入口行（解决方案列写明「走深化分叉」）→ 出深化引导；
    # 否则叶子回答，树导览结束（nav 清空，后续自由输入由 AI 接手，全程在 turns 里）
    container = hit["container"]
    row = node
    if container["deep_rows"] and "深化分叉" in row["sol"]:
        lead = _pick_phrase("scene", rng, last_phrase)
        msg = f"{lead}\n\n{container['deep_q'] or '再确认一个观察点'}"
        vm_meta["tree_phrase"] = lead
        return {"kind": "option", "message": msg, "action": "ask",
                "choices": [r["phen"] for r in container["deep_rows"]]}
    lead = _pick_phrase("option", rng, last_phrase)
    msg = f"{lead}\n\n{_opt_reply(row)}"
    vm_meta["tree_nav"] = {}
    vm_meta["tree_phrase"] = lead
    return {"kind": "option", "message": msg, "action": "answer", "choices": None}


# ============================================================
# 模式注册主流程
# ============================================================
async def register_mode(req: ModeConfirmRequest,
                        memory_manager: Optional[MemoryManager] = None) -> dict:
    """按 scene 取录入信息行 → session 注册定制模式 → 返回手册清单。

    返回：{code: 0, data: {confirmed, model, domain, manual_docs, opening}} 或 {code: 1, message}。
    memory_manager 参数供测试注入 mock；缺省用真实 get_memory_manager()。
    """
    scene = (req.scene or "").strip()
    if not _SCENE_RE.match(scene):
        logger.warning(
            f"[vehicle_mode] 场景值非法拦截: scene={scene!r}, session={req.session_id}",
        )
        return {"code": 1, "message": "扫码信息无效，请重新扫码或联系管理员"}

    row = await _lookup_qrcode(scene)
    if row is None or (row.status or "").strip() != _STATUS_PUBLISHED:
        # 实验阶段：查无此码 / 码不可用都报错拦住，不降级常规模式（前端展示提示）
        logger.warning(
            f"[vehicle_mode] 二维码不可用拦截: scene={scene} "
            f"status={getattr(row, 'status', None)!r}, session={req.session_id}",
        )
        return {"code": 1, "message": "该二维码未录入信息或不可用，请联系管理员"}

    # 车型以库内那行为准（录入界面必填，正常不会为空）；为空时用弹窗确认值兜底。
    # 归一到大写：prompt 里的「车型 xxx」与 KB 入库口径一致（小写会失配）。
    raw_model = (row.vehicle_model or "").strip() or (req.model or "").strip()
    model_key = model_to_subpath(raw_model)
    if not model_key:
        logger.warning(
            f"[vehicle_mode] 车型为空拦截: scene={scene}, session={req.session_id}",
        )
        return {"code": 1, "message": "该二维码未录入车型信息，请联系管理员"}

    # 车型 → 知识目录解析（ORS-1082 模糊匹配）：字母族键 XQE122/xqe122-2→XQE、
    # XQC163→XQC。产出目录清单（可多个）+ 平铺目录的文件名白名单，手册清单与
    # 检索域都按它走；解析不到目录时回退原值（与旧版一致：手册空 + 检索
    # 0 召回，不阻塞模式注册）。
    kb_root = _default_kb_root()
    resolved = resolve_model_dirs(model_key, kb_root=kb_root)
    model_dirs = list(resolved.get("dirs") or []) or [model_key]
    model_files = dict(resolved.get("files") or {})
    model_primary = str(resolved.get("primary") or model_key)
    model_family_key = str(resolved.get("family") or "")

    # 项目名/客户名同口径：库内非空以库内为准，为空用弹窗确认值兜底
    project_name = (row.project_name or "").strip() or (req.project_name or "").strip()
    customer_name = (row.customer_name or "").strip() or (req.customer_name or "").strip()

    domain = model_to_domain(model_key)
    mode_info = {
        "model": model_key,
        # 解析出的知识目录（磁盘原名，可多个）：手册清单与检索域按它走
        "model_dirs": model_dirs,
        # 平铺目录的文件名白名单 {目录: [文件名]}：检索层只能按 sub_domain
        # 过滤（product_catalog 下 66 款车型平铺），白名单在应用层二次收窄
        "model_files": model_files,
        # 主目录（分叉树/开场题/车型介绍页读它）
        "model_primary": model_primary,
        # 字母族键（XQE/XQC/…）
        "model_family": model_family_key,
        # 录入原串（只做追溯，不参与匹配）
        "model_raw": raw_model,
        "domain": domain,
        # scene = 行 id：本会话绑定的是「哪一台车」的唯一身份，供追溯
        "scene": scene,
        # 扫码链路无车号概念（车号字段保留给其他调用方；空则不注入 prompt 车号段）
        "vehicle_code": (req.vehicle_code or "").strip(),
        "project_name": project_name,
        "customer_name": customer_name,
        "location": (row.project_location or "").strip(),
        "registered_at": int(time.time()),
    }

    mm = memory_manager or await _default_memory_manager()
    memory = await mm.get_memory(req.session_id)
    # SOP 外显清单：排除引导设施文档（分叉树/故障码表的入口在对话里）；
    # 多目录合并 + 平铺目录按文件名白名单收窄
    manual_docs = list_manual_docs_multi(model_dirs, model_files, kb_root=kb_root,
                                         exclude_guide_docs=True)
    # 分叉树/开场题/车型介绍按主目录单目录读（平铺目录里没有这两类资产）
    opening = build_opening(parse_fork_tree_choices(model_key, kb_root=kb_root,
                                                     subpath=model_primary))
    vehicle_guide = build_vehicle_guide(model_key, kb_root=kb_root,
                                        files=model_files.get(model_primary),
                                        subpath=model_primary)
    if opening:
        # 开场类目随模式入 metadata：pipeline 识别「用户点开场气泡」（query
        # == 类目原文）做零 LLM 短接，不必再读分叉树文件
        mode_info["opening_choices"] = opening["choices"]
    # 覆盖重注册 = 幂等（前端刷新即重调本接口）
    memory.metadata["vehicle_mode"] = mode_info
    await mm.save_memory(memory)

    logger.info(
        f"[vehicle_mode] 定制模式注册: session={req.session_id} scene={scene} "
        f"model={model_key}(raw={raw_model}) family={model_family_key or '-'} "
        f"dirs={model_dirs} manual={len(manual_docs)}篇 "
        f"opening={len(opening['choices']) if opening else 0}类",
    )
    return {
        "code": 0,
        "data": {
            "confirmed": True,
            "model": model_key,
            "model_raw": raw_model,
            "model_family": model_family_key,
            "model_primary": model_primary,
            "model_dirs": model_dirs,
            "model_files": model_files,
            "domain": domain,
            "manual_docs": manual_docs,
            "opening": opening,
            "vehicle_guide": vehicle_guide,
        },
    }


async def _default_memory_manager() -> MemoryManager:
    from ai.core import get_memory_manager
    return await get_memory_manager()


def register_vehicle_mode_routes(qa_router) -> None:
    """把模式确认路由挂到 qa_router（router.py 一行调用，其余逻辑全部隔离在本模块）。"""
    from starlette.concurrency import run_in_threadpool  # noqa: F401（预留同步重活用）

    @qa_router.post("/mode/confirm", summary="车型定制模式确认（扫码入口：校验车辆档案并注册会话模式）")
    async def mode_confirm(req: ModeConfirmRequest):
        return await register_mode(req)
