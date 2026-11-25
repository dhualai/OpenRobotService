import json, sys
from unittest import mock

sys.path.insert(0, r"D:\OpenRobotService\wt\ors1149fix2\.github\ai-review\scripts")
import importlib

import post_comment

importlib.reload(post_comment)


def test_sync_roundtrip(tmp_path):
    inline = tmp_path / "inline.json"
    inline.write_text(json.dumps([
        {"path": "a.py", "line": 5, "level": "blockers",
         "body": "<!-- ai-review-inline -->\n🔴 **AI 检视**：x"},
        {"path": "b.py", "line": 9, "level": "blockers",
         "body": "<!-- ai-review-inline -->\n🔴 **AI 检视**：y"},
    ]), encoding="utf-8")

    calls = []

    def fake_gh(repo, path, token, method="GET", body=None):
        calls.append((method, path))
        if path.endswith("pulls/509/comments?") or "pulls/509/comments?" in path:
            # 已有一条旧位置评论（c.py:3），将被清理
            return 200, [{"id": 1, "path": "c.py", "line": 3,
                          "body": "<!-- ai-review-inline -->\n旧"}]
        if method == "POST" and path.endswith("comments"):
            return 201, {}
        if method == "PATCH":
            return 200, {}
        if method == "DELETE":
            return 204, {}
        return 200, {}

    with mock.patch.object(post_comment, "gh_api", side_effect=fake_gh):
        post_comment.sync_inline_comments(
            "dhualai/OpenRobotService", 509, "t", "abc123", str(inline))

    methods = [m for m, _ in calls]
    assert methods.count("DELETE") == 1   # 旧位置清理
    assert methods.count("POST") == 2     # 两个新位置创建
    assert methods.count("PATCH") == 0    # 无重叠位置


def test_sync_update_existing(tmp_path):
    inline = tmp_path / "inline.json"
    inline.write_text(json.dumps([
        {"path": "a.py", "line": 5, "level": "blockers",
         "body": "<!-- ai-review-inline -->\n🔴 新内容"},
    ]), encoding="utf-8")

    def fake_gh(repo, path, token, method="GET", body=None):
        calls.append((method, path))
        if path.endswith("pulls/509/comments?") or "pulls/509/comments?" in path:
            return 200, [{"id": 7, "path": "a.py", "line": 5,
                          "body": "<!-- ai-review-inline -->\n旧内容"}]
        if method == "PATCH":
            return 200, {}
        return 200, {}

    calls = []
    with mock.patch.object(post_comment, "gh_api", side_effect=fake_gh):
        post_comment.sync_inline_comments("dhualai/OpenRobotService", 509, "t", "abc", str(inline))
    assert ("PATCH", "pulls/comments/7") in calls
    assert not any(m == "POST" or m == "DELETE" for m, _ in calls)
