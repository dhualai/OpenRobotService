"""存量评论区图片 VLM 描述回填（ORS-#1035 一次性脚本）。

打标链路（上传时描述）只对新上传生效，此前传的图没有 desc——归档报告/
讨论摘要里这些图只能报「附 N 张图片」。本脚本扫历史评论里无描述的图片
附件，经后端附件代理取字节 → AI describe 端点出描述 → 写回 attachments
JSON（{path, filename, desc}）。

用法（默认 dry-run 只列清单，--apply 才写库）：
    DATABASE_URL='mysql+pymysql://root:123456@127.0.0.1:13307/helpdesk_test' \
    DESC_WEB_BASE='https://usp.ep-zl.com/t' DESC_USER=admin DESC_PASS='***' \
    python ai/scripts/backfill_comment_image_desc.py --limit 50 [--apply]

依赖：本地 DB 隧道（13307→服务器 3306，见 dar_studio）；DESC_WEB_BASE 指
向对应环境（/t 测试 /p 生产）。
"""
import argparse
import base64
import json
import os
import sys
import time
from urllib.parse import quote

import httpx

_IMAGE_EXTS = {"jpg", "jpeg", "png", "bmp", "gif", "webp"}


def _is_image_path(p: str) -> bool:
    return p.lower().rsplit(".", 1)[-1] in _IMAGE_EXTS


def _iter_pending(rows):
    """yield (comment_id, entry_index, path, filename)；只挑无 desc 的图片项。"""
    for cid, atts in rows:
        if not atts:
            continue
        atts = json.loads(atts) if isinstance(atts, str) else atts
        if not isinstance(atts, list):
            continue
        for i, a in enumerate(atts):
            if isinstance(a, dict):
                p = str(a.get("path") or "")
                if a.get("desc") or not _is_image_path(p):
                    continue
                yield cid, i, p, str(a.get("filename") or os.path.basename(p))
            elif isinstance(a, str) and _is_image_path(a):
                yield cid, i, a, os.path.basename(a)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=50, description="最多回填图片数")
    ap.add_argument("--apply", action="store_true", help="真写库（缺省 dry-run）")
    args = ap.parse_args()

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"),
                    override=False)
    except Exception:
        pass

    web = (os.getenv("DESC_WEB_BASE") or "").rstrip("/")
    user, pwd = os.getenv("DESC_USER") or "", os.getenv("DESC_PASS") or ""
    if not (web and user and pwd):
        sys.exit("请配 DESC_WEB_BASE / DESC_USER / DESC_PASS（对应环境地址与 admin 账号）")

    from ai.core.database import SessionLocal
    from sqlalchemy import text
    db = SessionLocal()
    try:
        rows = db.execute(text(
            "SELECT id, attachments FROM task_comments "
            "WHERE attachments IS NOT NULL AND attachments != '' "
            "ORDER BY id DESC LIMIT 3000")).fetchall()
    finally:
        db.close()
    pending = list(_iter_pending(rows))
    print(f"共 {len(pending)} 张无描述图片，处理前 {min(args.limit, len(pending))} 张"
          + ("（dry-run）" if not args.apply else ""))

    client = httpx.Client(timeout=60.0)
    tok = client.post(f"{web}/api/auth/login",
                      json={"username": user, "password": pwd}).json().get("access_token")
    H = {"Authorization": f"Bearer {tok}"}

    ok = fail = 0
    seen_paths = set()
    updates: dict = {}
    for cid, idx, path, filename in pending:
        if len(seen_paths) >= args.limit:
            break
        if path in seen_paths:
            continue
        seen_paths.add(path)
        try:
            url = (f"{web}/api/tasks/attachments/download?path={quote(path)}"
                   f"&filename={quote(filename)}&token={quote(tok)}")
            raw = client.get(url).content
            if not raw:
                raise RuntimeError("代理取字节为空")
            ext = path.lower().rsplit(".", 1)[-1]
            mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(ext, f"image/{ext}")
            uri = f"data:{mime};base64,{base64.b64encode(raw).decode()}"
            r = client.post(f"{web}/api/ai/qa/media/describe", json={"images": [uri]})
            d = r.json()
            if int(d.get("code") or 0) != 0:
                raise RuntimeError(str(d.get("message"))[:80])
            desc = str((d.get("data") or {}).get("desc") or "").strip()
            if not desc:
                raise RuntimeError("描述为空")
            updates.setdefault(cid, {})[idx] = (path, filename, desc)
            ok += 1
            print(f"  ✓ #{cid} {filename[:30]}: {desc[:50]}")
            time.sleep(0.3)  # 轻限速
        except Exception as e:
            fail += 1
            print(f"  ✗ #{cid} {filename[:30]}: {e}")

    if args.apply and updates:
        db = SessionLocal()
        written = 0
        try:
            for cid, idx_map in updates.items():
                row = db.execute(text("SELECT attachments FROM task_comments WHERE id=:i"),
                                 {"i": cid}).fetchone()
                if not row or not row[0]:
                    continue
                atts = json.loads(row[0]) if isinstance(row[0], str) else row[0]
                for idx, (path, filename, desc) in idx_map.items():
                    if idx < len(atts):
                        if isinstance(atts[idx], str):
                            atts[idx] = {"path": atts[idx], "filename": filename, "desc": desc}
                        elif isinstance(atts[idx], dict):
                            atts[idx]["desc"] = desc
                db.execute(text("UPDATE task_comments SET attachments=:a WHERE id=:i"),
                           {"a": json.dumps(atts, ensure_ascii=False), "i": cid})
                written += 1
            db.commit()
            print(f"写库完成：{written} 条评论更新")
        finally:
            db.close()
    elif not args.apply:
        print(f"dry-run 结束：{ok} 可回填 / {fail} 失败。加 --apply 真写库")


if __name__ == "__main__":
    main()
