import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

from ai.core import archive_flow


def test_prompt_requires_eight_report_sections_and_no_invented_facts():
    headings = ('问题概述', '问题场景', '复现条件', '原因分析', '解决方案', '验证结果', '适用范围', '遗留问题')
    for heading in headings:
        assert f'## {heading}' in archive_flow._REPORT_PROMPT
    assert '禁止编造' in archive_flow._SYSTEM_PROMPT
    assert '不写人员姓名' in archive_flow._REPORT_PROMPT


def test_background_writeback_does_not_overwrite_submitted_report(monkeypatch):
    database = MagicMock()
    database.execute.return_value.rowcount = 0
    monkeypatch.setitem(sys.modules, 'ai.core.database', SimpleNamespace(SessionLocal=lambda: database))
    assert not archive_flow._writeback_if_empty(7, '生成的报告')
    statement, values = database.execute.call_args.args
    assert "content = ''" in str(statement)
    assert "submit_status IN ('draft', 'rejected')" in str(statement)
    assert values == {'c': '生成的报告', 'rid': 7}
    database.close.assert_called_once()
