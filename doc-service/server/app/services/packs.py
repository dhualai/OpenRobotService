from __future__ import annotations

import hashlib
import io
import json
import zipfile

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infra.crypto import ContentCrypto
from app.infra.models import Document, Module
from app.policy import Principal, can_edit_module_content
from app.services.topology import module_view

# Bump when AGENTS.md constraints change. Old edit_token without this rev is stale.
AGENTS_REV = "9"

_AGENTS_MD_BODY = """# Agent 工作约定

规则版本 9。

【必读 · 行文优先】改正文前必须全文阅读本文件。后面的流程章节（讨论区、需求、多用户、权限）不能代替「行文」。跳过「行文」、或只扫标题，视为未读。import 成功只说明校验码对得上，不说明正文合格。正文不满足「行文」，不要 import。

工程包必须带本文件；import 核对其最后一行校验码。规则更新后须重新 export。授权包不用为此重下。旧工程包 import 会 409，返回会要求全文重读，尤其行文。

只改 write 模块 content/<id>/doc.md、讨论稿 content/doc.md，或需求 content/doc.md。
【产品】仅可改 H2：新增内容 / 修复内容 / 备注。
禁止写入说明、注意、补充、行首备注旁白。

## 行文（强制，不可跳过）

本节是改正文的硬约束，不是附录。写每一段、交稿前、import 前都要对照。下面六条有一条不满足，就不要 import。

读者是同事，不是听众。文章用来扫读、对照、作决定，不是用来念。

语句必须连贯。相邻句要接得上，主题换了先交代。读起来有一点点不连贯都不行。

文本风格严格避免以下情况：

1. 句式过碎
2. 主题跳换快
3. 指代不清，缺主动宾
4. 口语和书面语混用
5. 过长的段落用文字堆砌
6. 语句不连贯

写完每一段自检：一个没读过前文的人能否不靠猜读懂这一句；这一句跟上句是否接得上；超过两行的段落是否在塞流程或概念，而不是在收一句结论；复杂处是拆开了，还是用一段话顶过去。过不了就改，不要先灌上去。

简洁是一次只说一件事，结构清楚，图承担流程。简洁不是把信息量压进更少的字。短句堆在一起，看起来干净，读起来一步一停，那是速记，不是文章。

- 用完整书面句：主语、谓语、宾语都在。动作写全（谁对什么做了什么）。
- 需要连接和过渡。主题换了，先用小标题或一句交代，再写新内容。
- 一句话只推进一个意思。并列用列表，不要用分号把三件事串进一句。
- 一段通常不超过两行。第三行开始，就要拆段、改列表，或配图。
- 句与句之间要有接榫：上一句的结果或对象，能接到下一句的起点。

禁止口语、残句、内部黑话。例如「上游给」写成「由上游系统推送占用状态」；「满的挡路，空的不挡」写成「满库位会阻挡路径；空库位不会阻挡路径」。指代必须在本页自足。

禁词：拍板、定死。不要突兀单用「切」（「再切一条」这类）；「切换」「切分」「切开」可以。

主题跳转用小标题。并列用列表。方案对照用表。有先后的流程用编号步骤或图。决定项不要写成一段里塞五件事：一条决定一个标题句，细节用子列表。

配图尽量用 SVG。图与正文相互支撑，不要把同一信息写两遍：图承担结构与流程，正文只补图里读不到的前提、结论或例外。

描述复杂流程或概念时：要么把文字拆开，要么上图。不要用超过两行的段落把流程讲完。概念第一次出现时给图。图的标题就是结论。图后可以跟一句收口，不要再把图里的流程复述一遍。

产品模块的「后续方向」：一句框定 + 一张总览图 + 一条链到讨论稿。细节和结论只放讨论，不要在模块页展开。

强调一句结论时用灯炮条，不要满篇加粗，不要用引用块冒充强调。一页最多一两处。条里只放一句结论：

<div class="doc-tip"><span aria-hidden="true">💡</span><strong>告诉现场实施，用最少的成本恢复现场运行。</strong></div>

交稿前：语句连贯、相邻句接得上；没有速记残句和内部黑话；没有禁词拍板、定死，没有突兀单用的切；没有超过两行、内含多个转折的段落；配图尽量是 SVG；图文不重复同一信息；强调用灯炮条而不是满篇加粗；流程有步骤或图；本页术语前后一致（改了名字就全文替换）；模块页不重复讨论稿里的结论细节。

正文第一行标题必须和结构图上的模块名称一致：`# 名称` 或 `# 功能 · 名称`。名称对不上不要 import。

## 讨论区

讨论稿同样遵守上文行文。改正文走工程包：GET /api/v1/drafts/{id}/pack，改 content/doc.md 后 POST 回去。不要用 PATCH 改正文。新建只 POST 标题，再 export。

## 需求

需求同样遵守上文行文。改正文走工程包：GET /api/v1/products/{product}/requirements/{id}/pack，改 content/doc.md 后 POST 回去。不要用 PATCH 改正文。新建只 POST 标题，再 export。

## 多用户改同一模块

edit_token（A）= 正文指纹加规则版本。他人改过或规则变了 → A 失效，必须重新 export。

1. 若本地已有 A：GET .../packs/{id}/edit-token?since=A
   - unchanged=true：免 export，改完 import，PACK.json 的 edit_token 传 A
   - 否则：必须重新 export
2. POST import；成功则保存返回的 edit_token 为新的 A
3. 409：正文或 AGENTS.md 已更新。必须重新 export，全文阅读 AGENTS.md 并检查所有更新项，尤其「行文」，改完后再 import。不要只按流程章节重试。
4. 403 write_revoked：当前账号已无此模块写权限，GET writable-modules 核对

## 权限变化

- 授权包 modules.json 会过期。用户要求写某模块时，先 GET writable-modules 或 GET modules/{id} 看 can_edit，不要用授权包快照当现权限
- 权限被收回后再 import / export / edit-token，服务器返回 403 write_revoked 与 hint，不要当普通失败重试同一包
- 在模块 X 下新增的子模块：对 X 有写权限的人默认可改该子模块（级联，不另发授权）；X 的写权限被收回后，子模块一并失去
"""


