# UI 回归联合报告：真实测试环境 Smoke

> 状态：已确认，进入实现
> 日期：2026-09-19
> 环境：真实测试环境
> 数据约束：只读或幂等，不创建、修改、删除业务数据

## 1. 覆盖范围

每条 Smoke 对应一个接口，覆盖登录、摇人问答和系统任务三个模块。

| 用例 | 覆盖类型 | 角色 | 接口 | 预期 |
|---|---|---|---|---|
| 测试后端健康检查 | 正常流程 | 无 | `GET /api/health` | HTTP 200，`status=healthy` |
| U1 登录成功 | 正常流程 | U1 | `POST /api/auth/login` | HTTP 200，返回 `access_token` |
| U1 获取当前用户 | 正常流程 | U1 | `GET /api/auth/me` | HTTP 200，用户名为 `u1_auto` |
| U1 查询摇人会话列表 | 正常流程 | U1 | `GET /api/call/conversations?scene_type=chat&limit=10` | HTTP 200，返回数组 |
| U1 查询可访问工单 | 正常流程 | U1 | `POST /api/tasks/filter` | HTTP 200，返回 `items/total` |
| U1 查询可指派人员 | 正常流程 | U1 | `GET /api/tasks/assignable-users?skip=0&limit=10` | HTTP 200，返回数组 |

## 2. 不覆盖内容

- 登录失败、参数校验和权限异常。
- 创建会话、发送问题、生成或提交工单。
- 接单、推进阶段、已解决和关闭等写状态接口。
- 工单清理和数据库直接操作。

上述写状态接口由 UI 完整业务链路覆盖。

## 3. 报告分类

```text
单接口用例
└── 真实测试环境 Smoke
    ├── 登录
    ├── 摇人问答
    └── 系统任务
```

## 4. 断言与附件

- 每个接口断言 HTTP 状态码和稳定响应字段。
- Allure 步骤显示 `METHOD /path -> HTTP status`。
- `接口结果` 记录状态码、耗时和请求定位。
- `响应摘要` 只记录安全、关键的响应字段。
- `断言结果` 记录断言名称、期望值、实际值和通过/失败。
- 不记录 Authorization、access_token、Cookie 或完整用户数据。

## 5. 运行前置条件

- UI 回归本地 Gateway 和两条 SSH 隧道可用。
- U1 凭据已通过环境变量提供。
- 自动化 AI 不需要响应 Smoke 请求，但 UI 场景运行时仍需可用。
