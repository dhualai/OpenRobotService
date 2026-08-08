"""Pipeline orchestration tests using a fake LLM (no real calls).

Verifies: stage ordering, structural gates, gate fix loop, degrade-and-
report behavior.
"""

import json
from pathlib import Path

import pytest

from automation.ci_ai_gen.gates import (
    check_analysis,
    check_cases,
    check_cases_req_coverage,
    check_script,
    check_script_paths,
    extract_req_ids,
    extract_script_paths,
)
from automation.ci_ai_gen.run_pipeline import Pipeline, PipelineConfig

SPEC = {
    "paths": {
        "/api/v1/tasks/{task_id}": {"get": {}},
        "/api/v1/tasks": {"post": {}},
    }
}

ANALYSIS_OK = """# 需求概述\n- 任务模块\n\n# 测试范围\n- 全部接口\n\n# 测试重点\n- 状态流转\n\n# 测试策略\n- 黑盒\n"""

CASES_OK = json.dumps([
    {
        "id": "TC001", "req_id": "REQ-01", "module": "tasks", "title": "获取任务",
        "type": "positive", "precondition": "已登录",
        "method": "GET", "path": "/api/v1/tasks/{task_id}",
        "steps": [{"id": 1, "step": "调用接口", "testData": "task_id=1", "expectedResult": "200"}],
    },
    {
        "id": "TC002", "req_id": "REQ-02", "module": "tasks", "title": "创建任务",
        "type": "negative", "precondition": "已登录",
        "method": "POST", "path": "/api/v1/tasks",
        "steps": [{"id": 1, "step": "空参数", "testData": "{}", "expectedResult": "422"}],
    },
])

ANALYSIS_PRD = """# 需求概述
- 任务模块

# 功能点清单
- REQ-01 获取任务：查询任务详情
- REQ-02 创建任务：新建工单

# 状态流转
- 新建 → 处理中 → 已解决

# 权限矩阵
- 客户：仅自己工单

# 测试策略
- 黑盒
"""

SCRIPT_OK = '''import requests

BASE_URL = "http://localhost:8000"

def parse_response(r):
    return r.json()

class TestGeneratedAPI:
    def test_tc001(self):
        """获取任务"""
        resp = requests.get(f"{BASE_URL}/api/v1/tasks/1", verify=False)
        assert resp.status_code == 200

    def test_tc002(self):
        """创建任务"""
        resp = requests.post(f"{BASE_URL}/api/v1/tasks", json={}, verify=False)
        assert resp.status_code == 422
'''


class FakeLLM:
    """Replays canned outputs per prompt name."""

    def __init__(self, replies):
        self.replies = dict(replies)
        self.calls = []

    async def complete(self, system_prompt, user_prompt, max_tokens=1024):
        self.calls.append((system_prompt, user_prompt))
        if "修复要求" in user_prompt:
            name = "script"
        elif "待审阅脚本" in user_prompt:
            name = "gate"
        elif "## 产品需求文档（PRD）" in user_prompt:
            name = "analyzer"
        elif "## 接口清单" in user_prompt and "## 需求分析" in user_prompt:
            name = "cases"
        elif "## 接口清单" in user_prompt:
            name = "analyzer"
        else:
            name = "script"
        return self.replies.get(name, "")


def _jsonc_trailing_comma(json_text: str) -> str:
    """Add a trailing comma after the last case element (invalid JSONC)."""
    return json_text.replace("\n]\n", ",\n]\n")


def _make_config(tmp_path: Path, prd: str = "", **kw) -> PipelineConfig:
    spec_dir = tmp_path / "spec"
    spec_dir.mkdir(parents=True)
    (spec_dir / "openapi.json").write_text(json.dumps(SPEC), encoding="utf-8")
    (spec_dir / "endpoints.json").write_text(json.dumps({
        "count": 2,
        "endpoints": [
            {"method": "GET", "path": "/api/v1/tasks/{task_id}", "params": [{"name": "task_id"}], "responses": ["200"]},
            {"method": "POST", "path": "/api/v1/tasks", "params": [], "responses": ["201", "422"]},
        ],
    }), encoding="utf-8")
    if prd:
        (spec_dir / "prd.md").write_text(prd, encoding="utf-8")
    return PipelineConfig(
        spec_dir=spec_dir,
        out_dir=tmp_path / "out",
        run_id="run-1",
        verify_runtime=False,
        **kw,
    )


