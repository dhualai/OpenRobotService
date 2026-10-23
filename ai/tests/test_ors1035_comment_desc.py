"""ORS-1035 单测：评论图片打标链路 + 选题环回退。

- summarize 素材拼图片描述（_comment_image_descs）
- describe 端点参数校验与 VLM 调用
- 选题环候选回退：票史 > 名下（智能匹配已摘除）
"""
import asyncio
import sys
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai.agents.AiTaskPlatform.handlers.summarize_flow import _comment_image_descs


class _Comment:
    def __init__(self, attachments=None, content=""):
        self.attachments = attachments or []
        self.content = content


class TestCommentImageDescs:
    def test_dict_with_desc(self):
        c = _Comment([{"path": "b/t/a.png", "filename": "a.png",
                       "desc": "报错弹窗：连接超时"}])
        assert _comment_image_descs(c) == ["a.png：报错弹窗：连接超时"]

    def test_plain_string_skipped(self):
        """历史纯 path 字符串无 desc → 跳过（不硬造描述）。"""
        c = _Comment(["b/t/a.png"])
        assert _comment_image_descs(c) == []

    def test_empty_desc_skipped(self):
        c = _Comment([{"path": "b/t/a.png", "filename": "a.png", "desc": "  "}])
        assert _comment_image_descs(c) == []

    def test_cap3_and_truncate120(self):
        c = _Comment([
            {"path": f"b/t/{i}.png", "filename": f"{i}.png", "desc": "长" * 200}
            for i in range(5)
        ])
        out = _comment_image_descs(c)
        assert len(out) == 3
        assert all(len(x) <= 24 + 1 + 120 for x in out)

    def test_no_attachments(self):
        assert _comment_image_descs(_Comment()) == []


class TestMediaDescribeEndpoint:
    def _call(self, body, llm):
        from ai.api import router as ai_router
        with patch.object(ai_router, "get_llm_client", new=AsyncMock(return_value=llm)):
            return asyncio.run(ai_router.media_describe(body))

    def test_empty_images_rejected(self):
        from ai.api.router import MediaDescribeRequest
        out = self._call(MediaDescribeRequest(images=[], context=""),
                         AsyncMock())
        assert out["code"] == 1

    def test_happy_path(self):
        from ai.api.router import MediaDescribeRequest
        llm = MagicMock()
        llm.complete_vision = AsyncMock(return_value="  报错弹窗：连接超时，界面停在重试页。 ")
        out = self._call(MediaDescribeRequest(
            images=["data:image/png;base64,xxx"], context="如图"), llm)
        assert out["code"] == 0
        assert out["data"]["desc"].startswith("报错弹窗")
        llm.complete_vision.assert_awaited_once()
        assert len(llm.complete_vision.await_args.kwargs.get("images") or
                   llm.complete_vision.await_args.args[1]) == 1

    def test_vlm_empty_returns_code1(self):
        from ai.api.router import MediaDescribeRequest
        llm = MagicMock()
        llm.complete_vision = AsyncMock(return_value="")
        out = self._call(MediaDescribeRequest(
            images=["data:image/png;base64,xxx"], context=""), llm)
        assert out["code"] == 1

    def test_vlm_raises_returns_code1(self):
        from ai.api.router import MediaDescribeRequest
        llm = MagicMock()
        llm.complete_vision = AsyncMock(side_effect=RuntimeError("超时"))
        out = self._call(MediaDescribeRequest(
            images=["data:image/png;base64,xxx"], context=""), llm)
        assert out["code"] == 1
        assert "超时" in out["message"]


