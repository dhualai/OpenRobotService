# -*- coding: utf-8 -*-
"""自动发布说明生成脚本回归测试：7 天窗口 / 内部改动过滤 / LLM 输出解析 / 渲染兼容。

运行：仓库根目录 `python -m pytest deploy/tests -q`
不依赖网络：LLM 调用一律 monkeypatch，不发真实请求。
"""
import json
from datetime import datetime, timedelta, timezone

import gen_release_notes as grn
import notify

NOW = datetime(2026, 10, 21, 12, 0, 0, tzinfo=timezone.utc)


def _iso(delta_days):
    return (NOW - timedelta(days=delta_days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pr(number, title, merged_days_ago=1.0, labels=None):
    return {
        "number": number,
        "title": title,
        "mergedAt": _iso(merged_days_ago),
        "labels": labels or [],
    }


def _write_prs(tmp_path, prs):
    import json
    p = tmp_path / "prs.json"
    p.write_text(json.dumps(prs, ensure_ascii=False), encoding="utf-8")
    return str(p)


# ---------- 7 天窗口 ----------

def test_window_keeps_last_seven_days(tmp_path):
    prs = [
        _pr(1, "feat: 窗口内的功能", 0.5),
        _pr(2, "feat: 刚好 7 天内", 6.9),
        _pr(3, "feat: 超过 7 天", 7.1),
        _pr(4, "feat: 很久以前", 30),
    ]
    got = grn.load_prs(_write_prs(tmp_path, prs), now=NOW)
    assert [p["number"] for p in got] == [1, 2]


def test_window_ignores_missing_or_bad_time(tmp_path):
    prs = [
        {"number": 1, "title": "feat: 缺 mergedAt"},
        {"number": 2, "title": "feat: 时间非法", "mergedAt": "not-a-date"},
        {"number": 3, "title": "feat: 正常", "mergedAt": _iso(1)},
    ]
    got = grn.load_prs(_write_prs(tmp_path, prs), now=NOW)
    assert [p["number"] for p in got] == [3]


def test_load_prs_tolerates_missing_or_broken_file(tmp_path):
    assert grn.load_prs(str(tmp_path / "nope.json"), now=NOW) == []
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert grn.load_prs(str(bad), now=NOW) == []
    obj = tmp_path / "obj.json"
    obj.write_text('{"number": 1}', encoding="utf-8")
    assert grn.load_prs(str(obj), now=NOW) == []


# ---------- 内部改动过滤 ----------

def test_filters_non_user_facing_prefixes():
    for title in ("chore: 清理脚本", "docs: 补文档", "ci: 调流水线",
                  "refactor: 拆函数", "test: 加用例", "build: 升依赖",
                  "style: 格式化", "revert: 撤销某改动"):
        assert grn.is_user_facing({"title": title}) is False, title


def test_keeps_feat_fix_and_plain_chinese_titles():
    for title in ("feat: 新功能", "fix(ORS-1): 修问题",
                  "feat(scope)!: 破坏性变更", "把首页公告改成置顶"):
        assert grn.is_user_facing({"title": title}) is True, title


def test_filters_skip_labels_regardless_of_shape():
    assert grn.is_user_facing(
        {"title": "feat: x", "labels": [{"name": "skip-release-notes"}]}) is False
    assert grn.is_user_facing({"title": "feat: x", "labels": ["dependencies"]}) is False
    assert grn.is_user_facing({"title": "feat: x", "labels": [{"name": "frontend"}]}) is True


def test_dedupes_same_number_and_title(tmp_path):
    prs = [_pr(1, "feat: 同一条", 1), _pr(1, "feat: 同一条", 1)]
    got = grn.load_prs(_write_prs(tmp_path, prs), now=NOW)
    assert len(got) == 1


# ---------- LLM 输出解析 ----------

def test_parse_llm_json_plain_and_fenced():
    plain = '{"新增功能": ["甲", "乙"], "体验优化": [], "BUG 修复": ["丙"]}'
    assert grn.parse_llm_json(plain) == {"新增功能": ["甲", "乙"], "BUG 修复": ["丙"]}

    fenced = "好的，结果如下：\n```json\n" + plain + "\n```\n以上。"
    assert grn.parse_llm_json(fenced) == {"新增功能": ["甲", "乙"], "BUG 修复": ["丙"]}


def test_parse_llm_json_rejects_unknown_groups_and_bad_shapes():
    assert grn.parse_llm_json('{"重构": ["不该出现"]}') == {}
    assert grn.parse_llm_json('{"新增功能": {"a": 1}}') == {}
    assert grn.parse_llm_json("完全不是 JSON") == {}
    assert grn.parse_llm_json("") == {}
    assert grn.parse_llm_json(None) == {}


def test_parse_llm_json_cleans_items_and_caps_groups():
    raw = ('{"新增功能": ["- 1. 含 `代码` 与 *星号* 的条目", "含 `代码` 与 *星号* 的条目", '
           + ", ".join(f'"第{i}条"' for i in range(10)) + "]}"
           )
    groups = grn.parse_llm_json(raw)
    items = groups["新增功能"]
    assert len(items) == grn.MAX_ITEMS_PER_GROUP          # 每组封顶
    assert items[0] == "含 代码 与 星号 的条目"            # 去掉列表符号与 Markdown 控制符
    assert len(set(items)) == len(items)                  # 去重


def test_clean_item_truncates_and_flattens():
    assert grn.clean_item("第一行\n第二行") == "第一行 第二行"
    assert len(grn.clean_item("长" * 200)) == grn.MAX_ITEM_CHARS
    assert grn.clean_item(123) == ""


# ---------- 渲染与 notify 解析兼容 ----------

def test_render_markdown_is_parseable_by_notify(tmp_path):
    groups = {"新增功能": ["新增甲"], "体验优化": ["优化乙"], "BUG 修复": ["修复丙"]}
    md = grn.render_markdown(groups)
    p = tmp_path / "RELEASE_NOTES.md"
    p.write_text(md, encoding="utf-8")
    assert notify.parse_release_notes(str(p)) == [
        ("新增功能", ["新增甲"]),
        ("体验优化", ["优化乙"]),
        ("BUG 修复", ["修复丙"]),
    ]


def test_render_markdown_skips_empty_groups(tmp_path):
    md = grn.render_markdown({"BUG 修复": ["只修了一个问题"]})
    p = tmp_path / "RELEASE_NOTES.md"
    p.write_text(md, encoding="utf-8")
    assert notify.parse_release_notes(str(p)) == [("BUG 修复", ["只修了一个问题"])]


# ---------- 退出码与写盘 ----------

def test_main_skips_without_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("RELEASE_LLM_API_KEY", raising=False)
    out = tmp_path / "out.md"
    prs = _write_prs(tmp_path, [_pr(1, "feat: x", 1)])
    assert grn.main(["--prs", prs, "--out", str(out)]) == 2
    assert not out.exists()


def test_main_skips_when_no_pr_in_window(tmp_path, monkeypatch):
    monkeypatch.setenv("RELEASE_LLM_API_KEY", "k")
    out = tmp_path / "out.md"
    prs = _write_prs(tmp_path, [_pr(1, "feat: 太老了", 30)])
    assert grn.main(["--prs", prs, "--out", str(out)]) == 2
    assert not out.exists()


def test_main_returns_1_when_llm_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("RELEASE_LLM_API_KEY", "k")
    monkeypatch.setattr(grn, "call_llm", lambda *a, **kw: "")
    out = tmp_path / "out.md"
    prs = _write_prs(tmp_path, [_pr(1, "feat: x", 1)])
    assert grn.main(["--prs", prs, "--out", str(out)]) == 1
    assert not out.exists()


def test_main_returns_2_when_llm_output_unusable(tmp_path, monkeypatch):
    monkeypatch.setenv("RELEASE_LLM_API_KEY", "k")
    monkeypatch.setattr(grn, "call_llm", lambda *a, **kw: '{"重构": ["不算数"]}')
    out = tmp_path / "out.md"
    prs = _write_prs(tmp_path, [_pr(1, "feat: x", 1)])
    assert grn.main(["--prs", prs, "--out", str(out)]) == 2
    assert not out.exists()


def test_main_writes_file_on_success(tmp_path, monkeypatch):
    monkeypatch.setenv("RELEASE_LLM_API_KEY", "k")
    monkeypatch.setattr(grn, "call_llm", lambda *a, **kw: (
        '{"新增功能": ["新增甲"], "BUG 修复": ["修复丙"]}'))
    out = tmp_path / "out.md"
    prs = _write_prs(tmp_path, [_pr(1, "feat: 甲", 1), _pr(2, "fix: 丙", 2)])
    assert grn.main(["--prs", prs, "--out", str(out)]) == 0
    groups = notify.parse_release_notes(str(out))
    assert groups == [("新增功能", ["新增甲"]), ("BUG 修复", ["修复丙"])]
    assert "自动生成" in out.read_text(encoding="utf-8")


# ---------- 地址与 SSRF 校验 ----------

def test_chat_endpoint_appends_v1_once():
    assert grn.chat_endpoint("https://api.deepseek.com") == \
        "https://api.deepseek.com/v1/chat/completions"
    assert grn.chat_endpoint("https://yitongapi.com/v1/") == \
        "https://yitongapi.com/v1/chat/completions"
    assert grn.chat_endpoint("") == "https://api.deepseek.com/v1/chat/completions"


def test_validate_endpoint_rejects_internal_and_plain_http():
    grn.validate_endpoint("https://api.deepseek.com/v1/chat/completions")   # 公网 https 放行
    for bad in ("http://api.deepseek.com/v1/chat/completions",
                "https://127.0.0.1/v1/chat/completions",
                "https://10.0.0.8/v1/chat/completions",
                "https://192.168.1.2/v1/chat/completions",
                "file:///etc/passwd"):
        try:
            grn.validate_endpoint(bad)
        except ValueError:
            continue
        raise AssertionError(f"应拒绝：{bad}")


# ---------- LLM 调用与输出预算 ----------

class _FakeResp:
    def __init__(self, payload):
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_call_llm_flags_empty_content_instead_of_silent_fallback(monkeypatch, capsys):
    """推理模型预算耗尽时 finish_reason=length 且 content 为空。

    这种「HTTP 200 但没有正文」的失败必须留痕，否则线上表现只是悄悄回退人工版，
    排查时看不出任何线索。
    """
    sent = {}

    def fake_urlopen(req, timeout=None):
        sent["body"] = json.loads(req.data.decode("utf-8"))
        return _FakeResp({"choices": [{"message": {"content": ""},
                                       "finish_reason": "length"}]})

    monkeypatch.setattr(grn.urllib.request, "urlopen", fake_urlopen)
    assert grn.call_llm("p", api_key="k", base_url="https://api.deepseek.com") == ""
    out = capsys.readouterr().out
    assert "content 为空" in out and "length" in out
    # 预算要盖得住推理模型的 reasoning_tokens（曾因 1500 全部被 reasoning 吃掉而永远拿不到正文）
    assert sent["body"]["max_tokens"] == grn.MAX_TOKENS >= 4000


def test_call_llm_returns_content_on_success(monkeypatch):
    def fake_urlopen(req, timeout=None):
        return _FakeResp({"choices": [{"message": {"content": '{"新增功能": ["甲"]}'},
                                       "finish_reason": "stop"}]})

    monkeypatch.setattr(grn.urllib.request, "urlopen", fake_urlopen)
    assert grn.call_llm("p", api_key="k", base_url="https://api.deepseek.com") == \
        '{"新增功能": ["甲"]}'


def test_prompt_caps_items_per_group():
    """输出条数要贴着通知图的容量，别让 LLM 产出必然被截断的内容。"""
    prompt = grn.build_prompt([{"number": 1, "title": "feat: x"}])
    assert f"每组最多 {grn.MAX_ITEMS_PER_GROUP} 条" in prompt


# ---------- 视觉边界哨兵 ----------

def test_worst_case_notes_still_render_as_image():
    """每组上限 × 每条字数上限的最坏组合仍须落在 notify_image 的 2MB / 高度限制内。

    上限一旦放宽（比如条目数回到 6、字数回到 60），实测渲染会到 2.7MB，
    触发 NotifyImageError 降级成模板卡片——这条用例就是那个边界的哨兵。
    """
    pytest = __import__("pytest")
    pytest.importorskip("PIL")
    import notify_image

    item = ("这是一个非常长的功能描述文字用来测试最坏情况下的换行与图片体积，"
            "再补一些字凑满四十个字符上限") * 2
    item = item[:grn.MAX_ITEM_CHARS - 1]
    groups = {name: [item + str(i) for i in range(grn.MAX_ITEMS_PER_GROUP)]
              for name in grn.RELEASE_GROUPS}
    ctx = {
        "ok": True, "title": "部署成功", "env_name": "prod", "env_label": "生产",
        "components": "all", "git_ref": "dev", "sha": "abcdef12",
        "actor": "tester", "event": "手动触发", "elapsed": "2 分 10 秒",
        "release_date": "2026-10-21",
        "release_notes": [(k, v) for k, v in groups.items()],
        "layout": "release",
        "run_url": "https://github.com/example/actions/runs/1",
    }
    png = notify_image.render_png(ctx)
    assert png[:4] == b"\x89PNG"
    assert len(png) <= 2 * 1024 * 1024