def _rules_ack(body: str, rev: str) -> str:
    digest = hashlib.sha256(f"{rev}\n{body.strip()}\n".encode("utf-8")).hexdigest()[:8]
    return f"{rev}-{digest}"


def compose_agents_md(body: str | None = None, rev: str | None = None) -> str:
    b = (body if body is not None else _AGENTS_MD_BODY).strip() + "\n"
    r = rev or AGENTS_REV
    return b + f"校验码 {_rules_ack(b, r)}\n"


AGENTS_MD = compose_agents_md()

WRITE_REVOKED_HINT = (
    "当前账号已无此模块写权限。请 GET writable-modules 核对，或请管理员重新授权后再改。"
)
AGENTS_UPDATED_HINT = (
    "AGENTS.md 已经更新。请重新 export 工程包，全文阅读 AGENTS.md，并检查所有更新项，"
    "尤其「行文」规则（强制，不可跳过）。只按流程章节操作、不按行文改稿，视为未读本文件。"
    "按行文改完正文后再 import。"
)
EDIT_CONFLICT_HINT = (
    "正文已被他人更新。请重新 export 工程包，全文阅读 AGENTS.md 并检查所有更新项，"
    "尤其「行文」规则，改完后再 import。"
)
PACK_SKIP_KINDS = frozenset({"product_hub", "product_release", "group", "metrics"})


def conflict_hint(token: str) -> str:
    if not token_rules_current(token):
        return AGENTS_UPDATED_HINT
    return EDIT_CONFLICT_HINT


def compose_edit_token(content_hash: str | None) -> str:
    h = (content_hash or "").strip()
    if not h:
        return AGENTS_REV
    return f"{h}.{AGENTS_REV}"


