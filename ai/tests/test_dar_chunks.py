# -*- coding: utf-8 -*-
"""dar 链纯函数单测：retrieval ctx → chunks 解析。

覆盖 dar_retrieval_check.parse_retrieval_chunks 的块形态：
pipeline._retrieve_with_context 产出 `---\n{emoji路别} N（标题）：\n内容\n---`，
另有无编号的 🚗 提示块。跑法：python -m pytest ai/tests/test_dar_chunks.py
"""
import importlib.util
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "..", "scripts", "dar_retrieval_check.py")

_spec = importlib.util.spec_from_file_location("drc", _SRC)
drc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(drc)


def test_normal_blocks_with_title():
    ctx = ("---\n📖 手册 1（RXX 上线流程）：\n1. 打开页面\n2. 点上线\n---\n"
           "---\n📋 FAQ 2：\nQ: 怎么重置\nA: 设置里点重置\n---")
    out = drc.parse_retrieval_chunks(ctx)
    assert [c["route"] for c in out] == ["📖 手册", "📋 FAQ"]
    assert out[0]["title"] == "RXX 上线流程"
    assert out[1]["title"] == ""
    assert "打开页面" in out[0]["text"]
    assert "重置" in out[1]["text"]


def test_hint_block_without_index():
    """🚗 提示块无编号：route=冒号前文本，冒号后内容并入 text 不丢。"""
    ctx = "---\n🚗 提示：数字 [E101] 是相关知识而非错误码。\n---"
    out = drc.parse_retrieval_chunks(ctx)
    assert len(out) == 1
    assert out[0]["route"] == "🚗 提示"
    assert "E101" in out[0]["text"]


def test_empty_and_none():
    assert drc.parse_retrieval_chunks("") == []
    assert drc.parse_retrieval_chunks(None) == []
    assert drc.parse_retrieval_chunks("---\n---\n---") == []


def test_cap_and_limit():
    """text 截断 200 字 + 最多 6 块（标注工具 html 体积控制）。"""
    ctx = "---\n📄 知识库 1：\n" + "长" * 500 + "\n---"
    out = drc.parse_retrieval_chunks(ctx)
    assert len(out[0]["text"]) == 200
    many = "\n".join(f"---\n📄 知识库 {i}：\n内容{i}\n---" for i in range(10))
    assert len(drc.parse_retrieval_chunks(many)) == 6


def test_env_paths():
    """DAR_ENV 隔离：test/prod 的 OUT 与 MANUAL 互不串（目录隔离关键断言）。"""
    assert drc.OUT.endswith("export_dar\\test\\processed") or \
        drc.OUT.endswith("export_dar/test/processed")
    assert "prod" not in drc.OUT
    assert drc.MANUAL.endswith("manual_segmentation.json")
