# UI 测试用例清单

> 1 个 Page Object，3 个测试用例。详情见 [ui-test-plan.md](ui-test-plan.md)。

| # | 测试函数 | 覆盖点 |
|---|---------|--------|
| 1 | test_login_page_loads | 登录页加载，URL 含 /login |
| 2 | test_login_empty_fields_stays_on_login | 空字段提交后仍在登录页 |
| 3 | test_login_invalid_credentials | 无效凭据后不跳转 |

**合计：3 用例 ✅**

## 待实现

| 功能 | 估算用例数 | 优先级 |
|------|-----------|--------|
| AdminLoginPage PO | 3 | P1 |
| TaskBoardPage PO（工单列表/筛选/详情）| 5 | P1 |
| WeChatH5Page PO | 3 | P2 |
| 移动端视口测试 | 4 | P2 |
