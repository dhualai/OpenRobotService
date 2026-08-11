"""Allure report metadata generation (environment / executor / categories).

Called automatically from automation/conftest.py before each test run so
that generated reports carry standard enterprise metadata:
- environment.properties  -> Environments tab
- executor.json           -> Executor info (local vs CI)
- categories.json         -> Failure classification rules
"""

import json
import os
import platform
import sys
from datetime import datetime
from pathlib import Path

from automation.config import load_config

_CATEGORIES = [
    {
        "name": "认证失败",
        "matchedStatuses": ["failed", "broken"],
        "messageRegex": r"401|403|Authentication|Invalid credentials|未认证|无权限",
    },
    {
        "name": "资源不存在",
        "matchedStatuses": ["failed", "broken"],
        "messageRegex": r"404|not found|不存在",
    },
    {
        "name": "参数校验失败",
        "matchedStatuses": ["failed", "broken"],
        "messageRegex": r"422|field required|参数校验",
    },
    {
        "name": "状态冲突",
        "matchedStatuses": ["failed", "broken"],
        "messageRegex": r"400|Invalid state|非法状态",
    },
    {
        "name": "连接/超时错误",
        "matchedStatuses": ["broken"],
        "messageRegex": r"Connection error|timed out|Timeout",
    },
    {
        "name": "产品缺陷",
        "matchedStatuses": ["failed"],
    },
    {
        "name": "测试代码错误",
        "matchedStatuses": ["broken"],
    },
]


def _write_environment(allure_dir: Path) -> None:
    env = os.getenv("AUTOMATION_ENV", "local")
    mode = "mock" if os.getenv("USE_MOCK", "1") != "0" else "real"
    base_url = ""
    try:
        base_url = load_config().api.base_url
    except Exception:
        pass
    lines = [
        f"Automation.Env={env}",
        f"Backend.Mode={mode}",
        f"Backend.BaseUrl={base_url}",
        f"Python={sys.version.split()[0]}",
        f"Platform={platform.system()} {platform.release()}",
        f"RunTime={datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
    ]
    (allure_dir / "environment.properties").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_executor(allure_dir: Path) -> None:
    if os.getenv("GITHUB_ACTIONS"):
        repo = os.getenv("GITHUB_REPOSITORY", "")
        run_id = os.getenv("GITHUB_RUN_ID", "")
        data = {
            "name": "GitHub Actions",
            "type": "github",
            "url": f"https://github.com/{repo}/actions/runs/{run_id}",
        }
    else:
        data = {"name": "Local", "type": "local"}
    (allure_dir / "executor.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _write_categories(allure_dir: Path) -> None:
    (allure_dir / "categories.json").write_text(
        json.dumps(_CATEGORIES, ensure_ascii=False, indent=2), encoding="utf-8")


def write_allure_metadata(allure_dir: Path) -> None:
    """Write standard metadata files into the allure-results directory."""
    try:
        allure_dir.mkdir(parents=True, exist_ok=True)
        _write_environment(allure_dir)
        _write_executor(allure_dir)
        _write_categories(allure_dir)
    except Exception as e:  # metadata must never break the test run
        print(f"  [allure] metadata write skipped: {e}")
