"""存量直答率周报批量导入服务号后台（ORS-#981 一次性脚本）。

读本地 processed/weekly_*.json 逐个 POST 到 /api/admin/dashboard/dar-reports
（同 week_date+env 幂等覆盖，可重复跑）。

用法（先配三个环境变量，同 dar_weekly._push_report）：
    DAR_PUSH_BASE=https://usp.ep-zl.com \\
    DAR_PUSH_USER=admin DAR_PUSH_PASS=*** \\
    python ai/scripts/dar_backfill_reports.py --env prod
"""
import argparse
import glob
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# 注意：不能用 dar_weekly 的模块级 DATA——它硬编码 test，--env 在 main 里才生效，
# 这里 import 时还未执行（1016 实测坑）。按 --env 自行拼目录。
from dar_weekly import DATA_ROOT, _push_env  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default=os.environ.get("DAR_ENV", "prod"))
    args = ap.parse_args()
    os.environ["DAR_ENV"] = args.env

    base, user, pwd = _push_env()   # shell 环境变量优先，其次 ai/.env
    if not (base and user and pwd):
        sys.exit("请先配 DAR_PUSH_BASE / DAR_PUSH_USER / DAR_PUSH_PASS")

    data_dir = os.path.join(DATA_ROOT, args.env, "processed")
    files = sorted(glob.glob(os.path.join(data_dir, "weekly_*.json")))
    if not files:
        sys.exit(f"未找到周报: {data_dir}/weekly_*.json")

    req = urllib.request.Request(f"{base}/api/auth/login",
                                 json.dumps({"username": user, "password": pwd}).encode(),
                                 {"Content-Type": "application/json"})
    tok = json.loads(urllib.request.urlopen(req, timeout=20).read())["access_token"]

    ok = 0
    for f in files:
        week = os.path.basename(f)[len("weekly_"):-len(".json")]
        rep = json.load(open(f, encoding="utf-8"))
        body = json.dumps({"week_date": week, "env": args.env, "payload": rep}).encode()
        req = urllib.request.Request(f"{base}/api/admin/dashboard/dar-reports", body,
                                     {"Content-Type": "application/json",
                                      "Authorization": f"Bearer {tok}"})
        try:
            urllib.request.urlopen(req, timeout=30)
            ok += 1
            print(f"  {week} ✓")
        except Exception as e:
            print(f"  {week} ✗ {e}")
    print(f"导入完成：{ok}/{len(files)}")


if __name__ == "__main__":
    main()
