# -*- coding: utf-8 -*-
"""requested_assignee 防幻觉闸门单测（0910 #719 实锤：平台名「服务号」被填成处理人）。"""
from ai.agents.AiDiagnosisPlatform.pipeline import _assignee_is_real

_UMAP = {"user_1": "张三", "user_2": "毛梦晴", "user_9c63aead": "hujiannan"}


def test_real_display_name_passes():
    assert _assignee_is_real("张三", _UMAP)
    assert _assignee_is_real("毛梦晴", _UMAP)


def test_id_form_passes():
    assert _assignee_is_real("user_1", _UMAP)


def test_platform_name_blocked():
    assert not _assignee_is_real("服务号", _UMAP)
    assert not _assignee_is_real("摇人吧服务号", _UMAP)


def test_generic_titles_blocked():
    for bad in ("工程师", "客服", "AI", "平台"):
        assert not _assignee_is_real(bad, _UMAP)


def test_empty_and_blank():
    assert not _assignee_is_real("", _UMAP)
    assert not _assignee_is_real("   ", _UMAP)


def test_whitespace_stripped():
    assert _assignee_is_real("  张三  ", _UMAP)


def test_partial_name_not_matched():
    # 「张」不等于「张三」——精确匹配防误放
    assert not _assignee_is_real("张", _UMAP)
