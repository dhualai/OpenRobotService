# -*- coding: utf-8 -*-
"""requested_assignee 真名判定单测（0910 #719 实锤：平台名「服务号」被填成处理人）。

判定语义：真名 → build_ticket 走「指定处理人」硬指派（描述前缀+special_notes）；
非真名（职位/描述性指名）不丢弃 → 降级为「派单参考」进 special_notes。
"""
from ai.agents.AiDiagnosisPlatform.pipeline import _assignee_is_real

_UMAP = {"user_1": "张三", "user_2": "毛梦晴", "user_9c63aead": "hujiannan"}


def test_real_display_name_passes():
    assert _assignee_is_real("张三", _UMAP)
    assert _assignee_is_real("毛梦晴", _UMAP)


def test_id_form_passes():
    assert _assignee_is_real("user_1", _UMAP)


def test_platform_name_not_real():
    # 平台名匹配不到 → 不走硬指派（#719 场景；由调用方降级为派单参考）
    assert not _assignee_is_real("服务号", _UMAP)
    assert not _assignee_is_real("摇人吧服务号", _UMAP)


def test_job_titles_not_real_but_kept_as_hint():
    # 职位/描述性指名：判定非真名（走派单参考），不是丢弃——此处只测判定
    for hint in ("产品经理", "负责地图编辑前端的人", "工程师", "客服"):
        assert not _assignee_is_real(hint, _UMAP)


def test_empty_and_blank():
    assert not _assignee_is_real("", _UMAP)
    assert not _assignee_is_real("   ", _UMAP)


def test_whitespace_stripped():
    assert _assignee_is_real("  张三  ", _UMAP)


def test_partial_name_not_matched():
    # 「张」不等于「张三」——精确匹配防误放
    assert not _assignee_is_real("张", _UMAP)
