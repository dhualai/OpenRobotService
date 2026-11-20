"""评论附件收集：空评论不得挤掉更早的日志。"""
from ai.core.task_adapter import (
    _COMMENT_ATTACHMENT_LIMIT,
    _normalize_comment_attachment_items,
    _raw_comment_attachment_items,
)


def test_skip_empty_and_temp_id():
    assert _raw_comment_attachment_items(None) == []
    assert _raw_comment_attachment_items([]) == []
    assert _raw_comment_attachment_items(["   "]) == []
    assert _raw_comment_attachment_items(["a1b2c3d4-e5f6-7890-abcd-ef1234567890"]) == []
    keep = _raw_comment_attachment_items([
        "helpdesk-comment/x/logs_20260824_180313.zip",
        {"filename": "a.log", "object_path": "helpdesk-comment/y/a.log"},
    ])
    assert len(keep) == 2
    assert keep[0].endswith("logs_20260824_180313.zip")


def test_older_logs_survive_many_empty_comments():
    class Row:
        def __init__(self, attachments):
            self.attachments = attachments

    # 最新 40 条空评论 + 更早的两条日志（查询顺序：新 → 旧）
    rows = [Row([]) for _ in range(40)]
    rows.append(Row(["helpdesk-comment/t1/logs_20260824_180313.zip"]))
    rows.append(Row(["helpdesk-comment/t2/logs_20260824_180319.zip"]))

    raw = []
    with_files = 0
    for c in rows:
        items = _raw_comment_attachment_items(c.attachments)
        if not items:
            continue
        with_files += 1
        raw.extend(items)
        if len(raw) >= _COMMENT_ATTACHMENT_LIMIT:
            break
    assert with_files == 2
    names = _normalize_comment_attachment_items(raw, presign=False)
    assert [n["filename"] for n in names] == [
        "logs_20260824_180313.zip",
        "logs_20260824_180319.zip",
    ]
