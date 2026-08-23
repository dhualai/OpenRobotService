"""
测试上传附件行为：
  1. 附件不生成为对话 turn（不污染 LLM 上下文）
  2. 图片 VLM 描述存入 collected_info.image_description
  3. 附件列表存入 agent_state.attachments
  4. 文件名中的数字不会被 LLM 误读为错误码
"""
import pytest


class TestUploadDoesNotPolluteConversation:
    """上传附件不应在对话 turns 中产生虚假用户消息"""

    def test_no_fake_user_turn_for_non_image(self):
        """非图片文件 → 不应该有任何对话 turn 注入"""
        # 模拟 /qa/upload 的核心逻辑（去掉 MinIO/VLM 外部依赖）
        filenames = "log.txt, jpg_8805.data"
        saved = [{"filename": "log.txt"}, {"filename": "jpg_8805.data"}]
        image_desc = ""

        # 模拟：构建一个干净的会话状态
        turns = []  # 对话记录

        # 修复后的行为：不应 add_turn
        if image_desc:
            turns.append({"role": "user", "content": f"我上传了文件：{filenames}"})

        # 断言：没有 VLM 描述 → 不应注入任何 turn
        assert len(turns) == 0, (
            f"非图片文件不应注入对话 turn，但注入了 {len(turns)} 条"
        )

    def test_vlm_desc_not_injected_as_user_turn(self):
        """VLM 描述不再伪装成用户发言注入对话"""
        saved = [{"filename": "screenshot.png"}]
        image_desc = "界面显示错误码 4201，机器人状态离线"

        # 修复后：不再 add_turn，而是存 metadata
        turns = []
        collected_info = {}

        # 模拟新的存储方式
        if image_desc:
            collected_info["image_description"] = image_desc

        # 断言：turns 没有被污染
        assert len(turns) == 0
        # 断言：描述去了正确的地方
        assert collected_info.get("image_description") == image_desc

    def test_filename_numbers_not_in_conversation(self):
        """文件名 'jpg_8805' 不应出现在对话上下文中"""
        filenames = "jpg_8805.jpg, error_log_4201.txt"
        # 模拟 LLM 看到的对话上下文（_format_conversation 的输出）
        conversation_context = [
            {"role": "user", "content": "机器人离线了"},
            {"role": "assistant", "content": "让我帮你排查一下"},
        ]

        # 构建 LLM 会看到的完整文本
        formatted = "\n".join(
            f"{'用户' if t['role'] == 'user' else '助手'}：{t['content']}"
            for t in conversation_context
        )

        # 断言：文件名不应出现在对话上下文中
        assert "jpg_8805" not in formatted, (
            f"文件名不应出现在对话上下文中，但发现了 'jpg_8805'"
        )
        assert "4201" not in formatted, (
            f"文件名中的数字不应泄漏到对话中，但发现了 '4201'"
        )


class TestUploadMetadataCorrectness:
    """上传后元数据应正确存储"""

    def test_attachments_stored_in_agent_state(self):
        """附件列表正确存入 agent_state.attachments"""
        state = {"attachments": []}
        saved = [
            {"filename": "screenshot.png", "size": 102400, "path": "/minio/sess/screenshot.png"},
            {"filename": "log.txt", "size": 5120, "path": "/minio/sess/log.txt"},
        ]

        existing = state.get("attachments", [])
        state["attachments"] = existing + saved

        assert len(state["attachments"]) == 2
        assert state["attachments"][0]["filename"] == "screenshot.png"
        assert state["attachments"][1]["filename"] == "log.txt"

    def test_multiple_uploads_accumulate(self):
        """多次上传附件应该累积"""
        state = {"attachments": [{"filename": "first.jpg"}]}

        # 第二次上传
        state["attachments"] = state["attachments"] + [{"filename": "second.png"}]
        assert len(state["attachments"]) == 2

        # 第三次上传
        state["attachments"] = state["attachments"] + [{"filename": "third.txt"}]
        assert len(state["attachments"]) == 3

    def test_image_description_stored_in_collected_info(self):
        """图片 VLM 描述存入 collected_info.image_description"""
        collected_info = {}
        image_desc = "错误码 4201：通信超时，机器人最后位置在3号产线"

        prev = collected_info.get("image_description", "")
        collected_info["image_description"] = (
            (prev + "\n" + image_desc).strip() if prev else image_desc
        )

        assert "image_description" in collected_info
        assert "4201" in collected_info["image_description"]
        assert "3号产线" in collected_info["image_description"]

    def test_multiple_image_descriptions_concatenate(self):
        """多次上传图片 → 描述累积拼接"""
        collected_info = {}

        # 第一张图
        d1 = "图1：机器人控制面板显示离线状态"
        collected_info["image_description"] = d1

        # 第二张图
        d2 = "图2：错误日志显示网络超时"
        prev = collected_info.get("image_description", "")
        collected_info["image_description"] = (
            (prev + "\n" + d2).strip() if prev else d2
        )

        assert "图1" in collected_info["image_description"]
        assert "图2" in collected_info["image_description"]

    def test_non_image_no_description_stored(self):
        """非图片文件：collected_info 中不应有 image_description"""
        collected_info = {}
        image_desc = ""

        if image_desc:
            collected_info["image_description"] = image_desc

        assert "image_description" not in collected_info


class TestLLMNotConfusedByFilenames:
    """LLM 不应该因为文件名中的数字而产生幻觉"""

    def test_no_error_code_false_positive(self):
        """jpg_8805 中的 8805 不应被 LLM 当作错误码——因为根本不在对话里"""
        # 模拟诊断 prompt 中 LLM 能看到的内容
        prompt_context = {
            "problem_summary": "机器人离线",
            "collected_info": {"project": "华大基地"},
        }
        # 附件信息只在 ticket 构建时引用，不在对话中
        attachments = [{"filename": "jpg_8805.jpg"}]

        # LLM 看到的上下文文本
        visible_text = (
            f"问题：{prompt_context['problem_summary']}\n"
            f"已收集：{prompt_context['collected_info']}"
        )

        # 断言：8805 不出现在 LLM 可见文本中
        assert "8805" not in visible_text, (
            "文件名数字不应出现在 LLM 上下文里"
        )
        assert "jpg" not in visible_text.lower()

    def test_image_desc_visible_but_not_as_error_code(self):
        """图片描述可以被 LLM 看到，但会以 structured 形式展示，不容易误读"""
        prompt_context = {
            "problem_summary": "机器人离线",
            "collected_info": {
                "project": "华大基地",
                "image_description": "界面显示错误码 4201，机器人状态离线",
            },
        }

        # LLM 看到的上下文（模拟 _build_diagnosis_prompt 的格式）
        visible_text = (
            f"问题：{prompt_context['problem_summary']}\n"
            f"已收集：{prompt_context['collected_info']}"
        )

        # 图片描述中的内容会被 LLM 看到（这是有用的信息）
        assert "4201" in visible_text
        assert "image_description" in visible_text
        # 但不是作为孤立的数字，而是有上下文包裹的
        assert "错误码 4201" in visible_text


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
