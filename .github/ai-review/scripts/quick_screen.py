#!/usr/bin/env python3
"""AI Review 第一层：规则快筛（确定性，秒级，零 LLM 成本）。

用法：
  CI：  python3 quick_screen.py            # env: REPO / PR_NUMBER / GH_TOKEN
        命中时把评论段落写到 --out-md 指定文件，并以 exit 10 表示「命中高危」。
  库内复用：from quick_screen import screen

规则与 docs/ai-review/rules.md 保持同步（本文件是执行源，文档是镜像）。
分级原则：blocker = 值得推企微打断人（DDL/配置密钥/部署链路本体）；
warning = 只落评论不吆喝。狼来了三次功能就死了，高危档宁缺毋滥。
"""
import argparse
import json
import sys

from ai_review_common import (
    fetch_pr_files, guess_line_number, is_lock_file, now_display_str,
    split_code_and_docs, strip_patch_noise,
)

EXIT_OK = 0
EXIT_HIT = 10          # 命中高危（blocker）
EXIT_ERROR = 1

# 检视器自身目录（含本脚本与测试），screen() 一律跳过
SELF_DIR = ".github/ai-review/"

# ---------- 路径规则 ----------
# (glob, level, 标签, 理由)
PATH_RULES = [
    ("backend/alembic/*.py", "blocker",
     "数据库迁移", "alembic 脚本不入远端库、由专人执行；DDL 不可自动回滚，合并前需确认影响"),
    ("backend/alembic/*/*.py", "blocker",
     "数据库迁移", "alembic 脚本不入远端库、由专人执行；DDL 不可自动回滚，合并前需确认影响"),
    ("*.env", "blocker", "环境配置",
     "环境变量文件变更可能影响所有环境行为，且存在密钥泄漏风险"),
    ("*.env.*", "blocker", "环境配置",
     "环境变量文件变更可能影响所有环境行为，且存在密钥泄漏风险"),
    ("deploy/deploy.py", "blocker", "部署链路", "部署主脚本变更直接影响线上发布行为"),
    ("deploy/notify.py", "blocker", "部署链路", "通知链路变更影响发布告警的可达性"),
    ("deploy/notify_image.py", "blocker", "部署链路", "通知链路变更影响发布告警的可达性"),
    ("backend/scripts/schema_sync.py", "blocker", "部署链路",
     "结构门禁脚本变更影响部署准入判定"),
    (".github/workflows/*", "warning", "CI/CD 工作流",
     "工作流变更影响构建/部署/门禁行为（历史曾有旧分支合并覆盖门禁的事故）"),
    ("deploy/*", "warning", "部署配套", "部署目录内其他脚本的变更需要人工过目"),
    ("*Dockerfile*", "warning", "容器镜像", "镜像构建配置变更影响运行环境"),
    ("*docker-compose*", "warning", "容器镜像", "容器编排配置变更影响运行环境"),
    ("*supervisor*.conf", "warning", "进程守护", "进程守护配置变更影响服务拉起方式"),
    ("*nginx*.conf", "warning", "网关配置", "网关配置变更影响入口路由与安全策略"),
    ("requirements*.txt", "warning", "依赖变更", "依赖引入需要确认来源与兼容性"),
    ("*/requirements*.txt", "warning", "依赖变更", "依赖引入需要确认来源与兼容性"),
    ("*/package.json", "warning", "依赖变更", "前端依赖引入需要确认来源与兼容性"),
    ("package.json", "warning", "依赖变更", "前端依赖引入需要确认来源与兼容性"),
]

