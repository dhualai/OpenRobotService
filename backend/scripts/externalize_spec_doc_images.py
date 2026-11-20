"""问题文档（task_spec_doc）内嵌 base64 图片外置脚本（幂等，可重复运行）。

背景：md 正文内嵌 base64 图片会形成 100KB+ 超长单行；前端问题文档编辑器
（@uiw/react-md-editor）挂载时用 Prism/refractor 做语法高亮，其 setext 标题
正则在「超长单行 + == 结尾」形态下呈 O(n²) 灾难性回溯，可阻塞主线程数十秒
（工单 836 实测 81s，页面无法刷新/点击任何元素）。

本脚本把存量 task_spec_doc.content 中体积超阈值（8KB）的 base64 内联图片上传
MinIO，替换为 /api/tasks/files 代理 URL —— 与「保存接口自动外置」「Word 解析
内嵌图片外置」共用同一套逻辑（app.utils.spec_doc_parser.externalize_inline_images）。
已治理的行自动跳过，可安全重跑。

用法（在 backend/ 目录下用项目 venv）：
    python scripts/externalize_spec_doc_images.py --dry-run        # 预览将处理哪些工单
    python scripts/externalize_spec_doc_images.py --task-id 836    # 只处理指定工单
    python scripts/externalize_spec_doc_images.py --all --yes      # 全量执行

连接参数默认同 app/core/config.py 的 DB_CONFIG（root/123456@127.0.0.1:3306/helpdesk），
可用 DATABASE_URL 环境变量覆盖：mysql+pymysql://user:pass@host:port/db
"""
import argparse
import os
import re
import sys

import pymysql

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.utils.spec_doc_image_store import upload_inline_image  # noqa: E402
from app.utils.spec_doc_parser import externalize_inline_images  # noqa: E402


def _db_config() -> dict:
    """与 app/core/config.py DB_CONFIG 保持一致；DATABASE_URL 可覆盖。"""
    url = os.environ.get("DATABASE_URL")
    if url:
        m = re.match(r"mysql\+pymysql://([^:]+):([^@]+)@([^:]+):(\d+)/([^?]+)", url)
        if m:
            return {
                "user": m.group(1), "password": m.group(2),
                "host": m.group(3), "port": int(m.group(4)),
                "database": m.group(5),
            }
    return {"user": "root", "password": "123456", "host": "127.0.0.1",
            "port": 3306, "database": "helpdesk"}


def _count_inline_images(md: str) -> int:
    """统计正文里的 data:image 内联图片数量。"""
    return len(re.findall(r"data:image/", md or ""))


def main() -> int:
    parser = argparse.ArgumentParser(description="问题文档内嵌 base64 图片外置（MinIO）")
    parser.add_argument("--task-id", type=int, help="只处理指定工单")
    parser.add_argument("--all", action="store_true", help="处理全部含内嵌图片的文档")
    parser.add_argument("--dry-run", action="store_true", help="只预览，不写库")
    parser.add_argument("--yes", action="store_true", help="确认写入（非 dry-run 时必填）")
    parser.add_argument("--limit", type=int, default=0, help="最多处理 N 行（0=不限）")
    args = parser.parse_args()

    if not args.task_id and not args.all:
        parser.error("请指定 --task-id <id> 或 --all")
    if not args.dry_run and not args.yes:
        parser.error("真实写入需显式 --yes（建议先用 --dry-run 预览）")

    conn = pymysql.connect(
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        **_db_config(),
    )
    try:
        sql = (
            "SELECT task_id, CHAR_LENGTH(content) AS len FROM task_spec_doc "
            "WHERE content LIKE %s"
        )
        params: list = ["%data:image%"]
        if args.task_id:
            sql += " AND task_id = %s"
            params.append(args.task_id)
        sql += " ORDER BY task_id"
        if args.limit and args.limit > 0:
            sql += " LIMIT %s"
            params.append(int(args.limit))

        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

        if not rows:
            print("没有含内嵌 base64 图片的问题文档，无需治理。")
            return 0

        print(f"待检查文档 {len(rows)} 篇：" + ", ".join(f"#{r['task_id']}" for r in rows))
        total_replaced = 0
        total_failed_rows: list[int] = []

        for row in rows:
            task_id = int(row["task_id"])
            with conn.cursor() as cur:
                cur.execute("SELECT content FROM task_spec_doc WHERE task_id = %s", (task_id,))
                fetched = cur.fetchone()
            content = (fetched or {}).get("content") or ""
            before_count = _count_inline_images(content)

            new_content, replaced = externalize_inline_images(content, upload_inline_image)

            if replaced == 0:
                print(f"[skip] 工单 {task_id}：内嵌图 {before_count} 张，"
                      f"均未达外置阈值或上传失败，保持原样")
                total_failed_rows.append(task_id)
                continue

            if args.dry_run:
                print(f"[dry-run] 工单 {task_id}：将外置 {replaced}/{before_count} 张，"
                      f"正文 {len(content)} → {len(new_content)} 字符")
                total_replaced += replaced
                continue

            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE task_spec_doc SET content = %s WHERE task_id = %s",
                    (new_content, task_id),
                )
            conn.commit()
            total_replaced += replaced
            print(f"[done] 工单 {task_id}：外置 {replaced}/{before_count} 张，"
                  f"正文 {len(content)} → {len(new_content)} 字符")

        print("=" * 60)
        if args.dry_run:
            print(f"预览完成：共可外置 {total_replaced} 张内嵌图片（未写库）")
        else:
            print(f"执行完成：共外置 {total_replaced} 张内嵌图片")
        if total_failed_rows:
            print(f"未处理/部分未外置的工单：{total_failed_rows}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
