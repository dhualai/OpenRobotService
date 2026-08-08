"""AI test generation pipeline orchestrator (P0).

Pipeline stages (strict order):
    1. analyze   — spec -> analysis.md
    2. cases     — analysis -> cases.json
    3. script    — cases + spec -> test_gen.py
    4. gate      — LLM review + fix loop (max 2 rounds)

Every stage runs a structural gate; a stage that fails its gate is
recorded as failed and the pipeline continues (degrade-and-report),
never blocking the caller.

LLM client is injectable for testing; defaults to LLMJudgeClient.

Usage:
    python -m automation.ci_ai_gen.run_pipeline \
        --spec-dir test-gen/spec --out-dir test-gen --run-id <id>
"""

import argparse
import json
import py_compile
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from automation.ci_ai_gen import gates
from automation.src.ai_metrics.llm_judge import LLMJudgeClient


@dataclass
class PipelineConfig:
    spec_dir: Path
    out_dir: Path
    run_id: str
    prompts_dir: Path = field(default_factory=lambda: Path(__file__).parent / "prompts")
    max_fix_rounds: int = 2
    verify_runtime: bool = True
    base_url: str = "http://localhost:8000"
    prd_path: Optional[Path] = None
    archive_dir: Optional[Path] = None
    max_tokens: int = 8192
    case_batch_size: int = 5
    script_batch_size: int = 25