# ---------- 内容规则（只检查新增行） ----------
# (regex, level, 标签, 理由)
CONTENT_RULES = [
    # 密钥字面量（引用形态 os.environ / ${VAR} / {{ }} 不算）
    (r"(?:sk|pk)-[A-Za-z0-9]{20,}", "blocker", "疑似密钥",
     "疑似 API Key 字面量，泄漏即事故；应改为环境变量/Secrets"),
    (r"AKIA[0-9A-Z]{16}", "blocker", "疑似密钥",
     "疑似 AWS AccessKey 字面量，泄漏即事故；应改为环境变量/Secrets"),
    (r"(?i)(?:password|secret|api_key|apikey|access_token)\s*[:=]\s*[\"']"
     r"(?!\$\{|\{\{)(?!\s*['\"])[^\"']{8,}", "blocker", "疑似密钥",
     "疑似硬编码口令；应改为环境变量/Secrets 引用"),
    # schema 盲区：结构门禁明确不处理删列/改类型
    (r"DROP\s+COLUMN|drop_column|remove_column", "blocker", "删列风险",
     "schema 门禁不覆盖删列且不可自动回滚，需确认字段已无读写方"),
    (r"(?i)alter\s+table[^\n]*(?:modify|change)\s+column", "blocker", "改列风险",
     "schema 门禁不覆盖改类型/改名，需确认新旧字段兼容"),
    # 高危写法
    (r"\bos\.system\(|subprocess\.[^\n]*shell\s*=\s*True|\beval\(|\bexec\(",
     "warning", "命令/动态执行",
     "存在命令注入或动态执行风险（安全红线：命令注入/反序列化）"),
    (r"(?i)[fF]?[\"'][^\n]*\b(?:SELECT|INSERT\s+INTO|UPDATE|DELETE\s+FROM)\b[^\n]*[\"']\s*(?:\.format|%|\+|f)",
     "warning", "SQL 拼接",
     "疑似字符串拼接 SQL；应改参数化查询（安全红线：SQL 注入）"),
    (r"\bpickle\.load\b|yaml\.load\(", "warning", "反序列化",
     "反序列化外部数据需指定安全加载器/具体类型（安全红线：反序列化）"),
    (r"(?i)allow_origins[^\n]*[\"']\*[\"']|Access-Control-Allow-Origin[^\n]*[\"']\*[\"']",
     "warning", "CORS 全开", "跨域全放开需确认场景（安全红线：CORS）"),
    (r"datetime\.now\(\)", "warning", "时间字段",
     "落库时间必须用 UTC（datetime.now(timezone.utc)）；本地时间会整体偏移 8 小时"
     "（历史事故 PR #333）"),
]


def screen(files):
    """对 PR 变更文件列表执行规则快筛。

    返回 dict：
      skipped: bool（纯文档/无代码变更时 True）
      blockers / warnings: [Finding]
      doc_only_files: 纯文档文件名列表
    Finding = {path, line, label, reason, source}
    """
    # 自我豁免：检视器自身目录不参与快筛——规则源码里就有 drop_column 等
    # 正则字面量、测试里有假 key 桩，不豁免必然自我命中（PR #492 实测误报）。
    # 该目录的质量由人工 review + 深度检视兜底。
    files = [f for f in files
             if not f.get("filename", "").startswith(SELF_DIR)]
    code, docs = split_code_and_docs(files)
    result = {
        "skipped": not code,
        "blockers": [],
        "warnings": [],
        "doc_only_files": [f.get("filename", "") for f in docs],
    }
    if result["skipped"]:
        return result

    seen = set()
    for f in code:
        path = f.get("filename", "")
        if is_lock_file(path):
            continue
        patch = f.get("patch") or ""
        # 路径规则
        for pattern, level, label, reason in PATH_RULES:
            if _fnmatch_relaxed(path, pattern) and (path, label) not in seen:
                seen.add((path, label))
                result[f"{level}s"].append({
                    "path": path,
                    "line": None,
                    "label": label,
                    "reason": reason,
                    "source": "规则",
                })
        # 内容规则（只看新增行）
        for added in strip_patch_noise(patch):
            for regex, level, label, reason in CONTENT_RULES:
                if _compiled(regex).search(added) and (path, label) not in seen:
                    seen.add((path, label))
                    result[f"{level}s"].append({
                        "path": path,
                        "line": guess_line_number(patch),
                        "label": label,
                        "reason": reason,
                        "source": "规则",
                        "evidence": added.strip()[:120],
                    })
    return result


