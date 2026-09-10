# -*- coding: utf-8 -*-
"""工单沉淀审核的服务器编排（AI 质量工作台「工单沉淀」页签调用）。

把 0903 定的每周三步包进工作台按钮：
  export  ssh 到生产仓库跑 review_resolutions --export → scp 回本地审核目录
  apply   校验本地 review.csv → scp 上服务器 → ssh 跑 --apply --reviewer

生产写操作只有 apply（approved 放行可检索 / rejected·test 删点+DB 终态标记）；
export 只读库、在服务器数据根写导出文件（工具自身设计行为）。

用法：
  python ai/scripts/sink_flow.py export
  python ai/scripts/sink_flow.py apply --dir export_20260903_090746 --reviewer 张三
"""
import argparse
import csv
import io
import os
import re
import subprocess
import sys
from collections import Counter

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

SSH_HOST, SSH_PORT = "usp-a@125.122.97.107", "8802"
SRV_PY = "/data/workspace/ai/bin/python"
SRV_REPO = "/data/apps/OpenRobotService"
SRV_ROOT = "/data/apps/OpenRobotService-Data/review/ticket_resolutions"
LOCAL_ROOT = r"D:/Code/OpenRobotService_Data/review/ticket_resolutions"

_DIR_RE = re.compile(r"export_\d{8}_\d{6}$")


def sh(cmd):
    print(f"$ {' '.join(cmd[:4])}{' …' if len(cmd) > 4 else ''}")
    return subprocess.run(cmd, check=True)


def _remote(cmd):
    return f"cd {SRV_REPO} && HF_HUB_OFFLINE=1 {SRV_PY} -m ai.tools.review_resolutions {cmd}"


def _patch_review_html(path):
    """旧服务器模板（导出 CSV 按钮）→ 追加一段覆盖脚本：exportCsv 改走保存到工作台。
    只在 </body> 前追加，不做字符串切片（0910 实锤切片边界错位产 JS 语法错误=白屏）；
    服务器部署新版后（页面自带 saveCsv）自然跳过。"""
    with open(path, encoding="utf-8") as fh:
        html = fh.read()
    if "saveCsv" in html:
        return False
    if "function exportCsv() {" not in html:
        return False  # 结构对不上（模板大改），保守不动
    patch = '''<script>
// 工作台本地升级（追加覆盖，不改原脚本）：导出按钮 → 保存到工作台
function buildCsv() {
  const bad = CARDS.filter(c => state[c.point_id] && state[c.point_id].verdict === "rejected"
                               && !state[c.point_id].reason);
  if (bad.length) { alert("以下驳回的卡还没选理由：\\n" + bad.map(c => "#" + c.task_id).join(", ")); return null; }
  const lines = ["point_id,task_id,title,verdict,reason,note"];
  for (const c of CARDS) {
    const s = state[c.point_id] || {};
    lines.push([c.point_id, c.task_id, c.title, s.verdict || "", s.reason || "", s.note || ""]
      .map(csvCell).join(","));
  }
  return lines.join("\\r\\n");
}
async function saveCsv() {
  const csv = buildCsv();
  if (csv === null) return;
  const dir = new URLSearchParams(location.search).get("dir") || "";
  try {
    const r = await fetch("/api/sink_save", {method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({dir, csv})});
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || ("HTTP " + r.status));
    alert("已保存到工作台（已判 " + (j.judged || 0) + "/" + (j.total || 0)
      + " 张）——回工作台点「③ 应用判定」");
  } catch (e) {
    const blob = new Blob(["\\ufeff" + csv], {type: "text/csv;charset=utf-8"});
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = "review.csv"; a.click();
    alert("接口不通（非工作台入口打开），已退回浏览器下载 review.csv");
  }
}
function exportCsv() { saveCsv(); }  // 覆盖旧导出函数：按钮 onclick 不用动
</script>
</body>'''
    html = html.replace("</body>", patch, 1)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html)
    return True