class TestGates:
    def test_check_analysis_ok(self):
        assert check_analysis(ANALYSIS_OK) == []

    def test_check_analysis_missing_heading(self):
        issues = check_analysis("# 需求概述\n\n# 测试范围\n\n# 测试重点\n\n")
        assert any("测试策略" in i for i in issues)

    def test_check_cases_ok(self):
        issues, cases = check_cases(CASES_OK)
        assert issues == []
        assert len(cases) == 2

    def test_check_cases_invalid_json(self):
        issues, cases = check_cases("not json")
        assert issues and cases is None

    def test_check_cases_fenced_with_comments(self):
        messy = f"```json\n// 生成注释\n{_jsonc_trailing_comma(CASES_OK)}\n```"
        issues, cases = check_cases(messy)
        assert issues == []
        assert len(cases) == 2

    def test_strip_code_fence(self):
        from automation.ci_ai_gen.gates import strip_code_fence

        assert strip_code_fence("```python\nprint(1)\n```") == "print(1)\n"
        assert strip_code_fence("```\nprint(1)\n```") == "print(1)\n"
        assert strip_code_fence("no fence") == "no fence"

    def test_check_cases_duplicate_id(self):
        bad = json.dumps([json.loads(CASES_OK)[0], json.loads(CASES_OK)[0]])
        issues, _ = check_cases(bad)
        assert any("duplicate" in i for i in issues)

    def test_check_flow_case_ok(self):
        flow = [{
            "id": "TC100", "req_id": "REQ-27", "module": "系统任务-状态流转",
            "title": "取消后不可关闭", "type": "flow", "precondition": "已登录",
            "method": "POST", "path": "/api/tasks",
            "steps": [
                {"id": 1, "step": "建单", "method": "POST", "path": "/api/tasks",
                 "testData": "{}", "expectedResult": "200"},
                {"id": 2, "step": "取消", "method": "PATCH",
                 "path": "/api/tasks/{{step1.body.id}}/status",
                 "testData": '{"status":"cancelled"}', "expectedResult": "200"},
            ],
        }]
        issues, _ = check_cases(json.dumps(flow))
        assert issues == []

    def test_check_flow_case_missing_request_step(self):
        flow = [{
            "id": "TC101", "req_id": "REQ-27", "module": "系统任务-状态流转",
            "title": "缺请求步骤", "type": "flow", "precondition": "已登录",
            "method": "GET", "path": "/api/tasks",
            "steps": [
                {"id": 1, "step": "纯说明步骤", "testData": "x", "expectedResult": "y"},
                {"id": 2, "step": "建单", "method": "POST", "path": "/api/tasks",
                 "testData": "{}", "expectedResult": "200"},
            ],
        }]
        issues, _ = check_cases(json.dumps(flow))
        assert any("missing method/path" in i for i in issues)

    def test_check_flow_case_references_future_step(self):
        flow = [{
            "id": "TC102", "req_id": "REQ-27", "module": "系统任务-状态流转",
            "title": "引用未来步骤", "type": "flow", "precondition": "已登录",
            "method": "POST", "path": "/api/tasks",
            "steps": [
                {"id": 1, "step": "建单", "method": "POST", "path": "/api/tasks",
                 "testData": "{}", "expectedResult": "200"},
                {"id": 2, "step": "引用 step3", "method": "GET",
                 "path": "/api/tasks/{{step3.body.id}}", "testData": "", "expectedResult": "200"},
            ],
        }]
        issues, _ = check_cases(json.dumps(flow))
        assert any("step3 which is not executed yet" in i for i in issues)

    def test_check_flow_case_single_step(self):
        flow = [{
            "id": "TC103", "req_id": "REQ-27", "module": "系统任务-状态流转",
            "title": "单步不算链路", "type": "flow", "precondition": "已登录",
            "method": "GET", "path": "/api/tasks",
            "steps": [
                {"id": 1, "step": "查列表", "method": "GET", "path": "/api/tasks",
                 "testData": "", "expectedResult": "200"},
            ],
        }]
        issues, _ = check_cases(json.dumps(flow))
        assert any("at least 2 steps" in i for i in issues)

    def test_extract_script_paths(self):
        paths = extract_script_paths(SCRIPT_OK)
        assert "/api/v1/tasks/1" in paths
        assert "/api/v1/tasks" in paths

    def test_check_script_ok(self):
        assert check_script(SCRIPT_OK, SPEC) == []

    def test_check_script_path_not_in_spec(self):
        bad = SCRIPT_OK.replace("/api/v1/tasks/1", "/api/v1/fake/1")
        issues = check_script(bad, SPEC)
        assert any("not in spec" in i for i in issues)

    def test_check_script_path_normalizes_placeholder(self):
        script = SCRIPT_OK.replace("/api/v1/tasks/1", "/api/v1/tasks/{task_id}")
        assert check_script_paths(script, SPEC) == []

    def test_check_script_no_tests(self):
        issues = check_script("import requests\n", SPEC)
        assert any("test_" in i for i in issues)