def token_rules_current(token: str) -> bool:
    t = (token or "").strip()
    if not t:
        return False
    if "." in t:
        return t.rsplit(".", 1)[-1] == AGENTS_REV
    return t == AGENTS_REV


def agents_ack_line(text: str | None = None) -> str:
    raw = (text if text is not None else AGENTS_MD).replace("\r\n", "\n").strip()
    if not raw:
        return ""
    return raw.split("\n")[-1].strip()


def require_agents_md(z: zipfile.ZipFile) -> tuple[int, dict] | None:
    if "AGENTS.md" not in z.namelist():
        return 400, {
            "ok": False,
            "error": "missing_agents",
            "hint": "工程包须含 AGENTS.md。请重新 export，全文阅读 AGENTS.md（尤其「行文」）后再 import。",
        }
    try:
        text = z.read("AGENTS.md").decode("utf-8")
    except UnicodeDecodeError:
        return 400, {
            "ok": False,
            "error": "missing_agents",
            "hint": "工程包须含 AGENTS.md。请重新 export，全文阅读 AGENTS.md（尤其「行文」）后再 import。",
        }
    if agents_ack_line(text) != agents_ack_line():
        return 409, {"ok": False, "error": "agents_stale", "hint": AGENTS_UPDATED_HINT}
    return None


def write_denied(module_id: str) -> tuple[int, dict]:
    return 403, {
        "ok": False,
        "error": "write_revoked",
        "module_id": module_id,
        "hint": WRITE_REVOKED_HINT,
    }


def _current_doc(db: Session, product: str, module_id: str, version: str) -> Document | None:
    return db.scalar(
        select(Document).where(
            Document.product == product,
            Document.module_id == module_id,
            Document.version == version,
        )
    )


def _module_title(crypto: ContentCrypto, m: Module) -> str:
    if m.title_blob:
        return crypto.decrypt_text(m.title_blob, aad=f"module.title:{m.product}:{m.id}")
    return m.id


def _writable_module(
    db: Session, principal: Principal, product: str, module_id: str
) -> tuple[Module | None, tuple[int, dict] | None]:
    m = db.scalar(select(Module).where(Module.product == product, Module.id == module_id))
    if not m:
        return None, (404, {"ok": False, "error": "not_found"})
    if not can_edit_module_content(principal, module_view(m)):
        return None, write_denied(module_id)
    return m, None


def _writable_items(db: Session, crypto: ContentCrypto, principal: Principal, product: str) -> list[dict]:
    items = [
        {"id": m.id, "title": _module_title(crypto, m), "product": product}
        for m in db.scalars(select(Module).where(Module.product == product))
        if m.kind not in PACK_SKIP_KINDS and can_edit_module_content(principal, module_view(m))
    ]
    items.sort(key=lambda x: x["title"])
    return items


