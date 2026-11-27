"""业务摘要 markdown 的解析测试（供 Actions 运行摘要使用）。"""

from __future__ import annotations

from automation.src.reporting.business_report import business_summary_markdown

CARD = (
    '<details class="business-scenario" id="scenario-service-ticket-lifecycle">'
    '<summary><span class="module-name">我要摇人</span>'
    "<strong>提单 → 派单 → 处理 → 关闭</strong>"
    '<span class="business-status neutral">部分验证</span>'
    '<span class="coverage">P1 断言</span>'
    '<span class="business-snapshot">触发 · 提单<br>本次验证 · 有接口结果<br>卡点 · 页面闭环待验证</span>'
    "</summary><div class=\"business-body\"></div></details>"
)


def test_summary_lists_each_business_card():
    markdown = business_summary_markdown(CARD)
    assert "| 业务模块 | 主流程 | 结论 | 覆盖级别 | 卡点 |" in markdown
    assert "| 我要摇人 | 提单 → 派单 → 处理 → 关闭 | 部分验证 | P1 断言 | 页面闭环待验证 |" in markdown
    assert "test-report-business" in markdown


def test_summary_unescapes_entities():
    card = CARD.replace("我要摇人", "我要摇人 &lt;测试&gt;")
    markdown = business_summary_markdown(card)
    assert "我要摇人 <测试>" in markdown


def test_summary_without_cards_is_explicit():
    markdown = business_summary_markdown("<html><body>无卡片</body></html>")
    assert "未生成业务场景卡片" in markdown