class TestPipeline:
    @pytest.mark.asyncio
    async def test_happy_path(self, tmp_path):
        llm = FakeLLM({
            "analyzer": ANALYSIS_OK,
            "cases": CASES_OK,
            "script": SCRIPT_OK,
            "gate": '{"passed": true, "issues": []}',
        })
        pipeline = Pipeline(_make_config(tmp_path), llm=llm)
        results = await pipeline.run()
        assert all(r["passed"] for r in results.values())
        assert (tmp_path / "out" / "run-1" / "test_gen.py").exists()
        assert (tmp_path / "out" / "run-1" / "summary.md").exists()

    @pytest.mark.asyncio
    async def test_analyze_fail_degrades_but_continues(self, tmp_path):
        llm = FakeLLM({
            "analyzer": "# 只有标题\n",
            "cases": CASES_OK,
            "script": SCRIPT_OK,
            "gate": '{"passed": true, "issues": []}',
        })
        pipeline = Pipeline(_make_config(tmp_path), llm=llm)
        results = await pipeline.run()
        assert not results["analyze"]["passed"]
        assert results["script"]["passed"] and results["gate"]["passed"]

    @pytest.mark.asyncio
    async def test_script_fix_loop(self, tmp_path):
        gate_fails = '{"passed": false, "issues": [{"severity": "error", "detail": "缺少断言"}]}'
        llm = FakeLLM({
            "analyzer": ANALYSIS_OK,
            "cases": CASES_OK,
            "script": SCRIPT_OK,
            "gate": gate_fails,
        })
        # gate always fails -> after max_fix_rounds (2) the gate stage fails
        pipeline = Pipeline(_make_config(tmp_path), llm=llm)
        results = await pipeline.run()
        assert not results["gate"]["passed"]
        # gate reviewed 3 times (1 initial + 2 after fixes); 2 fix requests
        gate_calls = [c for c in llm.calls if "质量门禁" in c[0]]
        assert len(gate_calls) == 3
        script_calls = [c for c in llm.calls if "修复要求" in c[1]]
        assert len(script_calls) == 2

    @pytest.mark.asyncio
    async def test_gate_passes_after_fix(self, tmp_path):
        replies = {"analyzer": ANALYSIS_OK, "cases": CASES_OK, "script": SCRIPT_OK}
        gate_responses = [
            '{"passed": false, "issues": [{"severity": "error", "detail": "缺少注释"}]}',
            '{"passed": true, "issues": []}',
        ]
        counter = {"i": 0}

        class FixingLLM(FakeLLM):
            async def complete(self, system_prompt, user_prompt, max_tokens=1024):
                self.calls.append((system_prompt, user_prompt))
                if "待审阅脚本" in user_prompt:
                    reply = gate_responses[counter["i"]]
                    counter["i"] += 1
                    return reply
                if "修复要求" in user_prompt:
                    return SCRIPT_OK
                if "## 接口清单" in user_prompt and "## 需求分析" in user_prompt:
                    return CASES_OK
                if "## 接口清单" in user_prompt:
                    return ANALYSIS_OK
                return SCRIPT_OK

        pipeline = Pipeline(_make_config(tmp_path), llm=FixingLLM({}))
        results = await pipeline.run()
        assert results["gate"]["passed"]

    @pytest.mark.asyncio
    async def test_unparsable_gate_output(self, tmp_path):
        llm = FakeLLM({
            "analyzer": ANALYSIS_OK,
            "cases": CASES_OK,
            "script": SCRIPT_OK,
            "gate": "我无法判断",
        })
        pipeline = Pipeline(_make_config(tmp_path), llm=llm)
        results = await pipeline.run()
        assert not results["gate"]["passed"]


