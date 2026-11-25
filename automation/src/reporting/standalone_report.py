"""Render the offline case list and the GitHub Actions case summary."""

from __future__ import annotations

import argparse
from html import escape
from pathlib import Path

if __package__:
    from .report_data import (
        STATUS_CN, case_duration_ms, case_summary_markdown, count_statuses, load_results,
        read_object, report_overview,
    )
else:
    from report_data import (
        STATUS_CN, case_duration_ms, case_summary_markdown, count_statuses, load_results,
        read_object, report_overview,
    )


def render(results: list[dict], context: dict, *, expect_gate: bool = False) -> str:
    parts = [
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<title>测试用例清单</title><style>',
        'body{font-family:system-ui,"Microsoft YaHei",sans-serif;margin:24px;color:#24292f}',
        '.report-overview{padding:16px;border:1px solid #d1d9e0;border-radius:10px;margin-bottom:20px}',
        'h1{font-size:22px}h2{font-size:18px;margin:0 0 8px}.report-overview p{overflow-wrap:anywhere}',
        'table{border-collapse:collapse;width:100%}th,td{padding:9px 10px;text-align:left;',
        'border-bottom:1px solid #d8dee4;vertical-align:top}th{background:#f6f8fa}',
        'td.passed{color:#116329}td.failed,td.broken{color:#a40e26}',
        'small{color:#57606a}pre{font-size:12px;white-space:pre-wrap;overflow-wrap:anywhere}',
        '</style></head><body><h1>测试用例清单</h1>',
        report_overview(count_statuses(results), context, expect_gate=expect_gate),
        '<table><thead><tr><th>结果</th><th>用例</th><th>来源</th><th>耗时</th><th>详情</th>',
        '</tr></thead><tbody>',
    ]
    priority = {"failed": 0, "broken": 1, "unknown": 2, "skipped": 3, "passed": 4}
    for result in sorted(results, key=lambda case: (priority[case["status"]], case["name"])):
        status = result["status"]
        details = result.get("statusDetails")
        message = str(details.get("message") or "") if isinstance(details, dict) else ""
        full_name = str(result.get("fullName") or "")
        duration_ms = case_duration_ms(result)
        duration = f"{duration_ms} ms" if duration_ms is not None else "—"
        parts.extend([
            f'<tr><td class="{status}">{STATUS_CN[status]}</td>',
            f'<td>{escape(result["name"])}<br><small>{escape(full_name)}</small></td>',
            f'<td>{escape(str(result["source"] or "—"))}</td><td>{duration}</td>',
            f'<td><pre>{escape(message)}</pre></td></tr>' if message else '<td>—</td></tr>',
        ])
    parts.append('</tbody></table></body></html>')
    return "\n".join(parts)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--context", type=Path)
    parser.add_argument("--expect-gate", action="store_true")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--out", type=Path)
    modes.add_argument("--summary", action="store_true")
    args = parser.parse_args(argv)

    results = load_results(args.results)
    if args.summary:
        print(case_summary_markdown(results), end="")
        return

    context = read_object(args.context) if args.context else {}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(results, context, expect_gate=args.expect_gate), encoding="utf-8")
    print(f"standalone report: {args.out} ({len(results)} cases)")


if __name__ == "__main__":
    main()
