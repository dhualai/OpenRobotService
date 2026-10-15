"""跑回归信号并与基线比。**不连库、不调 LLM**。

用法：
    # 第一次：先抽用例，再存基线
    python -m ai.agents.AiTaskPlatform.regression.build_cases --limit 20
    python -m ai.agents.AiTaskPlatform.regression.run --save-baseline

    # 以后每次改动后：跑一遍，退化就非零退出（可当 CI 卡口）
    python -m ai.agents.AiTaskPlatform.regression.run

退出码：0 = 没退化 / 1 = 有退化 / 2 = 用法或数据问题。

## 它量什么、不量什么（别读错）

量的是**「有没有踩这几类明显的错」**：人名是不是凭空来的、引用的工单号是不是真的、
是不是答不上来就放弃。全部确定性、可复现。

**不量「答得对不对」** —— 那需要标准答案，而讨论问答的标准取决于用户问了什么
（见 issues.md #22 的结论：那把尺子是错的工具）。所以：
  - 指标降下去 ≠ 答得更好，只说明**没踩这几个坑**；
  - 想回答「变好了吗」，得另加人工抽检做锚点，这一步这里没做。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ai.agents.AiTaskPlatform.regression.signals import aggregate, compare, score_case

_DIR = Path(__file__).resolve().parent
_DEFAULT_DATASET = _DIR / "datasets" / "real_cases.json"
_DEFAULT_BASELINE = _DIR / "datasets" / "baseline.json"


def load_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def evaluate(data: dict) -> tuple[list[dict], dict]:
    names = data.get("known_names") or []
    ids = data.get("known_ticket_ids") or []
    rows = [score_case(c, names, ids) for c in (data.get("cases") or [])]
    return rows, aggregate(rows)


def format_report(agg: dict, cmp_result: dict, rows: list[dict]) -> str:
    lines = [
        f"用例 {agg['cases']} 条",
        f"  S1 人名核对   {agg['name_bad']}/{agg['name_checked']} 条有人名凭空出现"
        f"（分母=材料非空且提到了人名的用例）",
        f"  S2 引用真实性 {agg['cited_bad']}/{agg['cited_checked']} 条引用了不存在的工单号",
        f"  S3 放弃式收尾 {agg['giveup']}/{agg['cases']} 条",
    ]
    if not cmp_result.get("has_baseline"):
        lines.append("（没有基线 —— 用 --save-baseline 存一份，以后才有「退化」可比）")
        return "\n".join(lines)
    regs = cmp_result["regressions"]
    if not regs:
        lines.append("✅ 与基线相比没有退化")
        return "\n".join(lines)
    lines.append("❌ 与基线相比有退化：")
    for r in regs:
        lines.append(f"   - {r['metric']}: {r['baseline']} → {r['current']}")
    # 退化的用例点名，方便直接看原文
    bad = [r for r in rows if r.get("names_unsupported") or r.get("citations_missing")]
    for r in bad[:10]:
        detail = []
        if r.get("names_unsupported"):
            detail.append(f"人名凭空出现 {r['names_unsupported']}")
        if r.get("citations_missing"):
            detail.append(f"引用了不存在的工单 {r['citations_missing']}")
        lines.append(f"   · {r.get('case_id')}: {'；'.join(detail)}")
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser(description="回归信号（不连库、不调 LLM）")
    p.add_argument("--dataset", default=str(_DEFAULT_DATASET))
    p.add_argument("--baseline", default=str(_DEFAULT_BASELINE))
    p.add_argument("--save-baseline", action="store_true",
                   help="把本次结果存为基线（会覆盖旧基线）")
    p.add_argument("--json", dest="json_out", default=None, help="逐条结果落盘")
    args = p.parse_args()

    ds = Path(args.dataset)
    if not ds.exists():
        print(f"没有用例文件：{ds}\n先跑：python -m ai.agents.AiTaskPlatform.regression.build_cases --limit 20")
        return 2
    data = load_json(ds)
    rows, agg = evaluate(data)

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps({"aggregate": agg, "rows": rows}, ensure_ascii=False, indent=1),
            encoding="utf-8")

    if args.save_baseline:
        Path(args.baseline).write_text(
            json.dumps({"aggregate": agg, "saved_at": data.get("generated_at")},
                       ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"已存基线 → {args.baseline}")
        print(format_report(agg, {"has_baseline": False}, rows))
        return 0

    baseline = None
    bp = Path(args.baseline)
    if bp.exists():
        baseline = load_json(bp).get("aggregate")
    cmp_result = compare(baseline, agg)
    print(format_report(agg, cmp_result, rows))
    return 1 if cmp_result.get("regressions") else 0


if __name__ == "__main__":
    raise SystemExit(main())