class Pipeline:
    def __init__(self, config: PipelineConfig, llm: Optional[LLMJudgeClient] = None):
        self.config = config
        self.llm = llm or LLMJudgeClient.from_env()
        self.results: Dict[str, Dict[str, Any]] = {}

    # ---------- helpers ----------

    def _prompt(self, name: str) -> str:
        return (self.config.prompts_dir / f"{name}.md").read_text(encoding="utf-8")

    async def _call(self, prompt_name: str, user_content: str) -> str:
        return await self.llm.complete(
            self._prompt(prompt_name), user_content,
            max_tokens=self.config.max_tokens,
        )

    def _stage(self, name: str, ok: bool, issues: List[str], paths: Optional[List[str]] = None) -> None:
        self.results[name] = {"passed": ok, "issues": issues, "paths": paths or []}

    def _write(self, rel: str, content: str) -> Path:
        path = self.config.out_dir / self.config.run_id / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    # ---------- spec loading ----------

    def load_spec(self) -> dict:
        spec = json.loads((self.config.spec_dir / "openapi.json").read_text(encoding="utf-8"))
        endpoints = json.loads((self.config.spec_dir / "endpoints.json").read_text(encoding="utf-8"))
        self._spec = spec
        self._endpoints = endpoints.get("endpoints", [])
        return spec

    def _endpoint_brief(self) -> str:
        lines = []
        for ep in self._endpoints:
            params = ",".join(f"{p['name']}" for p in ep.get("params", [])) or "-"
            body = ep.get("request_body") or {}
            fields = ",".join(body.get("fields", [])) or "-"
            lines.append(
                f"{ep['method']} {ep['path']} 参数:{params} "
                f"请求体字段:{fields} 响应:{ep.get('responses')}"
            )
        return "\n".join(lines)

    def _load_prd(self) -> str:
        staged = self.config.spec_dir / "prd.md"
        if staged.exists():
            return staged.read_text(encoding="utf-8")
        if not self.config.prd_path:
            return ""
        path = self.config.prd_path
        if not path.exists():
            path = path.with_suffix(path.suffix + ".md")
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    # ---------- stages ----------

    async def stage_analyze(self) -> None:
        brief = self._endpoint_brief()
        prd = self._load_prd()
        diff = (self.config.spec_dir / "changes.txt").read_text(encoding="utf-8") \
            if (self.config.spec_dir / "changes.txt").exists() else "（无）"
        if prd:
            user = f"## 产品需求文档（PRD）\n{prd}\n\n## 接口清单（交叉校验）\n{brief}\n\n## 本次变更摘要\n{diff}"
        else:
            user = f"## 接口清单\n{brief}\n\n## 本次变更摘要\n{diff}"
        try:
            text = await self._call("analyzer", user)
        except Exception as e:  # pragma: no cover
            self._stage("analyze", False, [f"llm call failed: {e}"])
            return
        path = self._write("analysis.md", text)
        issues = gates.check_analysis(text, prd_mode=bool(prd))
        if prd:
            self._req_ids = gates.extract_req_ids(text)
            if not self._req_ids:
                issues.append("PRD 模式：分析文档未产出 REQ 功能点编号")
        else:
            self._req_ids = []
        self._stage("analyze", not issues, issues, [str(path)])

    async def stage_cases(self) -> None:
        analysis_path = self.config.out_dir / self.config.run_id / "analysis.md"
        analysis = analysis_path.read_text(encoding="utf-8") if analysis_path.exists() else "（分析失败，基于接口清单生成）"
        req_ids = getattr(self, "_req_ids", [])

        # Split feature points into batches so the LLM output never hits the
        # max_tokens ceiling; results are merged and gated afterwards.
        if req_ids:
            batches = [req_ids[i:i + self.config.case_batch_size]
                       for i in range(0, len(req_ids), self.config.case_batch_size)]
        else:
            batches = [[]]

        merged: List[dict] = []
        all_issues: List[str] = []
        for batch in batches:
            scope = "、".join(batch) if batch else "（全部接口）"
            user = (
                f"## 接口清单\n{self._endpoint_brief()}\n\n"
                f"## 需求分析\n{analysis}\n\n"
                f"## 本批功能点\n仅生成以下 REQ 的用例，不得生成其他 REQ 的用例：{scope}"
            )
            try:
                text = await self._call("case_gen", user)
            except Exception as e:  # pragma: no cover
                all_issues.append(f"batch[{scope[:30]}] llm call failed: {e}")
                continue
            issues, cases = gates.check_cases(text)
            if issues:
                all_issues.append(f"batch[{scope[:30]}]: {'; '.join(issues[:3])}")
                continue
            merged.extend(cases)

        if req_ids:
            all_issues.extend(gates.check_cases_req_coverage(merged, req_ids))

        # Global renumber: LLM batches may reuse TC ids across batches;
        # rename collisions deterministically so downstream ids stay unique.
        seen_ids: set = set()
        next_seq = 1
        for case in merged:
            cid = case.get("id")
            if cid in seen_ids:
                while f"TC{next_seq:03d}" in seen_ids:
                    next_seq += 1
                case["id"] = f"TC{next_seq:03d}"
                next_seq += 1
            seen_ids.add(case.get("id"))

        # Write a clean merged JSON (not the raw LLM text) so downstream
        # stages and the archive exporter always get parseable input.
        path = self._write("cases.json", json.dumps(merged, ensure_ascii=False, indent=2))
        self._stage("cases", not all_issues, all_issues, [str(path)])
        self._cases = merged

    async def stage_script(self) -> None:
        cases = self._cases or []
        cases = [c for c in cases if c.get("method") and c.get("path")]
        if not cases:
            self._stage("script", False, ["no parseable cases to generate scripts from"])
            self._script_text = None
            return
        batch_size = self.config.script_batch_size
        batches = [cases[i:i + batch_size] for i in range(0, len(cases), batch_size)]
        paths: List[str] = []
        all_issues: List[str] = []
        for idx, batch in enumerate(batches, start=1):
            user = (
                f"## 接口规格摘要\n{self._endpoint_brief()}\n\n"
                f"## 本批测试用例\n{json.dumps(batch, ensure_ascii=False, indent=2)}\n\n"
                f"## 执行环境\n测试服务地址: {self.config.base_url}"
            )
            try:
                text = await self._call("script_gen", user)
            except Exception as e:  # pragma: no cover
                all_issues.append(f"batch {idx} llm call failed: {e}")
                continue
            text = gates.strip_code_fence(text)
            fname = "test_gen.py" if len(batches) == 1 else f"test_gen_{idx:02d}.py"
            path = self._write(fname, text)
            issues = self._check_script_runtime(path, text)
            if issues:
                all_issues.append(f"{fname}: {'; '.join(issues[:3])}")
            else:
                paths.append(fname)
        self._stage("script", not all_issues, all_issues, paths)
        self._script_files = paths

    def _check_script_runtime(self, path: Path, text: str) -> List[str]:
        issues = gates.check_script(text, self._spec)
        if issues:
            return issues
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as e:
            return [f"script fails to compile: {e}"]
        if self.config.verify_runtime:
            try:
                import subprocess

                proc = subprocess.run(
                    [sys.executable, "-m", "pytest", str(path), "--collect-only", "-q"],
                    capture_output=True, text=True, timeout=120,
                )
                if proc.returncode != 0:
                    return [f"pytest collection failed: {proc.stderr[-500:]}"]
            except Exception as e:
                return [f"runtime verification error: {e}"]
        return []

    async def stage_gate(self) -> None:
        files = getattr(self, "_script_files", None)
        if not files:
            self._stage("gate", False, ["script stage produced no files"])
            return
        all_issues: List[str] = []
        for fname in files:
            path = self.config.out_dir / self.config.run_id / fname
            issues = await self._gate_one(path, path.read_text(encoding="utf-8"))
            if issues:
                all_issues.append(f"{fname}: {'; '.join(issues)}")
        self._stage("gate", not all_issues, all_issues)
    async def _gate_one(self, path: Path, script_text: str) -> List[str]:
        """Review one script file with fix loop. Returns final issues ([] = pass)."""
        user = (
            f"## 接口规格摘要\n{self._endpoint_brief()}\n\n"
            f"## 待审阅脚本\n```python\n{script_text}\n```"
        )
        accumulated: List[str] = []
        for attempt in range(self.config.max_fix_rounds + 1):
            try:
                raw = await self._call("gate", user)
                self._write(f"{path.stem}.gate_raw.txt", raw)
                verdict = self._parse_gate(raw)
            except Exception as e:  # pragma: no cover
                return [f"gate llm call failed: {e}"]
            if verdict is None:
                return [f"gate output unparsable: {raw[:200]}"]
            if verdict["passed"]:
                return []
            accumulated = verdict["issues"]
            if attempt >= self.config.max_fix_rounds:
                return accumulated
            fix_prompt = (
                f"## 审阅问题\n{json.dumps(verdict['issues'], ensure_ascii=False)}\n\n"
                f"## 修复要求\n请修复以下问题后重新输出完整脚本（仍只输出代码）。\n\n"
                f"## 接口规格摘要\n{self._endpoint_brief()}\n\n"
                f"## 当前脚本\n```python\n{script_text}\n```"
            )
            try:
                text = await self._call("script_gen", fix_prompt)
            except Exception as e:  # pragma: no cover
                return [f"fix llm call failed: {e}"]
            text = gates.strip_code_fence(text)
            issues = self._check_script_runtime(path, text)
            script_text = text
            path.write_text(text, encoding="utf-8")
            if issues:
                # Broken fix: keep looping for remaining rounds.
                accumulated = issues
                continue

    @staticmethod
    def _parse_gate(raw: str) -> Optional[Dict[str, Any]]:
        import re as _re

        text = gates.strip_code_fence(raw)
        m = _re.search(r"\{[\s\S]*\}", text)
        if not m:
            return None
        blob = m.group()
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            try:
                data = json.loads(gates._strip_json_comments(blob))
            except (json.JSONDecodeError, Exception):
                return None
        if "passed" not in data:
            return None
        issues = data.get("issues", [])
        if not isinstance(issues, list):
            return None
        normalized = []
        for i in issues:
            if isinstance(i, dict):
                detail = i.get("detail") or i.get("issue") or str(i)
                normalized.append(detail)
            else:
                normalized.append(str(i))
        return {"passed": bool(data["passed"]), "issues": normalized}

    # ---------- orchestration ----------

    async def run(self) -> Dict[str, Any]:
        self.load_spec()
        self._cases = None
        self._script_files = []
        self._req_ids = []
        await self.stage_analyze()
        await self.stage_cases()
        await self.stage_script()
        await self.stage_gate()
        self._write_summary()
        self._archive()
        return self.results

    def _archive(self) -> None:
        """Copy run artifacts into the archive dir + export cases to Excel.

        Archive layout: {archive_dir}/{run_id}/{analysis.md, cases.json,
        cases.xlsx, test_gen.py, summary.md}.
        """
        if not self.config.archive_dir:
            return
        src = self.config.out_dir / self.config.run_id
        if not src.exists():
            return
        dest = self.config.archive_dir / self.config.run_id
        dest.mkdir(parents=True, exist_ok=True)
        import shutil

        for name in ("analysis.md", "cases.json", "test_gen.py", "summary.md"):
            f = src / name
            if f.exists():
                shutil.copy2(f, dest / name)
        for f in src.glob("test_gen_*.py"):
            shutil.copy2(f, dest / f.name)

        cases_path = src / "cases.json"
        if cases_path.exists():
            try:
                from automation.ci_ai_gen.export_xlsx import (
                    cases_from_file,
                    export_cases_to_markdown_split,
                    export_cases_to_xlsx,
                )

                cases = cases_from_file(cases_path)
                count = export_cases_to_xlsx(cases, dest / "cases.xlsx")
                md_counts = export_cases_to_markdown_split(
                    cases, dest, title=f"AI 生成测试用例（{self.config.run_id}）",
                )
                print(f"[archive] exported {count} cases -> {dest / 'cases.xlsx'}")
                for name, n in md_counts.items():
                    print(f"[archive] exported {n} cases -> {dest / name}")
            except Exception as e:  # pragma: no cover
                print(f"[archive] xlsx/md export failed: {e}")
        print(f"[archive] artifacts copied -> {dest}")

    def _write_summary(self) -> None:
        lines = [
            f"# AI 测试生成摘要 {self.config.run_id}",
            "",
            "| 阶段 | 状态 | 说明 |",
            "|------|------|------|",
        ]
        for name in ("analyze", "cases", "script", "gate"):
            r = self.results.get(name, {})
            ok = "✅ 通过" if r.get("passed") else "⚠️ 未通过"
            detail = "; ".join(r.get("issues", [])) or "-"
            lines.append(f"| {name} | {ok} | {detail} |")
        lines.append("")
        failed = [n for n, r in self.results.items() if not r.get("passed")]
        lines.append(f"未通过阶段: {', '.join(failed) if failed else '无'}")
        lines.append("")
        lines.append("> 生成脚本路径: `test_gen/{run_id}/test_gen.py`（门禁通过时可直接执行）")
        summary_path = self.config.out_dir / self.config.run_id / "summary.md"
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text("\n".join(lines), encoding="utf-8")


