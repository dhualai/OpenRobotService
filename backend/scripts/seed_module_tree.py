"""一次性种子脚本：把 Assigner config.yaml 的 module_tree 通过后端 API 写入 DB。

策略：调用后端已运行的进程（其 DB 连接已配置好 WSL MySQL），走 PUT /module-tree/
整体覆盖保存 —— 写 DB + 同步用户画像 + 导出 config.yaml + 通知 AI 热更新。
避开在隔离的临时 python 进程里直连 WSL MySQL（易失联）。

运行方式：python scripts/seed_module_tree.py [后端地址]  [管理员密码]
默认后端 http://localhost:8400，密码从 backend/.env 读。
"""
import sys
import json
from pathlib import Path

import requests
import yaml

BACKEND_ROOT = Path(__file__).resolve().parents[1]

# 读取 backend/.env 中的管理员账号与后端地址
def _env_backend(base: Path):
    env = {}
    p = base / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env

env = _env_backend(BACKEND_ROOT)
back_url = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8400").rstrip("/")
admin_pass = sys.argv[2] if len(sys.argv) > 2 else (env.get("ADMIN_PASSWORD") or "usp2026@EP")
admin_user = env.get("ADMIN_USERNAME") or "admin"

# --- 读取 config.yaml module_tree ---
_ASSIGNER_CONFIG = (
    BACKEND_ROOT.resolve().parents[0]
    / "ai" / "agents" / "AiDiagnosisPlatform" / "assigner" / "config" / "config.yaml"
)
with open(_ASSIGNER_CONFIG, encoding="utf-8") as f:
    cfg = yaml.safe_load(f)

tree = cfg.get("module_tree") or {}
print(f"config.yaml module_tree 产品数: {len(tree)} -> {list(tree.keys())}")
if not tree:
    raise SystemExit("config.yaml 中没有 module_tree，终止。")

# --- 登录后端拿 token ---
login = requests.post(
    f"{back_url}/api/auth/login",
    json={"username": admin_user, "password": admin_pass},
    timeout=10,
)
login.raise_for_status()
sess = login.json()
tok = sess.get("access_token") or sess.get("token")
if not tok:
    raise SystemExit(f"登录响应中无 token: {json.dumps(sess, ensure_ascii=False)[:200]}")
headers = {"Authorization": f"Bearer {tok}"}
print(f"已登录 {admin_user}")

# --- PUT 整体覆盖保存 ---
resp = requests.put(
    f"{back_url}/api/admin/module-tree/",
    json=tree,
    headers=headers,
    timeout=30,
)
print(f"PUT /module-tree/ -> {resp.status_code}")
if resp.status_code >= 400:
    raise SystemExit(f"保存失败: {resp.text[:300]}")
print("后端保存结果:", json.dumps(resp.json(), ensure_ascii=False)[:200])

# --- 回读验证 ---
tree_resp = requests.get(f"{back_url}/api/admin/module-tree/", headers=headers, timeout=10)
tree_resp.raise_for_status()
data = tree_resp.json()
for product, body in data.items():
    ifaces = body.get("interfaces", []) if isinstance(body, dict) else []
    funcs = sum(len(i.get("functions", [])) for i in ifaces)
    print(f"  {product}: {len(ifaces)} 界面 / {funcs} 功能")
