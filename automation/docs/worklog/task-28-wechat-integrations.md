# task-28 · wechat + integrations 模块用例补齐

## 本次目标

补齐 wechat（27 条）与 integrations（16 条）模块用例，消除最大覆盖缺口。范围经 grill 确认：P0+P1 全做（可离线断言的纯后端/契约级接口），mock 实现真实语义。

## 已确认决策（grill）

Q1 P0+P1 全做 | Q2 回调验签实现真实语义 | Q3 独立目录 | Q4 X-API-Key 固定 key 模拟

## 调研要点（子代理盘点 backend 真实契约）

- wechat 27 接口：admin_auth 因 DEBUG_MODE=True 当前不生效（鉴权空操作）；回调验签是本地 sha1 可离线算；tag/menu/message 执行层依赖微信但入口校验是后端逻辑
- integrations：sources 用 X-API-Key 鉴权（缺 key 一律 401）；wecom/projects/sync 用 JWT；task-user-mappings 纯 DB
- mock 差异：GET/POST /api/wechat 语义错位（标签 vs 回调）、get_menu/create_menu/send_message 响应信封不同、无 tag 系列、/api/integrations 假路径、mappings 字段语义不同

## 修改文件列表

- `automation/src/mocks/backend_mock.py`：
  - `_route_wechat` 重写：回调验签（sha1 本地计算，GET 返回 echostr/403）、POST XML 回复；新增 login/permissions/tag 系列（8 路由含 batch 100 限制）/delete_menu/broadcast/send_link/webnotify/import-data/notify
  - `_handle_callback`、`_route_wechat_tag` 新增
  - `_route_sources` 新增：X-API-Key 鉴权（固定 key test-api-key）+ sources 列表 + {source}/sync（未注册 404）+ wecom/projects/sync（JWT）；删除假 /api/integrations
  - `task-user-mappings`：字段语义修正（source/external_account/local_user_id）+ PUT/DELETE + 409 冲突
  - handle() 路由顺序：sources 提前于 /api/tasks；seed 补充 tag[1]、zentao 源
- 新增 `automation/tests/wechat/test_wechat_code.py`（27 条：登录/权限/标签 10/菜单 3/消息 7/导入 3）
- 新增 `automation/tests/integrations/test_integrations_code.py`（16 条：映射 9/任务源 5/企微同步 2）
- 修改 `automation/tests/auth/test_auth_code.py`：TestWechat 改为回调验签语义（验签成功/失败 403/XML 回复），新增文本断言 assert_contains/assert_equals
- 新增 3 份用例清单（case-inventory-wechat/integrations/auth.md）
- 新增 `automation/docs/worklog/task-28-wechat-integrations.md`（本文）

## 测试结果

```
# 全量
382 passed, 28 skipped in 13.70s

# API 用例（含新模块）
174 passed（wechat 27 + integrations 16 + call 41 + tasks 32 + admin 45 + auth 13）
```

## Allure 报告

已生成：`automation/output/allure-report/index.html`（174 条，六模块）

## 过程问题

- mappings 路由 `len(rest) > 21` 边界 bug（/task-user-mappings/1 长度恰为 21）
- sources 被 /api/tasks 前缀提前拦截（路由顺序）
- {source}/sync 解析含 /sync 后缀（正则修正）
- tag 无 seed 导致 404（补 seed tag[1]）
- login 用例 expected_fields={'token': None} 断言错误（去掉字段断言）

## 下一步

- P1 DB 集成测试接 CI / 真实后端验证（等 MySQL 地址）
- admin 附加接口（dashboard/日报读改删/permissions）可继续补齐
- 提交 git
