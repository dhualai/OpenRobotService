# -*- coding: utf-8 -*-
"""新车型宣传 PDF → product_catalog 产品页转换（对齐现有 md 格式）。

输入：Desktop/需要加入知识库的新车型/*.pdf（双语宣传样本，排版稿）
输出：kb/company/product_catalog/{车型}.md，格式对齐现有产品页
（H1 车型名 + 来源 + 技术参数表 + 产品特点 + 适用场景）。

转换靠 LLM 重组（宣传稿排版提取后标签与值分离，规则解析不可靠），
现有产品页 XP3201.md 作为格式 few-shot 传入。
"""
import asyncio
import re
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from dotenv import load_dotenv
load_dotenv(_project_root / "ai" / ".env")

from pypdfium2 import PdfDocument

SRC = Path(r"C:/Users/PAJ26020/Desktop/需要加入知识库的新车型")
OUT = Path(r"D:/Code/OpenRobotService_Data/kb/company/product_catalog")
FORMAT_EXAMPLE = (OUT / "XP3201.md").read_text(encoding="utf-8")

# (pdf 文件名, 输出车型文件名, 备注(driver 给 LLM 的提示))
TASKS = [
    ("2、XPA152双语宣传样本（260713）.pdf", "XPA152", "参数以 PDF 样本参数表为准"),
    ("UHX-01(双语版-简易).pdf", "UHX-01", ""),
    ("XC1031(双语版-简易).pdf", "XC1031", ""),
    ("XCD0051.pdf", "XCD0051", ""),
    ("XCD062(双语版-简易).pdf", "XCD062", ""),
    ("XCD202Y（双语版-简易）.pdf", "XCD202Y", ""),
    ("XCS101U(双语版-简易)20260907.pdf", "XCS101U", ""),
    ("XFC001(双语版-简易).pdf", "XFC001", ""),
    ("XFL151E(双语版).pdf", "XFL151E", ""),
    ("XFL301-351(双语版-简易).pdf", "XFL301", "系列文档，如内含 301/351 两型号参数则拆两节或在特点中注明系列"),
    ("XFL351(双语版-简易).pdf", "XFL351", ""),
    ("XORD1-25; XORD1-05(双语版-简易).pdf", "XORD1", "一篇含 XORD1-25 与 XORD1-05 两型号，参数表分别列出"),
    ("XP1153楼层仓储搬运机器人20231201.pdf", "XP1153", "完整样本，以此为主"),
    ("XQC163(双语版-简易).pdf", "XQC163", ""),
    ("XS1201样本25-11-27（原XS1161）..pdf", "XS1201", "文件名注明原 XS1161：型号已更名 XS1201"),
    ("擎天柱样本.pdf", "XORD3", "「擎天柱」是产品别名，正式型号 XORD3（10m 高位拣选）"),
    ("高位飞仓XFC002样本(双语版).pdf", "XFC002", "有单页+样本两份，样本信息全，以此为主"),
]

_PROMPT = """你是 USP 知识库的产品页编辑。下面是一份新车型的宣传样本 PDF 提取文本（排版稿，
标签与数值可能分离、中英混排）。请整理成标准产品页 markdown。

## 输出格式（严格对齐示例）
# {车型型号} {中文名称}

> 产品系列：{系列名} | 品牌：中力数智搬马机器人
> 来源：{pdf 文件名}

## 技术参数

| 参数 | 值 |
|------|------|
| 导航方式 | ... |
| 载荷 | ... |

（参数表必须覆盖 PDF 参数表页给出的全部字段：载荷/自重/定位精度/电池/尺寸/速度等；
数值带单位；PDF 里没有的字段不要编造，直接省略该行）

## 产品特点

- （3-6 条，从宣传文案提炼）

## 适用场景

- （1-3 条）

## 规则
1. 只用 PDF 里的信息，禁止编造参数；提取文本里标签和数值是分离的两段，请按顺序配对
2. 型号不确定时以参数表页出现的型号为准
3. 直接输出 markdown，不要解释、不要代码围栏

## 格式示例（现有产品页）
{example}

## PDF 提取文本（{pdf_name}）
{content}
"""


def extract_pdf_text(path: Path, cap: int = 24000) -> str:
    pdf = PdfDocument(str(path))
    parts = []
    for i, page in enumerate(pdf):
        t = page.get_textpage().get_text_range()
        parts.append(f"[第{i+1}页]\n{t}")
    return "\n".join(parts)[:cap]


async def main():
    from ai.core.llm import get_llm_client
    llm = await get_llm_client()
    OUT.mkdir(parents=True, exist_ok=True)
    ok = fail = 0
    for i, (fn, model, note) in enumerate(TASKS, 1):
        src = SRC / fn
        out_file = OUT / f"{model}.md"
        print(f"[{i}/{len(TASKS)}] {fn} → {model}.md ...", flush=True)
        if out_file.exists():
            print("  [SKIP] 已存在")
            continue
        if not src.is_file():
            print("  [MISS] 源文件不存在")
            fail += 1
            continue
        try:
            content = extract_pdf_text(src)
        except Exception as e:
            print(f"  [FAIL] 提取失败 {e}")
            fail += 1
            continue
        note_txt = "【备注】" + note if note else ""
        prompt = (_PROMPT
                  .replace("{车型型号}", model)
                  .replace("{pdf 文件名}", fn)
                  .replace("{example}", FORMAT_EXAMPLE)
                  .replace("{content}", content + "\n\n" + note_txt))
        for attempt in range(3):
            try:
                out = await llm.chat([{"role": "user", "content": prompt}],
                                     max_tokens=2500, temperature=0.1)
                break
            except Exception as e:
                print(f"  [RETRY {attempt+1}] {str(e)[:80]}")
                await asyncio.sleep(2)
        else:
            fail += 1
            continue
        out = out.strip()
        if out.startswith("```"):
            out = re.sub(r"^```[a-z]*\n", "", out)
            out = re.sub(r"\n```$", "", out)
        if not out.startswith("# "):
            print("  [WARN] 输出不是产品页格式，供检查")
        out_file.write_text(out + "\n", encoding="utf-8")
        print(f"  [OK] {out_file.name}（{len(out)} 字）")
        ok += 1
    print(f"\n[DONE] 成功 {ok} / 失败 {fail}")


if __name__ == "__main__":
    asyncio.run(main())
