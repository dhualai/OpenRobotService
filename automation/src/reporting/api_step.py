"""统一的 API 步骤封装。

职责边界：
- 请求 / 响应附件由 `ApiClient` 自动附加（含敏感字段脱敏）
- 断言明细由 `assertions.report` 汇总成单条「断言信息」附件
- 本模块只负责统一 step 标题与断言触发，使各测试文件的 `_api` 行为一致，
  便于集中调整（例如后续把 step 标题改为业务化描述）。

`step` 传中文业务描述（如"提交建单请求"）；不传则回退为「方法 + 路径」。
"""

import allure

from automation.src.assertions import assert_dict_contains_subset, assert_status_code
from automation.src.assertions.report import flush_assert_attachment


async def api_step(client, method: str, path: str, step: str = "", headers=None,
                   expected_status: int | None = None, expected_fields: dict | None = None,
                   **kwargs):
    """执行一次 API 调用，包在 Allure step 内并触发断言记录。"""
    label = step or f"{method.upper()} {path}"
    with allure.step(label):
        r = await client.request(method, path, headers=headers, **kwargs)
        if expected_status is not None:
            assert_status_code(r, expected_status)
        if expected_fields:
            assert_dict_contains_subset(r.json(), expected_fields)
        flush_assert_attachment()
        return r
