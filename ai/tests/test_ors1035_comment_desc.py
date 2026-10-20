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
