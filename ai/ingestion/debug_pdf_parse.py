"""
诊断脚本 v3：统计 + 缺失分析
用法：python ai/ingestion/debug_pdf_parse.py
"""
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))

from dotenv import load_dotenv
load_dotenv(_project_root / "ai" / ".env")

from ai.config import get_docs_dir
from ai.ingestion.parsers.cheduan_pdf import CheduanPDFIngester

TARGETS = ["1031", "12351", "12361", "6311", "6601"]

print("=" * 60)
print("车端错误码 PDF 解析诊断 v3")
print("=" * 60)

entries = CheduanPDFIngester().parse()
all_codes = sorted(set(e.code for e in entries), key=lambda x: int(x))

print(f"\n解析到: {len(entries)} 个条目, {len(all_codes)} 个唯一错误码")

# 按位数
len_3 = [c for c in all_codes if len(c) == 3]
len_4 = [c for c in all_codes if len(c) == 4]
len_5 = [c for c in all_codes if len(c) == 5]
print(f"  3位: {len(len_3)}")
print(f"  4位: {len(len_4)}")
print(f"  5位: {len(len_5)}")

# 按首位
by_prefix = {}
for c in all_codes:
    p = c[0]
    by_prefix.setdefault(p, []).append(c)
for p in sorted(by_prefix):
    codes = sorted(by_prefix[p], key=int)
    print(f"  {p}xxx: {len(codes)} 个, {codes[0]}~{codes[-1]}")
    if len(codes) <= 35:
        print(f"        {codes}")

# 目标
print(f"\n{'=' * 60}")
print("目标错误码:")
print(f"{'=' * 60}")
for tc in TARGETS:
    found = tc in all_codes
    print(f"  {'[OK]' if found else '[!!]'} {tc}", end="")
    if found:
        e = next(e for e in entries if e.code == tc)
        print(f" 类别={e.category} 等级={e.level} 描述={e.description_cn[:80]}")
    else:
        print()