async def main() -> int:
    parser = argparse.ArgumentParser(description="AI test generation pipeline")
    parser.add_argument("--spec-dir", required=True, help="dir with openapi.json + endpoints.json")
    parser.add_argument("--out-dir", required=True, help="output root (run_id subdir created)")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--no-verify-runtime", action="store_true",
                        help="skip pytest --collect-only verification")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--prd", default="", help="PRD document path (feature-driven mode)")
    parser.add_argument("--archive-dir", default="",
                        help="archive dir for generated artifacts (default: automation/references/generated-cases)")
    args = parser.parse_args()

    archive_dir = Path(args.archive_dir) if args.archive_dir else \
        Path(__file__).parents[1] / "references" / "generated-cases"

    config = PipelineConfig(
        spec_dir=Path(args.spec_dir),
        out_dir=Path(args.out_dir),
        run_id=args.run_id,
        verify_runtime=not args.no_verify_runtime,
        base_url=args.base_url,
        prd_path=Path(args.prd) if args.prd else None,
        archive_dir=archive_dir,
    )
    try:
        pipeline = Pipeline(config)
    except Exception as e:
        print(f"[pipeline] LLM init failed: {e}", file=sys.stderr)
        return 1
    try:
        results = await pipeline.run()
    except Exception as e:  # pragma: no cover
        print(f"[pipeline] unexpected error: {e}\n{traceback.format_exc()}", file=sys.stderr)
        return 1
    for name, r in results.items():
        print(f"[{name}] {'PASS' if r['passed'] else 'FAIL'} {'; '.join(r['issues'])}")
    return 0


if __name__ == "__main__":
    import asyncio

    raise SystemExit(asyncio.run(main()))
