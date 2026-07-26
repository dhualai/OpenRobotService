# UI 测试用例清单

> 格式按 [template-test-case.md](template-test-case.md)

---

## Login Page

### UI-TC-001 — test_login_page_loads

**属性：** 优先级 P0 · 自动化 · 冒烟 是 · 功能点 登录页加载

**测试点：** 验证登录页正常加载且 URL 含 /login

**前置条件：** Playwright 浏览器可用；前端已启动

**测试步骤：**
1. page.goto(login_url)
2. 验证 page.url 包含 /login
3. 验证登录表单可见

**结果：** PASS

---

## Login Page

### UI-TC-002 — test_login_empty_fields_stays_on_login

**属性：** 优先级 P0 · 自动化 · 冒烟 是 · 功能点 空字段提交

**测试点：** 验证空字段提交后页面不跳转，仍停留在登录页

**前置条件：** Playwright 浏览器可用；前端已启动

**测试步骤：**
1. page.goto(login_url)
2. 不填用户名/密码，点击登录
3. 验证页面 URL 仍含 /login

**结果：** PASS

---

## Login Page

### UI-TC-003 — test_login_invalid_credentials

**属性：** 优先级 P0 · 自动化 · 冒烟 是 · 功能点 无效凭据

**测试点：** 验证无效凭据提交后不跳转

**前置条件：** Playwright 浏览器可用；前端已启动

**测试步骤：**
1. page.goto(login_url)
2. 填入无效用户名/密码，点击登录
3. 验证页面不跳转

**结果：** PASS

---