def export_pack(
    db: Session,
    crypto: ContentCrypto,
    principal: Principal,
    product: str,
    module_id: str,
) -> tuple[int, bytes | dict]:
    m, err = _writable_module(db, principal, product, module_id)
    if err:
        return err
    assert m is not None
    from app.services.modules import ensure_latest_pointer

    ensure_latest_pointer(db, crypto, product, m)
    doc = _current_doc(db, product, module_id, m.current_version)
    body = (
        crypto.decrypt_text(doc.body_blob, aad=f"document:{product}:{module_id}:{m.current_version}")
        if doc
        else ""
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("AGENTS.md", AGENTS_MD)
        z.writestr(
            "PACK.json",
            json.dumps(
                {
                    "product": product,
                    "module_id": module_id,
                    "edit_token": compose_edit_token(doc.content_hash if doc else ""),
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
        z.writestr(f"content/{module_id}/doc.md", body)
    return 200, buf.getvalue()


def edit_token_status(
    db: Session,
    principal: Principal,
    product: str,
    module_id: str,
    since: str = "",
) -> tuple[int, dict]:
    m, err = _writable_module(db, principal, product, module_id)
    if err:
        return err
    assert m is not None
    from app.infra.crypto import ContentCrypto
    from app.settings import get_settings
    from app.services.modules import ensure_latest_pointer

    ensure_latest_pointer(db, ContentCrypto(get_settings().topo_content_key), product, m)
    doc = _current_doc(db, product, module_id, m.current_version)
    token = compose_edit_token(doc.content_hash if doc else "")
    since = (since or "").strip()
    unchanged = bool(since) and since == token
    return 200, {
        "ok": True,
        "product": product,
        "module_id": module_id,
        "edit_token": token,
        "since": since or None,
        "unchanged": unchanged if since else None,
        "export_required": (not unchanged) if since else None,
        "can_edit": True,
        "hint": (
            "unchanged=true 时可免 export，import 时 PACK.json 的 edit_token 传该值；否则重新 GET export"
        ),
    }


def list_writable_modules(db: Session, crypto: ContentCrypto, principal: Principal, product: str) -> dict:
    return {"ok": True, "product": product, "items": _writable_items(db, crypto, principal, product)}


def import_pack(
    db: Session,
    crypto: ContentCrypto,
    principal: Principal,
    product: str,
    module_id: str,
    raw: bytes,
) -> tuple[int, dict]:
    from app.services.topology import save_document

    if not raw or raw[:2] != b"PK":
        return 400, {"ok": False, "error": "not_zip"}
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        return 400, {"ok": False, "error": "not_zip"}
    names = set(z.namelist())
    if "PACK.json" not in names:
        return 400, {"ok": False, "error": "missing_pack"}
    agents_err = require_agents_md(z)
    if agents_err:
        return agents_err
    try:
        pack = json.loads(z.read("PACK.json").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return 400, {"ok": False, "error": "missing_pack"}
    pack_product = str(pack.get("product") or "").strip().lower()
    pack_mid = str(pack.get("module_id") or "").strip()
    if pack_product and pack_product != product:
        return 400, {"ok": False, "error": "product_mismatch"}
    if pack_mid and pack_mid != module_id:
        return 400, {"ok": False, "error": "module_mismatch"}
    m, err = _writable_module(db, principal, product, module_id)
    if err:
        return err
    assert m is not None
    from app.services.modules import ensure_latest_pointer

    ensure_latest_pointer(db, crypto, product, m)
    path = f"content/{module_id}/doc.md"
    if path not in names:
        alt = next((n for n in names if n.endswith("/doc.md") or n == "doc.md"), "")
        path = alt
    if not path:
        return 400, {"ok": False, "error": "missing_doc"}
    try:
        body = z.read(path).decode("utf-8")
    except UnicodeDecodeError:
        return 400, {"ok": False, "error": "missing_doc"}
    token = str(pack.get("edit_token") or pack.get("base_edit_token") or "").strip()
    doc = _current_doc(db, product, module_id, m.current_version)
    current = compose_edit_token(doc.content_hash if doc else "")
    if token != current:
        same_body = bool(doc and doc.content_hash and crypto.content_hash(body) == doc.content_hash)
        if same_body and token_rules_current(token):
            return 200, {
                "ok": True,
                "written": [module_id],
                "content_hash": doc.content_hash,
                "edit_token": current,
            }
        return 409, {"ok": False, "error": "edit_conflict", "hint": conflict_hint(token)}
    code, out = save_document(
        db, crypto, principal, product, module_id, body, snapshot=True, from_pack=True
    )
    if code != 200:
        return code, out
    return 200, {"ok": True, "written": [module_id], **out}


def export_draft_pack(
    db: Session, crypto: ContentCrypto, principal: Principal, did: str
) -> tuple[int, bytes | dict]:
    from app.infra.models import Draft
    from app.services import drafts as draft_svc

    row = db.get(Draft, did)
    if not row or not draft_svc._can_view(db, principal, row):
        return 404, {"ok": False, "error": "not_found"}
    body = crypto.decrypt_text(row.body_blob, aad=f"draft:{did}")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("AGENTS.md", AGENTS_MD)
        z.writestr(
            "PACK.json",
            json.dumps(
                {
                    "kind": "draft",
                    "draft_id": did,
                    "title": row.title,
                    "edit_token": compose_edit_token(row.content_hash),
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
        z.writestr("content/doc.md", body)
    return 200, buf.getvalue()


def draft_edit_token_status(
    db: Session, principal: Principal, did: str, since: str = ""
) -> tuple[int, dict]:
    from app.infra.models import Draft
    from app.services import drafts as draft_svc

    row = db.get(Draft, did)
    if not row or not draft_svc._can_view(db, principal, row):
        return 404, {"ok": False, "error": "not_found"}
    token = compose_edit_token(row.content_hash)
    since = (since or "").strip()
    unchanged = bool(since) and since == token
    return 200, {
        "ok": True,
        "draft_id": did,
        "edit_token": token,
        "since": since or None,
        "unchanged": unchanged if since else None,
        "export_required": (not unchanged) if since else None,
        "can_edit": True,
        "hint": (
            "unchanged=true 时可免 export，import 时 PACK.json 的 edit_token 传该值；否则重新 GET export"
        ),
    }


def import_draft_pack(
    db: Session, crypto: ContentCrypto, principal: Principal, did: str, raw: bytes
) -> tuple[int, dict]:
    from app.infra.models import Draft
    from app.services import drafts as draft_svc

    if not raw or raw[:2] != b"PK":
        return 400, {"ok": False, "error": "not_zip"}
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        return 400, {"ok": False, "error": "not_zip"}
    names = set(z.namelist())
    if "PACK.json" not in names:
        return 400, {"ok": False, "error": "missing_pack"}
    agents_err = require_agents_md(z)
    if agents_err:
        return agents_err
    try:
        pack = json.loads(z.read("PACK.json").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return 400, {"ok": False, "error": "missing_pack"}
    pack_id = str(pack.get("draft_id") or "").strip()
    if pack_id and pack_id != did:
        return 400, {"ok": False, "error": "module_mismatch"}
    path = "content/doc.md"
    if path not in names:
        alt = next((n for n in names if n.endswith("/doc.md") or n == "doc.md"), "")
        path = alt
    if not path:
        return 400, {"ok": False, "error": "missing_doc"}
    try:
        body = z.read(path).decode("utf-8")
    except UnicodeDecodeError:
        return 400, {"ok": False, "error": "missing_doc"}
    row = db.get(Draft, did)
    if not row or not draft_svc._can_view(db, principal, row):
        return 404, {"ok": False, "error": "not_found"}
    token = str(pack.get("edit_token") or pack.get("base_edit_token") or "").strip()
    current = compose_edit_token(row.content_hash)
    if token != current:
        same_body = bool(row.content_hash and crypto.content_hash(body) == row.content_hash)
        if same_body and token_rules_current(token):
            return 200, {
                "ok": True,
                "written": [did],
                "content_hash": row.content_hash,
                "edit_token": current,
            }
        return 409, {"ok": False, "error": "edit_conflict", "hint": conflict_hint(token)}
    title = str(pack.get("title") or "").strip() or None
    code, out = draft_svc.update_draft(
        db, crypto, principal, did, title=title, markdown=body, from_pack=True
    )
    if code != 200:
        return code, out
    return 200, {
        "ok": True,
        "written": [did],
        "content_hash": out.get("content_hash") if isinstance(out, dict) else "",
        "edit_token": out.get("edit_token") if isinstance(out, dict) else "",
    }


def export_requirement_pack(
    db: Session, crypto: ContentCrypto, principal: Principal, product: str, rid: str
) -> tuple[int, bytes | dict]:
    from app.infra.models import Requirement, RequirementDoc
    from app.policy import can_edit_requirement

    row = db.get(Requirement, rid)
    if not row or row.product != product:
        return 404, {"ok": False, "error": "not_found"}
    if not can_edit_requirement(principal, row.created_by):
        return 403, {"ok": False, "error": "forbidden"}
    doc = db.get(RequirementDoc, rid)
    body = crypto.decrypt_text(doc.body_blob, aad=f"requirement:{rid}") if doc else ""
    token = compose_edit_token(doc.content_hash if doc else "")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("AGENTS.md", AGENTS_MD)
        z.writestr(
            "PACK.json",
            json.dumps(
                {
                    "kind": "requirement",
                    "product": product,
                    "requirement_id": rid,
                    "title": row.title,
                    "edit_token": token,
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
        z.writestr("content/doc.md", body)
    return 200, buf.getvalue()


def requirement_edit_token_status(
    db: Session, principal: Principal, product: str, rid: str, since: str = ""
) -> tuple[int, dict]:
    from app.infra.models import Requirement, RequirementDoc
    from app.policy import can_edit_requirement

    row = db.get(Requirement, rid)
    if not row or row.product != product:
        return 404, {"ok": False, "error": "not_found"}
    if not can_edit_requirement(principal, row.created_by):
        return 403, {"ok": False, "error": "forbidden"}
    doc = db.get(RequirementDoc, rid)
    token = compose_edit_token(doc.content_hash if doc else "")
    since = (since or "").strip()
    unchanged = bool(since) and since == token
    return 200, {
        "ok": True,
        "product": product,
        "requirement_id": rid,
        "edit_token": token,
        "since": since or None,
        "unchanged": unchanged if since else None,
        "export_required": (not unchanged) if since else None,
        "can_edit": True,
        "hint": (
            "unchanged=true 时可免 export，import 时 PACK.json 的 edit_token 传该值；否则重新 GET export"
        ),
    }


def import_requirement_pack(
    db: Session, crypto: ContentCrypto, principal: Principal, product: str, rid: str, raw: bytes
) -> tuple[int, dict]:
    from app.infra.models import Requirement, RequirementDoc
    from app.services import requirements as req_svc

    if not raw or raw[:2] != b"PK":
        return 400, {"ok": False, "error": "not_zip"}
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        return 400, {"ok": False, "error": "not_zip"}
    names = set(z.namelist())
    if "PACK.json" not in names:
        return 400, {"ok": False, "error": "missing_pack"}
    agents_err = require_agents_md(z)
    if agents_err:
        return agents_err
    try:
        pack = json.loads(z.read("PACK.json").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return 400, {"ok": False, "error": "missing_pack"}
    pack_id = str(pack.get("requirement_id") or "").strip()
    pack_product = str(pack.get("product") or "").strip().lower()
    if pack_id and pack_id != rid:
        return 400, {"ok": False, "error": "module_mismatch"}
    if pack_product and pack_product != product:
        return 400, {"ok": False, "error": "product_mismatch"}
    path = "content/doc.md"
    if path not in names:
        alt = next((n for n in names if n.endswith("/doc.md") or n == "doc.md"), "")
        path = alt
    if not path:
        return 400, {"ok": False, "error": "missing_doc"}
    try:
        body = z.read(path).decode("utf-8")
    except UnicodeDecodeError:
        return 400, {"ok": False, "error": "missing_doc"}
    row = db.get(Requirement, rid)
    if not row or row.product != product:
        return 404, {"ok": False, "error": "not_found"}
    doc = db.get(RequirementDoc, rid)
    token = str(pack.get("edit_token") or pack.get("base_edit_token") or "").strip()
    current = compose_edit_token(doc.content_hash if doc else "")
    if token != current:
        same_body = bool(doc and doc.content_hash and crypto.content_hash(body) == doc.content_hash)
        if same_body and token_rules_current(token):
            return 200, {
                "ok": True,
                "written": [rid],
                "content_hash": doc.content_hash if doc else "",
                "edit_token": current,
            }
        return 409, {"ok": False, "error": "edit_conflict", "hint": conflict_hint(token)}
    title = str(pack.get("title") or "").strip() or None
    code, out = req_svc.update_requirement(
        db, crypto, principal, rid, title=title, markdown=body, from_pack=True
    )
    if code != 200:
        return code, out
    return 200, {
        "ok": True,
        "written": [rid],
        "content_hash": out.get("content_hash") if isinstance(out, dict) else "",
        "edit_token": out.get("edit_token") if isinstance(out, dict) else "",
    }


def use_pack_denied() -> tuple[int, dict]:
    return 409, {
        "ok": False,
        "error": "use_pack",
        "hint": (
            "Agent 须通过工程包改正文。请 GET export，全文阅读 AGENTS.md（尤其「行文」规则），再 POST import。"
        ),
    }


def build_auth_pack(
    db: Session,
    crypto: ContentCrypto,
    principal: Principal,
    token: str,
    base_url: str,
) -> bytes:
    from app.infra.models import Requirement
    from app.policy import can_edit_requirement, can_edit_requirements
    base = (base_url or "").rstrip("/")
    products = [p for p in sorted(principal.products) if p in ("usp", "ors")]
    if principal.role == "admin":
        products = ["usp", "ors"]
    if not products:
        products = ["usp"]
    mods = {p: _writable_items(db, crypto, principal, p) for p in products}
    reqs = []
    if can_edit_requirements(principal):
        for r in db.scalars(select(Requirement)):
            if not can_edit_requirement(principal, r.created_by):
                continue
            reqs.append(
                {
                    "id": r.id,
                    "title": r.title,
                    "product": r.product,
                    "status": r.status,
                }
            )
    auth_doc = {
        "format": "topo-agent-auth",
        "version": 1,
        "base_url": base,
        "username": principal.username,
        "token": token,
        "auth_header": f"Bearer {token}",
        "role": principal.role,
        "products": products,
        "apis": {
            "export": "GET {base_url}/api/v1/products/{product}/packs/{module_id}",
            "import": "POST {base_url}/api/v1/products/{product}/packs/{module_id}",
            "edit_token": "GET {base_url}/api/v1/products/{product}/packs/{module_id}/edit-token?since={edit_token}",
            "writable": "GET {base_url}/api/v1/products/{product}/writable-modules",
            "module": "GET {base_url}/api/v1/products/{product}/modules/{module_id}",
            "requirements": "GET {base_url}/api/v1/products/{product}/requirements",
            "drafts": "GET {base_url}/api/v1/drafts",
            "draft_export": "GET {base_url}/api/v1/drafts/{draft_id}/pack",
            "draft_import": "POST {base_url}/api/v1/drafts/{draft_id}/pack",
            "draft_edit_token": "GET {base_url}/api/v1/drafts/{draft_id}/edit-token?since={edit_token}",
            "req_export": "GET {base_url}/api/v1/products/{product}/requirements/{req_id}/pack",
            "req_import": "POST {base_url}/api/v1/products/{product}/requirements/{req_id}/pack",
            "req_edit_token": "GET {base_url}/api/v1/products/{product}/requirements/{req_id}/edit-token?since={edit_token}",
        },
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("AGENTS.md", AGENTS_MD)
        z.writestr("auth.json", json.dumps(auth_doc, ensure_ascii=False, indent=2) + "\n")
        z.writestr(
            "modules.json",
            json.dumps(
                {"username": principal.username, "role": principal.role, "modules_by_product": mods},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )
        if reqs or principal.role in ("pm", "pjm", "engineer", "admin", "supervisor"):
            z.writestr(
                "requirements.json",
                json.dumps(
                    {"username": principal.username, "role": principal.role, "requirements": reqs},
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
            )
    return buf.getvalue()
