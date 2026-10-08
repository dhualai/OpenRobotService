"""Generate a self-contained HTML test report from allure-results JSON files.

Usage:
    cd backend && python tests/generate_allure_report.py

Output: allure-report/index.html  (no external dependencies, no Java required)
Supports: test status, duration, error traces, and HTTP request/response attachments.
"""
import json, os, html
from datetime import datetime
from pathlib import Path

ALLURE_DIR = Path(__file__).resolve().parent.parent / "allure-results"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "allure-report"


STATUS_ICONS = {
    "passed": "\u2705", "failed": "\u274c", "broken": "\U0001f4a5",
    "skipped": "\u23ed\ufe0f", "xfailed": "\u26a0\ufe0f", "xpassed": "\u26a0\ufe0f",
}
STATUS_COLORS = {
    "passed": "#43a047", "failed": "#e53935", "broken": "#fb8c00",
    "skipped": "#9e9e9e", "xfailed": "#fb8c00", "xpassed": "#fb8c00",
}


def load_json_files(suffix):
    for f in sorted(ALLURE_DIR.glob(f"*{suffix}")):
        try:
            yield json.loads(f.read_text("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue


def resolve_attachment(source: str) -> str:
    """Read attachment content from file in allure-results directory."""
    file_path = ALLURE_DIR / source
    if file_path.exists():
        try:
            return file_path.read_text("utf-8", errors="replace")
        except Exception:
            return f"[binary file: {source}]"
    return ""


def build_report():
    results = list(load_json_files("-result.json"))
    containers = {c["uuid"]: c for c in load_json_files("-container.json")}

    total = len(results)
    passed = sum(1 for r in results if r.get("status") == "passed")
    failed = sum(1 for r in results if r.get("status") == "failed")
    broken = sum(1 for r in results if r.get("status") == "broken")
    skipped = sum(1 for r in results if r.get("status") == "skipped")
    xfailed = sum(1 for r in results if r.get("status") == "xfailed")

    rows = []
    for r in sorted(results, key=lambda x: x.get("start", 0)):
        name = r.get("name", "unknown")
        status = r.get("status", "unknown")
        duration_ms = r.get("stop", 0) - r.get("start", 0)
        trace = (r.get("statusDetails") or {}).get("trace", "")
        message = (r.get("statusDetails") or {}).get("message", "")
        full_name = r.get("fullName", "")
        short_name = full_name.split("::")[-1] if "::" in full_name else name

        icon = STATUS_ICONS.get(status, "\u2753")
        color = STATUS_COLORS.get(status, "#333")

        # Error details
        error_html = ""
        if message or trace:
            detail = html.escape(message or trace)[:2000]
            error_html = (
                f'<details style="margin-top:4px">'
                f'<summary style="cursor:pointer;font-size:12px;color:#e53935">\u00d7 \u9519\u8bef\u8be6\u60c5</summary>'
                f'<pre style="background:#f5f5f5;padding:8px;border-radius:4px;font-size:11px;margin:4px 0 0 0;max-height:200px;overflow:auto;white-space:pre-wrap">{detail}</pre>'
                f"</details>"
            )

        # Attachments (request/response data)
        attachments_html = ""
        for att in r.get("attachments") or []:
            att_name = att.get("name", "")
            att_source = att.get("source", "")
            att_type = att.get("type", "")
            content = resolve_attachment(att_source)
            if content:
                escaped = html.escape(content[:3000])
                is_json = "json" in att_type or att_name.startswith("Request") or att_name.startswith("Response")
                bg = "#1e3a5f" if is_json else "#f5f5f5"
                tc = "#e8e8e8" if is_json else "#333"
                attachments_html += (
                    f'<details style="margin-top:4px">'
                    f'<summary style="cursor:pointer;font-size:12px;color:#1565c0">{html.escape(att_name)}</summary>'
                    f'<pre style="background:{bg};color:{tc};padding:8px;border-radius:4px;font-size:11px;margin:4px 0 0 0;max-height:300px;overflow:auto;white-space:pre-wrap">{escaped}</pre>'
                    f"</details>"
                )

        rows.append(f"""<tr>
  <td style="padding:8px 10px;border-bottom:1px solid #eee;text-align:center;font-size:18px">{icon}</td>
  <td style="padding:8px 10px;border-bottom:1px solid #eee;word-break:break-all">{html.escape(short_name)}</td>
  <td style="padding:8px 10px;border-bottom:1px solid #eee;color:{color};font-weight:600">{status}</td>
  <td style="padding:8px 10px;border-bottom:1px solid #eee;text-align:right;color:#666;font-size:13px">{duration_ms}ms</td>
  <td style="padding:8px 10px;border-bottom:1px solid #eee;max-width:500px">
    {error_html}
    {attachments_html}
  </td>
</tr>""")

    summary_bars = [
        (passed, "#43a047", "\u00b7 \u901a\u8fc7"),
        (failed, "#e53935", "\u00b7 \u5931\u8d25"),
        (broken, "#fb8c00", "\u00b7 \u5f02\u5e38"),
        (skipped + xfailed, "#9e9e9e", "\u00b7 \u8df3\u8fc7/XFAIL"),
    ]
    bar_html = "".join(
        f'<div style="flex:1;background:{c};height:8px;border-radius:4px;min-width:{max(v/total*100, 1) if total else 1}%" title="{v}{l}"></div>'
        for v, c, l in summary_bars if v > 0
    )

    html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Test Report - OpenRobotService</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:#f5f5f5;color:#333}}
  .container{{max-width:1200px;margin:0 auto;padding:20px}}
  h1{{font-size:22px;margin-bottom:4px}}
  .sub{{color:#666;font-size:13px;margin-bottom:16px}}
  .stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(100px,1fr));gap:12px;margin-bottom:20px}}
  .stat-card{{background:#fff;border-radius:8px;padding:14px;text-align:center;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
  .stat-card .num{{font-size:28px;font-weight:700}}
  .stat-card .label{{font-size:12px;color:#888;margin-top:2px}}
  .bar{{display:flex;gap:2px;margin-bottom:20px;height:8px}}
  table{{width:100%;border-collapse:collapse;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
  th{{background:#37474f;color:#fff;padding:10px;text-align:left;font-size:13px;white-space:nowrap}}
  td{{padding:8px 10px;border-bottom:1px solid #eee;font-size:13px;vertical-align:top}}
  tr:hover{{background:#f8f9fa}}
  details summary{{user-select:none}}
  details summary::-webkit-details-marker{{display:none}}
  @@media(max-width:768px){{.stats{{grid-template-columns:repeat(2,1fr)}}}}
</style>
</head>
<body>
<div class="container">
  <h1>\u6d4b\u8bd5\u62a5\u544a</h1>
  <p class="sub">{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} \u00b7 {total} \u6761\u6d4b\u8bd5\u00b7 {passed}\u901a\u8fc7 / {failed}\u5931\u8d25 / {skipped + xfailed}\u8df3\u8fc7</p>
  <div class="bar">{bar_html}</div>
  <div class="stats">
    <div class="stat-card"><div class="num" style="color:#43a047">{passed}</div><div class="label">\u901a\u8fc7</div></div>
    <div class="stat-card"><div class="num" style="color:#e53935">{failed}</div><div class="label">\u5931\u8d25</div></div>
    <div class="stat-card"><div class="num" style="color:#fb8c00">{broken}</div><div class="label">\u5f02\u5e38</div></div>
    <div class="stat-card"><div class="num">{skipped + xfailed}</div><div class="label">\u8df3\u8fc7/XFAIL</div></div>
    <div class="stat-card"><div class="num">{total}</div><div class="label">\u603b\u8ba1</div></div>
  </div>
  <table>
    <thead><tr>
      <th style="width:40px;text-align:center">#</th>
      <th>\u7528\u4f8b</th>
      <th style="width:80px">\u7ed3\u679c</th>
      <th style="width:70px;text-align:right">\u8017\u65f6</th>
      <th style="width:450px">\u8be6\u60c5</th>
    </tr></thead>
    <tbody>{"".join(rows)}</tbody>
  </table>
</div>
</body></html>"""

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "index.html").write_text(html_content, encoding="utf-8")
    print(f"Report generated: {OUTPUT_DIR / 'index.html'}")
    print(f"  Total: {total} | Passed: {passed} | Failed: {failed} | Skipped: {skipped + xfailed}")


if __name__ == "__main__":
    build_report()