_REGEX_CACHE = {}


def _compiled(regex):
    if regex not in _REGEX_CACHE:
        import re
        _REGEX_CACHE[regex] = re.compile(regex)
    return _REGEX_CACHE[regex]


def _fnmatch_relaxed(path, pattern):
    """路径匹配：pattern 不带 / 时只匹配文件名，带 / 时匹配相对路径。"""
    import fnmatch
    if "/" not in pattern:
        return fnmatch.fnmatch(path.rsplit("/", 1)[-1], pattern)
    return fnmatch.fnmatch(path, pattern)


def render_quick_markdown(result, pr_number, sha):
    """渲染快筛结果的评论段落（极简风：粗体小标题 + 小圆点条目）。"""
    lines = [
        "<!-- ors-ai-review:quick -->",
        f"🤖 **AI 规则快筛** · PR #{pr_number}"
        + (f" · `{sha[:8]}`" if sha else "")
        + f" · {now_display_str()}",
        "",
    ]
    if result["skipped"]:
        lines += ["纯文档变更，未执行快筛。"]
        return "\n".join(lines)
    if not result["blockers"] and not result["warnings"]:
        lines += ["**结论：未命中已知风险规则。**",
                  "",
                  "确定性规则仅覆盖路径与典型高危写法，建议合并前手动触发一次"
                  "「ai-review」标签进行 AI 深度检视。"]
        return "\n".join(lines)
    lines.append(f"🔴 **规则快筛命中 {len(result['blockers'])} 项高危、"
                 f"🟡 {len(result['warnings'])} 项建议关注**")
    lines.append("")
    for title, key in (("🔴 必须采纳（高危）", "blockers"),
                       ("🟡 选择采纳（建议关注）", "warnings")):
        items = result[key]
        if not items:
            continue
        lines.append(f"**{title}**")
        lines.append("")
        for it in items:
            loc = f"`{it['path']}`" + (f":{it['line']}" if it.get("line") else "")
            lines.append(f"- {loc}：{it['label']}——{it['reason']}")
        lines.append("")
    lines += ["---", "本提醒不阻塞合并 · 由人工复核决定"]
    return "\n".join(lines)


def main():
    import os
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-md", help="命中时输出评论段落到该文件")
    parser.add_argument("--out-json", help="输出机器可读结果到该文件")
    parser.add_argument("--files-json", help="跳过 API，直接读 files 列表 JSON（测试/回放）")
    parser.add_argument("--sha", default=os.environ.get("HEAD_SHA", ""))
    args = parser.parse_args()

    repo = os.environ.get("REPO", "")
    pr = os.environ.get("PR_NUMBER", "")
    token = os.environ.get("GH_TOKEN", "")

    if args.files_json:
        with open(args.files_json, encoding="utf-8-sig") as fh:
            files = json.load(fh)
        status = 200
    else:
        if not (repo and pr and token):
            print("缺少 REPO / PR_NUMBER / GH_TOKEN 环境变量", file=sys.stderr)
            return EXIT_ERROR
        status, files = fetch_pr_files(repo, pr, token)
        if status != 200:
            print(f"拉取 PR 文件失败：HTTP {status}", file=sys.stderr)
            return EXIT_ERROR

    result = screen(files)
    md = render_quick_markdown(result, pr, args.sha)

    if args.out_json:
        with open(args.out_json, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2)
    hit = bool(result["blockers"])
    if hit and args.out_md:
        with open(args.out_md, "w", encoding="utf-8") as fh:
            fh.write(md)

    if result["skipped"]:
        print("纯文档/无代码变更，快筛跳过")
    else:
        print(f"快筛完成：blocker={len(result['blockers'])} "
              f"warning={len(result['warnings'])}")
    return EXIT_HIT if hit else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