class TestStandardReportMechanicalCard:
    """ORS-1035 用户设想：标准 MD 八节报告机械映射知识卡（不调 LLM）。"""

    REPORT = """# 核实货叉防掉落机制（车端确认）

## 问题概述
用户需确认货叉防掉落机制的实际实现情况。故障码：E1002
现场截图：![IMG_6384.jpeg](/api/tasks/attachments/download?path=helpdesk-comment%2Fa.jpg)

## 问题场景
举升式仓储机器人，货叉载货升降环节。

## 复现条件
举升行程超过 500mm 时观察防坠落保护。

## 原因分析
防掉落翘起由车端逻辑控制（待按车型确认）。

## 解决方案
1. 按车型 XP1152 确认货叉控制归属
2. 由工程师出具结论

## 验证结果
现场处理人确认车端实现，附现场图片。

## 适用范围
车型 XP1152 及同构举升车型。

## 遗留问题
无
"""

    def test_standard_md_maps_mechanically(self):
        from ai.core.solution_sink import _card_from_standard_report
        card = _card_from_standard_report(self.REPORT)
        assert card is not None
        assert "货叉防掉落机制" in card["problem_summary"]
        assert "车端逻辑控制" in card["root_cause"]
        assert "XP1152" in card["solution_steps"]
        assert "E1002" == card["fault_code"]
        assert "XP1152" in card["robot_type"]
        # 图片语法剥掉
        assert "attachments/download" not in card["problem_summary"]

    def test_missing_anchor_goes_llm_path(self):
        """缺「解决方案」锚点 = 非标准 → None（调用方走 LLM）。"""
        from ai.core.solution_sink import _card_from_standard_report
        bad = "# 标题\n\n## 问题概述\n只有概述没有解决节\n"
        assert _card_from_standard_report(bad) is None

    def test_plain_text_returns_none(self):
        from ai.core.solution_sink import _card_from_standard_report
        assert _card_from_standard_report("随便写的几行话\n没有小节结构") is None
        assert _card_from_standard_report("") is None

    def test_tolerates_one_deleted_section(self):
        """审核改稿删掉一节（7/8）→ 仍机械映射，不跌回 LLM。"""
        from ai.core.solution_sink import _card_from_standard_report
        report = self.REPORT.replace("## 遗留问题\n无\n", "")
        card = _card_from_standard_report(report)
        assert card is not None and "货叉" in card["problem_summary"]

    def test_fuzzy_reworded_headers(self):
        """改措辞（问题概述→现象描述、解决方案→处理方式）仍按类识别。"""
        from ai.core.solution_sink import _card_from_standard_report
        report = self.REPORT.replace("## 问题概述", "## 现象描述").replace("## 解决方案", "## 处理方式")
        card = _card_from_standard_report(report)
        assert card is not None
        assert "货叉" in card["problem_summary"]
        assert "XP1152" in card["solution_steps"]

    def test_qa_variant_two_sections(self):
        """咨询问答两节制：问题描述 + 回答 —— 机械映射照常出卡（1020 用户定）。"""
        from ai.core.solution_sink import _card_from_standard_report
        qa = """# 标题

## 问题描述
在什么条件下出现了这个问题，涉及车型 XP1152。

## 回答
这个问题的原因是货叉防掉落由车端实现；解决方式是按车型确认归属。
"""
        card = _card_from_standard_report(qa)
        assert card is not None
        assert "XP1152" in card["problem_summary"]
        assert "车端" in card["root_cause"] or "车端" in card["solution_steps"]
        assert "原因" in card["solution_steps"] or "原因" in card["root_cause"]

    def test_too_few_sections_returns_none(self):
        """只剩 3 类（<5）→ 不算标准 MD，走 LLM。"""
        from ai.core.solution_sink import _card_from_standard_report
        report = "## 问题概述\n现象\n\n## 原因分析\n原因\n\n## 验证结果\n已验证\n"
        assert _card_from_standard_report(report) is None


class TestProjectCandidatesRevert:
    """ORS-1035：选题环回退票史口径——智能匹配源已摘除。"""

    def _pipeline(self):
        from ai.agents.AiDiagnosisPlatform.pipeline import AiDiagnosisPlatform
        p = object.__new__(AiDiagnosisPlatform)

        async def _recent(u):
            return [{"name": "票史项目", "code": "1"}]

        async def _owned(u):
            return [{"name": "名下项目", "code": "2"},
                    {"name": "票史项目", "code": "1"}]

        p._get_recent_ticket_projects = _recent
        p._get_user_projects = _owned
        return p

    def test_recent_first_owned_fill(self):
        p = self._pipeline()
        out = asyncio.run(p._get_project_candidates("tester"))
        assert [c["name"] for c in out] == ["票史项目", "名下项目"]

    def test_no_smart_match_source(self):
        """智能匹配源已删除：_match_projects_for_session 不存在。"""
        p = self._pipeline()
        assert not hasattr(p, "_match_projects_for_session")

    def test_signature_no_memory_param(self):
        """签名回退为 (self, username)，调用点不再传 memory。"""
        import inspect
        from ai.agents.AiDiagnosisPlatform.pipeline import AiDiagnosisPlatform
        sig = inspect.signature(AiDiagnosisPlatform._get_project_candidates)
        assert list(sig.parameters) == ["self", "username"]


class TestArchiveReportImageMaterial:
    """ORS-1035：归档报告素材的图片描述拼装（solution_sink 纯函数）。"""

    def test_descs_extraction(self):
        from ai.core.solution_sink import _comment_image_descs
        atts = [{"path": "b/t/a.png", "filename": "a.png", "desc": "报错弹窗"}]
        assert _comment_image_descs(atts) == ["a.png：报错弹窗"]

    def test_descs_skip_plain_and_empty(self):
        from ai.core.solution_sink import _comment_image_descs
        assert _comment_image_descs(["b/t/a.png",
                                     {"path": "b/t/b.png", "filename": "b.png"}]) == []

    def test_count_images_both_forms(self):
        from ai.core.solution_sink import _count_comment_images
        atts = ["b/t/a.PNG", {"path": "b/t/b.jpeg"}, "b/t/doc.pdf", {"path": ""}]
        assert _count_comment_images(atts) == 2

    def test_similar_summary_strips_embedded_image_md(self):
        """报告沉淀后卡正文带图片 markdown——相似弹层 summary 剥掉图片语法，
        保留其余文字。"""
        from ai.core.retrieval import RetrievalResult, rank_similar_tickets
        r = RetrievalResult(
            id="x", score=0.02, title="t",
            content="问题：货叉翘起\n![a.jpeg](/api/tasks/attachments/download?path=b%2Fa.jpeg)\n根因：车端控制",
            vector_score=0.8, task_id="462", review_status="approved",
        )
        out = rank_similar_tickets([r])
        assert "attachments/download" not in out[0]["summary"]
        assert "货叉翘起" in out[0]["summary"]
        assert "根因：车端控制" in out[0]["summary"]