class TestReqCoverage:
    def test_extract_req_ids(self):
        ids = extract_req_ids("- REQ-01 获取任务\n- REQ-02 创建任务\n- REQ-02 重复")
        assert ids == ["REQ-01", "REQ-02"]

    def test_extract_req_ids_empty(self):
        assert extract_req_ids("无编号") == []

    def test_coverage_ok(self):
        _, cases = check_cases(CASES_OK)
        assert check_cases_req_coverage(cases, ["REQ-01", "REQ-02"]) == []

    def test_coverage_missing_req(self):
        _, cases = check_cases(CASES_OK)
        issues = check_cases_req_coverage(cases, ["REQ-01", "REQ-02", "REQ-03"])
        assert any("REQ-03" in i for i in issues)

    def test_coverage_no_cases(self):
        issues = check_cases_req_coverage(None, ["REQ-01"])
        assert issues

    def test_coverage_no_reqs_skips(self):
        assert check_cases_req_coverage([], []) == []


class TestPipelinePrdMode:
    @pytest.mark.asyncio
    async def test_prd_happy_path(self, tmp_path):
        llm = FakeLLM({
            "analyzer": ANALYSIS_PRD,
            "cases": CASES_OK,
            "script": SCRIPT_OK,
            "gate": '{"passed": true, "issues": []}',
        })
        pipeline = Pipeline(_make_config(tmp_path, prd="# PRD\n功能点：获取任务、创建任务"), llm=llm)
        results = await pipeline.run()
        assert results["analyze"]["passed"]
        assert results["cases"]["passed"]
        # analyzer prompt contained the PRD document
        assert any("## 产品需求文档（PRD）" in c[1] for c in llm.calls)

    @pytest.mark.asyncio
    async def test_prd_analysis_without_req_ids_fails(self, tmp_path):
        llm = FakeLLM({
            "analyzer": ANALYSIS_OK,  # 无 REQ 编号
            "cases": CASES_OK,
            "script": SCRIPT_OK,
            "gate": '{"passed": true, "issues": []}',
        })
        pipeline = Pipeline(_make_config(tmp_path, prd="# PRD"), llm=llm)
        results = await pipeline.run()
        assert not results["analyze"]["passed"]
        assert any("REQ" in i for i in results["analyze"]["issues"])

    @pytest.mark.asyncio
    async def test_prd_coverage_gap_fails_cases(self, tmp_path):
        partial = json.dumps([
            {**json.loads(CASES_OK)[0]},  # 只有 REQ-01
        ])
        llm = FakeLLM({
            "analyzer": ANALYSIS_PRD,   # 要求 REQ-01, REQ-02
            "cases": partial,
            "script": SCRIPT_OK,
            "gate": '{"passed": true, "issues": []}',
        })
        pipeline = Pipeline(_make_config(tmp_path, prd="# PRD"), llm=llm)
        results = await pipeline.run()
        assert not results["cases"]["passed"]
        assert any("REQ-02" in i for i in results["cases"]["issues"])

    @pytest.mark.asyncio
    async def test_archive_copies_artifacts(self, tmp_path):
        llm = FakeLLM({
            "analyzer": ANALYSIS_PRD,
            "cases": CASES_OK,
            "script": SCRIPT_OK,
            "gate": '{"passed": true, "issues": []}',
        })
        archive = tmp_path / "archive"
        pipeline = Pipeline(
            _make_config(tmp_path, prd="# PRD", archive_dir=archive), llm=llm,
        )
        results = await pipeline.run()
        assert results["gate"]["passed"]
        dest = archive / "run-1"
        assert (dest / "analysis.md").exists()
        assert (dest / "cases.json").exists()
        assert (dest / "test_gen.py").exists()
        assert (dest / "summary.md").exists()
        assert (dest / "cases.xlsx").exists()

    @pytest.mark.asyncio
    async def test_no_archive_dir_skips(self, tmp_path):
        llm = FakeLLM({
            "analyzer": ANALYSIS_PRD,
            "cases": CASES_OK,
            "script": SCRIPT_OK,
            "gate": '{"passed": true, "issues": []}',
        })
        pipeline = Pipeline(_make_config(tmp_path, prd="# PRD"), llm=llm)
        await pipeline.run()
        assert not (tmp_path / "archive").exists()