def cmd_export():
    r = subprocess.run(["ssh", "-p", SSH_PORT, SSH_HOST, _remote("--export")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(r.stdout or "", end="")
    if r.returncode != 0:
        print(r.stderr or "")
        sys.exit(f"服务器 export 失败（退出码 {r.returncode}）")
    hits = re.findall(r"export_\d{8}_\d{6}", r.stdout or "")
    if not hits:
        if "没有 review_status" in (r.stdout or ""):
            print("服务器无待审卡（pending=0）——本周无增量，无需审核")
            return
        sys.exit("服务器输出里没找到导出目录名（review_resolutions 输出格式变了？）")
    name = hits[-1]
    os.makedirs(LOCAL_ROOT, exist_ok=True)
    sh(["scp", "-P", SSH_PORT, "-r",
        f"{SSH_HOST}:{SRV_ROOT}/{name}", LOCAL_ROOT + "/"])
    html_path = os.path.join(LOCAL_ROOT, name, "review.html")
    if os.path.isfile(html_path) and _patch_review_html(html_path):
        print("（旧版审核页已本地升级：导出按钮 → 保存到工作台）")
    print(f"\n已拉回本地：{LOCAL_ROOT}/{name} → 工作台「② 打开审核页」")


def cmd_apply(dir_name, reviewer):
    if not _DIR_RE.fullmatch(dir_name or ""):
        sys.exit(f"非法目录名 {dir_name!r}（应为 export_YYYYMMDD_HHMMSS）")
    if not re.fullmatch(r"[\w\u4e00-\u9fff.-]+", reviewer or ""):
        sys.exit(f"审核人名字含特殊字符：{reviewer!r}")
    csv_path = os.path.join(LOCAL_ROOT, dir_name, "review.csv")
    if not os.path.isfile(csv_path):
        sys.exit(f"找不到 {csv_path}（先在审核页「保存标注结果」）")
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        sys.exit("review.csv 是空的")
    c = Counter((r.get("verdict") or "").strip().lower() for r in rows)
    bad = [r for r in rows if (r.get("verdict") or "").strip().lower() == "rejected"
           and not (r.get("reason") or "").strip()]
    if bad:
        sys.exit(f"{len(bad)} 张驳回未选理由（#{','.join(r.get('task_id', '?') for r in bad[:5])}…）"
                 "——回审核页补选")
    app, dele = c.get("approved", 0), c.get("rejected", 0) + c.get("test", 0)
    if not app and not dele:
        sys.exit("没有任何已判定的行（先审核）")
    print(f"将执行：放行 {app} 张｜删除 {dele} 张｜未判跳过 {c.get('', 0) + sum(v for k, v in c.items() if k not in ('', 'approved', 'rejected', 'test'))} 张")
    tmp = f"/tmp/{dir_name}_review.csv"
    sh(["scp", "-P", SSH_PORT, csv_path.replace("\\", "/"), f"{SSH_HOST}:{tmp}"])
    sh(["ssh", "-p", SSH_PORT, SSH_HOST,
        _remote(f"--apply {tmp} --reviewer {reviewer}")])
    print("\napply 完成（放行卡即刻可检索；删除断点）")


def main():
    ap = argparse.ArgumentParser(description="工单沉淀审核编排（工作台后端调用）")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--export", action="store_true", help="服务器导出待审 + 拉回本地")
    g.add_argument("--apply", action="store_true", help="本地 review.csv 回写服务器")
    ap.add_argument("--dir", default="", help="apply 的导出目录名（export_YYYYMMDD_HHMMSS）")
    ap.add_argument("--reviewer", default="manual", help="审核人（写入 reviewed_by）")
    args = ap.parse_args()
    if args.export:
        cmd_export()
    else:
        cmd_apply(args.dir, args.reviewer)


if __name__ == "__main__":
    main()
