"""报告快照：同进程内拿得到、过期/淘汰/空值拿不到。"""

import time

from ai.agents.AiTaskPlatform.runtime import report_cache


def test_roundtrip_by_task_id():
    report_cache.clear()
    report_cache.put(44123, "  # 诊断报告\n根因: 相机标定漂移  ")
    assert report_cache.get(44123) == "# 诊断报告\n根因: 相机标定漂移"
    assert report_cache.get("44123") == "# 诊断报告\n根因: 相机标定漂移"
    assert report_cache.get(99999) is None


def test_blank_input_does_not_occupy_a_slot():
    report_cache.clear()
    report_cache.put("", "内容")
    report_cache.put(1, "   ")
    assert report_cache.get(1) is None
    assert report_cache.get("") is None


def test_expired_snapshot_is_gone():
    report_cache.clear()
    report_cache.put(1, "旧报告")
    # 直接把写入时间推回 TTL 之前
    ts, body = report_cache._snapshots["1"]
    report_cache._snapshots["1"] = (ts - report_cache.TTL_SECONDS - 1, body)
    assert report_cache.get(1) is None, "过期后必须拿不到，让它走『报告不落库』的提示"


def test_oldest_entry_evicted_over_cap():
    report_cache.clear()
    for i in range(report_cache.MAX_ENTRIES + 5):
        report_cache.put(i, f"报告{i}")
    assert len(report_cache._snapshots) == report_cache.MAX_ENTRIES
    assert report_cache.get(0) is None, "最早的先淘汰"
    assert report_cache.get(report_cache.MAX_ENTRIES + 4) == f"报告{report_cache.MAX_ENTRIES + 4}"


def test_rewrite_refreshes_ttl_and_keeps_newest():
    report_cache.clear()
    report_cache.put(1, "第一版")
    ts, _ = report_cache._snapshots["1"]
    report_cache._snapshots["1"] = (ts - report_cache.TTL_SECONDS + 60, "第一版")
    report_cache.put(1, "第二版")
    assert report_cache.get(1) == "第二版"
    assert time.time() - report_cache._snapshots["1"][0] < 5, "重写应刷新时间戳"


def test_body_is_capped():
    report_cache.clear()
    report_cache.put(1, "x" * (report_cache.MAX_CHARS + 500))
    assert len(report_cache.get(1)) == report_cache.MAX_CHARS
