# -*- coding: utf-8 -*-
"""华睿 PDF → md 转换器（company 域 huarui 子目录入库用）。

策略：每页文本直接转 markdown 章节，图片另存到 media/（命名按页码），
避免重复入向量库（PDF 里同一个图被嵌多页就不收——以唯一性去重）。
图片路径在 md 内改写为 media 相对路径，让 kb_media 路由可服务。
"""
from pathlib import Path

from pypdfium2 import PdfDocument

SRC = Path(r"D:/Code/OpenRobotService_Data/华睿文档/华睿文档")
OUT = Path(r"D:/Code/OpenRobotService_Data/kb/company/vehicle_implementation/huarui")
MEDIA = OUT / "media"

# 优先级：主接入手册先做
FILES = [
    ("华睿科技VDA5050协议接入手册-V1.007.pdf", "华睿VDA5050协议接入手册", "接入参考"),
    ("华睿AGV货架业务对接VDA5050.pdf", "华睿AGV货架业务对接VDA5050", "业务对接"),
    ("VDA5050-V2.1.0-2025-01-1.pdf", "VDA5050协议规范V2.1.0", "协议规范"),
    ("IAP2300C系列 V1.0.0 使用手册_20220506.pdf", "IAP2300C系列使用手册", "车型手册"),
]


def convert_one(pdf_path: Path, title: str, topic: str) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    MEDIA.mkdir(parents=True, exist_ok=True)
    pdf = PdfDocument(str(pdf_path))
    seen_xrefs: set = set()
    md_parts = [f"# {title}\n\n", f"> 主题：{topic} · 来源：{pdf_path.name}\n"]
    for i, page in enumerate(pdf):
        text_page = page.get_textpage()
        text = text_page.get_text_range()
        md_parts.append(f"## 第 {i+1} 页\n\n{text}\n")
        text_page.close()
        # 注：pypdfium2 单页不暴露图片接口，图片导出留待后续（用 PyMuPDF 单独跑）。
    out = OUT / f"{pdf_path.stem}.md"
    out.write_text("\n---\n".join(md_parts), encoding="utf-8")
    return out


def main():
    n_ok =  n_fail = 0
    for fn, title, topic in FILES:
        p = SRC / fn
        if not p.is_file():
            print(f"[MISS] {fn}")
            n_fail += 1
            continue
        if (OUT / f"{p.stem}.md").is_file():
            print(f"[SKIP] {fn} 已转")
            n_ok += 1
            continue
        print(f"[GEN] {fn} ...")
        try:
            out = convert_one(p, title, topic)
            print(f"  [OK] -> {out.name}（{len(out.read_text(encoding='utf-8'))//1024} KB）")
            n_ok += 1
        except Exception as e:
            print(f"  [FAIL] {type(e).__name__}: {str(e)[:120]}")
            n_fail += 1
    print(f"\n[DONE] 成功 {n_ok} / 失败 {n_fail}")


if __name__ == "__main__":
    main()