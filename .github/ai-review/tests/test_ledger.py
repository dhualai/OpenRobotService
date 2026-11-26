# -*- coding: utf-8 -*-
"""ledger.py 台账生成：判定表解析与准确率聚合的单测。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import ledger  # noqa: E402


def test_load_verdicts_parse(tmp_path):
    md = """# AI Review 误报台账（观察期）

## 观察期意见台账

| 日期 | PR | 级别 | 意见摘要 | 判定（有效/误报/无法判定） | 备注 |
|---|---|---|---|---|---|
| 2026-11-23 | #492 | 高危(快筛) | 删列风险+疑似密钥 ×3 | 误报 | 自命中 |
| 2026-11-23 | #492 | 高危(快筛) | ai-review.yml 工作流提醒 | 有效 | 信息性 |
| 2026-11-23 | #501 | 高危(快筛) | workflow 变更 ×2 | 有效 | 提示成立 |
| 2026-11-23 | #495 | 高危(AI) | UTC 锚点提醒 | 有效 | 方向正确 |
| | | | | | |

## 观察期小结
"""
    p = tmp_path / "evaluation.md"
    p.write_text(md, encoding="utf-8")
    v = ledger.load_verdicts(str(p))
    assert v[492]["快筛"] == [("误报", 3), ("有效", 1)]
    assert v[501]["快筛"] == [("有效", 2)]
    assert v[495]["AI"] == [("有效", 1)]      # 级别列含 AI → AI 层


def test_render_accuracy_weighted():
    verdicts = {492: {"快筛": [("误报", 3), ("有效", 1)]},
                501: {"快筛": [("有效", 2)]},
                516: {"快筛": [("误报", 1)]}}
    data = [{"pr": 492, "created": "2026-11-23T06:08", "runs": 2,
             "sentinel": 0, "report": True,
             "items": [{"level": "快筛", "path": "a.yml",
                        "desc": "工作流提醒"}]},
            {"pr": 501, "created": "2026-11-23T10:03", "runs": 1,
             "sentinel": 0, "report": True, "items": []},
            {"pr": 516, "created": "2026-11-25T04:54", "runs": 1,
             "sentinel": 0, "report": True, "items": []}]
    block = ledger.render(data, "2026-11-23T06:00Z", verdicts)
    assert "有效 3 / 误报 4 = 43%" in block   # (1+2)/(3+1+2+1) 按条数加权
    assert "误报（×3） / 有效（×1）" in block   # 流水行拼接多批次判定
    assert "#516 | 快筛" not in block          # 无 items 的 PR 不进流水
    assert "快筛自检率 | 3/3 = 100%" in block
