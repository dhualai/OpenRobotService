# -*- coding: utf-8 -*-
"""项目题候选 md 文字化：metadata_.project_choices 在工单对话记录 md 里渲染成
编号列表（前端按钮的文字版）。纯函数测试，无 DB/MinIO 依赖。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from ai.core.chat_snapshot import _expand_project_choices, _turns_to_markdown

CHOICES = [{"index": 1, "name": "摇人吧服务号", "code": "YRB"},
           {"index": 2, "name": "南京本川项目"}]


def _meta(choices, double=False):
    s = json.dumps({"project_choices": choices}, ensure_ascii=False)
    return json.dumps(s, ensure_ascii=False) if double else s


def test_expand_inserts_listing_between_head_tail():
    content = "出单前确认一下关联项目\n\n点击下方按钮选择，或直接回复【序号】"
    out = _expand_project_choices({"metadata_": _meta(CHOICES)}, content)
    assert out == ("出单前确认一下关联项目\n\n"
                   "1. 摇人吧服务号\n2. 南京本川项目\n\n"
                   "点击下方按钮选择，或直接回复【序号】")


def test_expand_double_encoded_metadata():
    # safe_json_dumps 二次编码形态（后端 update_message 写入后的真实存储）
    content = "题面head\n\ntail"
    out = _expand_project_choices({"metadata_": _meta(CHOICES, double=True)}, content)
    assert "1. 摇人吧服务号" in out and "2. 南京本川项目" in out
    assert out.startswith("题面head\n\n1.")


def test_expand_no_blank_line_appends_tail():
    out = _expand_project_choices({"metadata_": _meta(CHOICES)}, "单段题面")
    assert out == "单段题面\n\n1. 摇人吧服务号\n2. 南京本川项目"


def test_expand_ignores_missing_or_invalid():
    plain = "普通话术\n\ntail"
    assert _expand_project_choices({}, plain) == plain
    assert _expand_project_choices({"metadata_": None}, plain) == plain
    assert _expand_project_choices({"metadata_": "不是json"}, plain) == plain
    assert _expand_project_choices({"metadata_": json.dumps({"project_choices": []})}, plain) == plain


def test_turns_to_markdown_renders_listing():
    turns = [
        {"role": "user", "content": "提单给贾爽，车不动了", "created_at": ""},
        {"role": "assistant", "content": "出单前确认一下关联项目\n\n回复【序号】我帮你预填",
         "metadata_": _meta(CHOICES)},
        {"role": "user", "content": "1", "created_at": ""},
        {"role": "assistant", "content": "已确认项目为摇人吧服务号。", "created_at": ""},
    ]
    md, _rest = _turns_to_markdown(turns)
    assert "1. 摇人吧服务号\n2. 南京本川项目" in md
    # 列表插在 head 与 tail 之间（与前端按钮位置一致）
    assert md.index("出单前确认一下关联项目") < md.index("1. 摇人吧服务号") < md.index("回复【序号】")
    # 无 metadata 的轮次不受影响
    assert "已确认项目为摇人吧服务号。" in md


if __name__ == "__main__":
    for name, fn in sorted({k: v for k, v in globals().items() if k.startswith("test_")}.items()):
        fn()
        print(f"{name} OK")
