"""AI 项目摘要服务纯函数测试 —— 不连库、不调大模型。

覆盖：节点内容解码（text/select/file JSON）、信息树渲染（路径/空值跳过/未填写计数/
截断）、提示词组装（项目字段标签、空值跳过、未填写提示、写作要求）与模型输出清洗。
"""
import json

from app.modules.admin.services.project_ai_summary_service import (
    _clean_summary,
    _display_value,
    build_summary_prompt,
    render_project_info,
)


def _project():
    return {
        "name": "测试项目A",
        "project_code": "P-001",
        "status": "实施中",
        "project_type": "试点项目",
        "project_region": "大陆(China Mainland)",
        "total_vehicle_count": 12,
        "controller_vendor": "自研",
        "system_integration": ["DAS", "客户WMS"],
        "project_manager": None,  # 空值应被跳过
        "description": "",
    }


def _tree():
    return [
        {"title": "基础信息", "value": None, "content_type": "text", "children": [
            {"title": "项目区域/地点", "value": None, "content_type": "text", "children": [
                {"title": "区域选项", "content_type": "select",
                 "value": json.dumps({"selected": "大陆(China Mainland)", "options": []}),
                 "children": []},
                {"title": "省份", "value": "", "content_type": "text", "children": []},
            ]},
        ]},
        {"title": "硬件", "value": None, "content_type": "text", "children": [
            {"title": "车辆", "value": None, "content_type": "text", "children": [
                {"title": "XCD061", "value": "6 台", "content_type": "text", "children": []},
            ]},
        ]},
    ]


def test_display_value_text_collapses_whitespace():
    assert _display_value({"content_type": "text", "value": "多行\n文本   内容\t末"}) == "多行 文本 内容 末"


def test_display_value_select_decodes_selected():
    node = {"content_type": "select", "value": json.dumps({"selected": "试点项目", "options": []})}
    assert _display_value(node) == "试点项目"


def test_display_value_select_fallbacks():
    # 非 JSON 的 select 值按原文兜底（兼容旧数据把选项直接存成字符串）
    assert _display_value({"content_type": "select", "value": "not-json"}) == "not-json"
    assert _display_value({"content_type": "select", "value": json.dumps({"options": []})}) == ""


def test_display_value_file_json_picks_name():
    node = {"content_type": "file", "value": json.dumps({"name": "需求说明书.docx", "url": "/x"})}
    assert _display_value(node) == "需求说明书.docx"


def test_display_value_empty():
    assert _display_value({"content_type": "text", "value": ""}) == ""
    assert _display_value({"content_type": "text", "value": None}) == ""


def test_render_project_info_paths_and_counts():
    body, filled, empty_leaf = render_project_info(_tree())
    assert "基础信息 / 项目区域/地点 / 区域选项：大陆(China Mainland)" in body
    assert "硬件 / 车辆 / XCD061：6 台" in body
    assert filled == 2
    assert empty_leaf == 1  # 省级节点未填写
    # 空值节点不出现在正文里
    assert "省份" not in body


def test_render_project_info_truncates():
    body, _, _ = render_project_info(_tree(), max_chars=10)
    assert "已截断" in body and len(body) < 100


def test_build_summary_prompt_contains_fields_and_info():
    prompt = build_summary_prompt(_project(), _tree())
    assert "项目名称：测试项目A" in prompt
    assert "控制器选择：自研" in prompt
    assert "系统/外设对接：DAS、客户WMS" in prompt
    assert "硬件 / 车辆 / XCD061：6 台" in prompt
    assert "另有 1 个节点未填写" in prompt
    # 空值字段被跳过
    assert "项目经理" not in prompt
    # 结构化 Markdown 输出要求与硬约束在提示词里
    assert "250 字以内" in prompt
    assert "## 项目概况" in prompt
    assert "## 风险与关注点" in prompt
    assert "不得编造" in prompt


def test_render_project_info_counts_value_bearing_groups():
    """「有值又有子节点」的节点（下拉车型 + 数量）自己也是一条可填项。

    选中了型号、数量还没填时，不能整片算成「未填写」——否则摘要会漏掉车型，
    展示页也会把这条分支当成空分支裁掉。
    """
    tree = [
        {"title": "硬件", "value": None, "content_type": "text", "children": [
            {"title": "车辆", "value": None, "content_type": "text", "children": [
                {"title": "车型1", "content_type": "select",
                 "value": json.dumps({"selected": "XC1051", "options": ["XC1051"]}),
                 "children": [
                     {"title": "数量", "value": "", "content_type": "text", "children": []},
                 ]},
            ]},
        ]},
    ]
    body, filled, empty_leaf = render_project_info(tree)
    assert "硬件 / 车辆 / 车型1：XC1051" in body
    assert filled == 1
    assert empty_leaf == 1  # 车型1 下的「数量」未填
    # 纯文本分组（硬件 / 车辆）自己没有值，不该被算成未填写
    assert "硬件 / 车辆：" not in body


def test_build_summary_prompt_empty_tree():
    prompt = build_summary_prompt(_project(), [])
    assert "（信息树中的条目均未填写）" in prompt


def test_clean_summary_plain_and_fenced():
    assert _clean_summary("  正常正文。 ") == "正常正文。"
    assert _clean_summary("```\n围栏正文\n```") == "围栏正文"
    assert _clean_summary('```text\n带标注的正文\n```') == "带标注的正文"
    assert _clean_summary('"引号包裹"') == "引号包裹"
