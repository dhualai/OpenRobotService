# -*- coding: utf-8 -*-
"""远程 qdrant 只读连接基建：隧道 + 指针切换 + env 覆盖（测试/生产同实例不同指针）。

用法（必须在首次 import pipeline 之前进入 with，env 覆盖只对未读过的配置生效）：

    from dar_qdrant import remote_qdrant
    with remote_qdrant("prod"):          # 或 "test"：测试服务正在用的知识库
        from ai.agents.AiDiagnosisPlatform.pipeline import ...
        ...  # 此范围内本地代码的检索全部走远程知识库

- 隧道：本地 16333 → 服务器 localhost:6333（测试/生产 qdrant 同一实例）；
  已开直接复用，没开自动起 ssh -N 后台进程（Windows 无 -f，Popen 驻留）
- 指针：ssh 拉 {服务}/ai/kb/active_{domain}_collection.txt 三域
  （company/industry/team），备份本地值 → 写远程值 → with 结束自动恢复
  （原本不存在的文件恢复为删除）
- dispatch 不碰（不归知识库口径，本地缺指针维持现状即该域不检索）
"""
import os
import subprocess
import time
import urllib.request
from contextlib import contextmanager

SSH_HOST = "usp-a@125.122.97.107"
SSH_PORT = "8802"
REMOTE_AI_ROOTS = {
    "prod": "/data/apps/OpenRobotService/ai",
    "test": "/data/apps/TestOpenRobotService/ai",
}
DOMAINS = ("company", "industry", "team")
TUNNEL_PORT = 16333

_LOCAL_KB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "kb")
_ENV_KEYS = ("QDRANT_LOCAL_PATH", "QDRANT_HOST", "QDRANT_PORT")


def _tunnel_alive():
    try:
        urllib.request.urlopen(f"http://localhost:{TUNNEL_PORT}/collections", timeout=2)
        return True
    except Exception:
        return False


_proc = None


def ensure_tunnel():
    """隧道通则复用；不通则起 ssh -N 后台进程。返回 True，失败抛异常。

    Windows 的 ssh 没有 -f（后台化后不关闭继承的输出句柄，捕获式调用会挂死
    直到超时把隧道杀掉），所以用 Popen 驻留 + 本地端口轮询探测。"""
    global _proc
    if _tunnel_alive():
        return True
    if not _proc or _proc.poll() is not None:
        _proc = subprocess.Popen(
            ["ssh", "-N", "-L", f"{TUNNEL_PORT}:localhost:6333",
             "-p", SSH_PORT, "-o", "ExitOnForwardFailure=yes",
             "-o", "ConnectTimeout=10", "-o", "BatchMode=yes",
             "-o", "ServerAliveInterval=30", SSH_HOST],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
    for _ in range(30):  # 最多等 15s
        if _tunnel_alive():
            return True
        if _proc.poll() is not None:  # ssh 已自己退出（免密/网络问题）
            break
        time.sleep(0.5)
    raise RuntimeError(f"建 ssh 隧道失败（检查免密 ssh {SSH_HOST}:{SSH_PORT}）")


def remote_pointers(which: str):
    """拉指定环境（test/prod）三域指针值 {domain: collection}；全部拉不到抛异常。"""
    root = REMOTE_AI_ROOTS[which]
    # echo 空行打头：指针文件行尾无换行，cat 值与下一个标记会黏在同一行
    script = "; ".join(
        f'echo; echo =={d}; cat {root}/kb/active_{d}_collection.txt 2>/dev/null'
        for d in DOMAINS)
    r = subprocess.run(["ssh", "-p", SSH_PORT, SSH_HOST, script],
                       capture_output=True, timeout=40)
    if r.returncode != 0:
        raise RuntimeError(f"拉 {which} 指针失败: " + r.stderr.decode("utf-8", "replace")[:200])
    text = r.stdout.decode("utf-8", "replace")
    out, cur = {}, None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("==") and line[2:] in DOMAINS:
            cur = line[2:]
        elif cur and not cur in out:
            out[cur] = line  # 指针值独占一行（echo 空行已保证与标记分离）
    if not out:
        raise RuntimeError(f"{which} 指针全空")
    return out


def _bak_path(domain_file: str) -> str:
    return domain_file + ".dar_bak"


def _restore_leftover():
    """上次 remote_qdrant 被 kill（stop 按钮/进程树杀）时 finally 不执行，
    指针文件残留远程值 → 之后跑 local 检索全查不存在的集合（0909 实锤）。
    进入时发现 .bak 还在 = 上次没退干净，先恢复本地原值再继续。"""
    for d in DOMAINS:
        p = os.path.join(_LOCAL_KB, f"active_{d}_collection.txt")
        bak = _bak_path(p)
        if not os.path.exists(bak):
            continue
        val = open(bak, encoding="utf-8").read().strip()
        if val:
            open(p, "w", encoding="utf-8").write(val + "\n")
            print(f"[dar_qdrant] 检测到上次异常退出残留，{d} 指针已恢复: {val}")
        elif os.path.exists(p):
            os.remove(p)
            print(f"[dar_qdrant] 检测到上次异常退出残留，{d} 指针已恢复为删除")
        os.remove(bak)


@contextmanager
def remote_qdrant(which: str):
    """进入远程检索模式（隧道+指针+env），退出自动恢复指针与 env。which=test|prod。"""
    if which not in REMOTE_AI_ROOTS:
        raise ValueError("which 取值 test|prod")
    ensure_tunnel()
    _restore_leftover()
    ptr = remote_pointers(which)
    backs, old_env = {}, {k: os.environ.get(k) for k in _ENV_KEYS}
    try:
        for d, col in ptr.items():
            p = os.path.join(_LOCAL_KB, f"active_{d}_collection.txt")
            backs[p] = open(p, encoding="utf-8").read() if os.path.exists(p) else None
            open(p, "w", encoding="utf-8").write(col + "\n")
            # 备份旁挂：正常退出删除；被 kill 后下次进入据此自愈恢复
            open(_bak_path(p), "w", encoding="utf-8").write(backs[p] or "")
        os.environ["QDRANT_LOCAL_PATH"] = ""
        os.environ["QDRANT_HOST"] = "localhost"
        os.environ["QDRANT_PORT"] = str(TUNNEL_PORT)
        yield ptr
    finally:
        for p, old in backs.items():
            if old is None:
                if os.path.exists(p):
                    os.remove(p)
            else:
                open(p, "w", encoding="utf-8").write(old)
            if os.path.exists(_bak_path(p)):
                os.remove(_bak_path(p))
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


if __name__ == "__main__":
    # 自检：python ai/scripts/dar_qdrant.py → 打印两环境指针与连通性
    for w in ("test", "prod"):
        with remote_qdrant(w) as ptr:
            print(f"{w} 指针:", ptr)
    print("collections:", urllib.request.urlopen(
        f"http://localhost:{TUNNEL_PORT}/collections", timeout=5).read()[:200])
    print("指针已恢复")
