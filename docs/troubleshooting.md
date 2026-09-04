# 常见问题及排查方法

> 本文收集 OpenRobotService 项目在开发、测试、部署过程中遇到的常见问题和排查方法。
> 内容基于实际项目代码和技术栈，持续补充。

---

## 一、测试环境问题

### 1.1 pytest 收集测试时报 ImportError

**现象**：
```
ImportError while importing test module '...\test_xxx.py'.
ModuleNotFoundError: No module named 'xxx'
```

**原因**：
`backend/app/__init__.py` 在 import 时执行 `Base.metadata.create_all()` 连 MySQL，且某些模块（如 `app.core.database`）在 import 时触发建表逻辑。`conftest.py` 通过占位模块机制阻止此行为，但某些测试文件的 import 链绕过了 conftest 的 mock。

**排查步骤**：
1. 检查测试文件的最顶层 import 链
2. 确认 conftest.py 的模块替换覆盖了所有 `from app.xxx import yyy` 路径
3. 临时方案：在 conftest.py 中添加额外的 `sys.modules` 占位

**常见场景**：

```
错误：ModuleNotFoundError: No module named 'fastapi'
→ 未安装依赖，运行 pip install -r requirements.txt -r requirements-test.txt

错误：ImportError: cannot import name 'db_manager' from 'app.core.database'
→ app.core.database 模块在导入时触发了真实 DB 连接，conftest 的 mock 未覆盖
→ 当前 tests/tasks/ 下的测试有该问题，跳过执行：pytest --ignore=tests/tasks
```

### 1.2 pytest-asyncio 模式警告

**现象**：
```
DeprecationWarning: The configuration option "asyncio_mode" will be ... 
```

**解决方案**：
在 `backend/pytest.ini` 中添加：
```ini
[pytest]
asyncio_mode = auto
```

### 1.3 Allure 报告无法生成

**现象**：
```
'allure' 不是内部或外部命令，也不是可运行的程序
```

**原因**：Allure CLI 未安装或不在 PATH 中。

**解决方案**：
1. 从 https://github.com/allure-framework/allure2/releases 下载 Allure CLI
2. 解压后将 `bin/` 目录加入系统 PATH
3. 确认 `allure --version` 可运行
4. Allure CLI 依赖 Java 17+，确保 `java -version` 可用

### 1.4 vitest 测试中使用 DOM API 失败

**现象**：
```
ReferenceError: localStorage is not defined
TypeError: window.matchMedia is not a function
```

**原因**：jsdom 未 mock `localStorage` 或 `matchMedia`。

**解决方案**：
`frontend/src/test/setup.ts` 已包含这两个 mock。如遇到新 API 未 mock：
```typescript
// 在 setup.ts 中添加
Object.defineProperty(window, 'xxx', { value: mockFn });
```

---

## 二、后端运行问题

### 2.1 数据库连接失败

**现象**：
```
sqlalchemy.exc.OperationalError: (pymysql.err.OperationalError) ... 
Can't connect to MySQL server on '127.0.0.1:3306'
```

**排查步骤**：
1. 检查 MySQL 服务是否运行：`Get-Service MySQL*`
2. 检查 `.env` 中的 `DATABASE_URL` 配置（端口、用户名、密码、库名）
3. 若使用 Docker MySQL 容器，确认容器在运行且端口映射正确
4. 注意 MySQL 8.0.13 有 `DEFAULT(now())` index bug，建议 8.0.14+ 或 8.4

### 2.2 Alembic 迁移失败

**现象**：
```
FAILED: alembic.command.MigrationError
```

**排查步骤**：
1. 确认 `DATABASE_URL` 指向正确的数据库
2. 确认数据库已创建：`CREATE DATABASE IF NOT EXISTS openrobotservice CHARACTER SET utf8mb4`
3. 检查迁移版本树：`alembic history`
4. 回退迁移：`alembic downgrade -1`

### 2.3 路由 404

**现象**：
访问 `/api/tasks/sources` 返回 404，但 `/api/tasks/{task_id}` 正常

**原因**：路由注册顺序问题。`integrations_sources_router` 必须在 `tasks_router` 之前注册，否则 `GET /tasks/sources` 会被 `GET /tasks/{task_id}` 贪婪匹配吞掉。

**修复**：在 `backend/app/__init__.py` 中调整路由注册顺序。

---

## 三、前端运行问题

### 3.1 代理不生效

**现象**：前端开发时 API 请求指向 `http://localhost:5173/api/...` 而非后端。

**排查步骤**：
1. 确认 `backend/main.py`（8400）和 `ai/run.py`（8401）都已启动
2. 检查 `vite.config.ts` 的 proxy 配置
3. 重启 vite dev server（修改 `vite.config.ts` 后必须重启）
4. 检查 `VITE_DEV_BACKEND_TARGET` / `VITE_DEV_AI_TARGET` 环境变量

### 3.2 构建后资源路径错误

**现象**：生产部署后 JS/CSS 文件 404

**原因**：`base` 路径配置与 nginx 分发路径不匹配。

| 构建命令 | base | 期望的 nginx 前缀 |
|----------|------|--------------------|
| `npm run build` | `/` | 直接 `/` |
| `npm run build:test` | `/t/app/` | nginx `location /t/` |
| `npm run build:prod` | `/p/app/` | nginx `location /p/` |

### 3.3 微信 OAuth 回跳 404

**现象**：微信授权后回跳页面显示 404

**原因**：`state` 参数中丢失了部署前缀（`/p/app/`）。

**修复**：后端 `app/wechat/api/wechat.py` 的 `resolve_callback_target()` 应优先按 base64url 解码 `state` 中的完整 URL 回跳。

### 3.4 antd DatePicker 浮层被编辑弹窗遮挡（React 19 兼容）

**现象**：编辑工单弹窗内「最晚解决时间」用 antd `DatePicker`，点击后日历/时间面板看不到或无法交互。

**原因**：两层叠加——(1) tdesign `Popup` 编辑弹窗 `z-index: 11500`（见 `tdesign-mobile-react/es/popup/style/index.css`），而 antd `DatePicker` 浮层默认 `z-index` 远低于此，浮层被编辑弹窗遮罩/内容盖住；(2) React 19 已移除 `findDOMNode`，antd v5 浮层依赖它挂载，缺 `@ant-design/v5-patch-for-react-19` 补丁时浮层直接不渲染。

**修复**：
- 入口 `main.tsx` 顶部 `import '@ant-design/v5-patch-for-react-19';`（React 19 必需，否则浮层不挂载）。
- `DatePicker` 设 `styles={{ popup: { root: { zIndex: 12000 } } }}`（> 11500），浮层浮在编辑弹窗之上。
- 废弃的 `popupStyle` 勿再用。

**涉及位置**：`frontend/src/pages/call/TicketDetailPage.tsx`、`frontend/src/pages/tasks/TaskDetailPage.tsx`。

### 3.5 详情页「最晚解决时间」回显消失（字段名蛇形/驼峰混淆）

**现象**：工单已写入 `deadline_at`，但历史工单详情页 / 系统任务详情页不显示「最晚解决时间」一行，编辑弹窗也不回显。

**原因**：tasks 详情接口 `GET /{id}` 的 `response_model=TicketResponse` 返回**蛇形 `deadline_at`**（见 `backend/app/modules/tasks/schemas/ticket.py:107`）。而 `ticket_service.py` 的 `FIELD_MAPPING` 里有 `'deadlineAt': (Ticket.deadline_at, ...)`，那是给 `POST /filter` 复合过滤查询用的驼峰别名，**与详情接口无关**。前端曾误把 `FIELD_MAPPING` 的驼峰当成详情接口返回，把读取从 `detail.deadline_at` 改成 `detail.deadlineAt` → 取值恒为 `undefined` → 回显块 `{... && ...}` 判定为假、不渲染。

**修复**：详情/编辑统一读蛇形 `deadline_at`：
- `tasks/TaskDetailPage.tsx`：`Ticket.deadline_at`；展示读 `detail.deadline_at`；`startEdit` 回显 `detail.deadline_at`；编辑提交 `editForm.deadline_at`（后端 `update_ticket` 的 `setattr(ticket, 'deadline_at', ...)` 认蛇形）。
- `call/TicketDetailPage.tsx`：`setTicket` 两处从 `taskDetail.deadline_at`（蛇形）映射到 `ticket.deadline_at`；详情读 `ticket.deadline_at`。

**鉴别要点**：判断接口返回字段名时，看该路由的 `response_model`（Pydantic schema 字段名），不要看 `FIELD_MAPPING`（那是过滤查询别名表）。

### 3.6 iOS 软键盘遮挡 DatePicker 日历浮层（2026-08-18 修复）

**现象**：iOS Safari 上打开转工单确认弹窗或工单编辑弹窗，点击「最晚解决时间」`antd DatePicker`，日历面板下半部分被苹果输入法软键盘盖住，无法点底部"确定"按钮；Android Chrome 上无此问题。

**原因**：iOS Safari 与 Android Chrome 软键盘行为差异——

| 浏览器 | 键盘弹出时 | fixed/absolute 元素 |
|---|---|---|
| Android Chrome | `window.innerHeight` 缩小 | 重新布局，自动避让键盘 |
| iOS Safari | `window.innerHeight` 不变，仅 `visualViewport.height` 缩小 | **不重新布局**，fixed 元素停留在原位被键盘盖住 |

而 antd `DatePicker` 默认 `getPopupContainer = () => document.body`，日历浮层用 fixed 定位，在 iOS 上不会随键盘上移；又因 `placement` 默认 `bottomLeft` 向下弹，正好被底部键盘盖住。

**修复**（三处 DatePicker 统一改）：
- `placement="topLeft"`：日历**向上弹**，避开底部键盘（iOS 键盘从底部上滑，向上弹的日历天然不冲突）。
- `getPopupContainer={(trigger) => trigger.parentElement || document.body}`：浮层挂载到 trigger 父元素（字段容器），变 absolute 定位、跟随表单滚动，避免 iOS fixed 定位失效。
- 顺带移除"此刻/Now"快捷按钮：外层 `showNow={false}` + `showTime.showNow: false`。原 `showTime` 默认开启"此刻"，点击取当前时刻但 `format: 'HH:00'` 固定整点显示，会出现"11:15 提单却显示 11:00"的整点截断误显示。

**涉及位置**：`frontend/src/shared/components/ChatPanel.tsx`（转工单确认弹窗）、`frontend/src/pages/tasks/TaskDetailPage.tsx`（任务详情编辑弹窗）、`frontend/src/pages/call/TicketDetailPage.tsx`（工单详情编辑弹窗）。

---

## 四、AI 模块问题

### 4.1 LLM API 调用超时

**现象**：AI 诊断无响应或返回超时错误

**排查步骤**：
1. 检查 `ai/.env` 中的 API Key 配置
2. 测试网络连通性：`python ai/tests/test_llm_api.py`
3. 检查 OpenAI SDK 超时设置
4. 确认后端服务（8401）在运行且路由正确

### 4.2 知识库检索为空

**现象**：AI 回复表示"未找到相关知识"

**排查步骤**：
1. 确认 Qdrant 服务运行中
2. 检查知识库是否已导入数据：`ai/kb/qdrant/` 目录
3. 运行知识摄取脚本：`python -m ai.ingestion.ingest_all`
4. 检查 `ai/.env` 中的 Qdrant 连接配置

---

## 五、依赖安装问题

### 5.1 asyncmy 安装失败（Python 3.14）

**现象**：
```
ERROR: Failed building wheel for asyncmy
```

**原因**：asyncmy 在 Python 3.14 下无预编译 wheel，且源码编译失败。

**解决方案**：
`backend/app/core/db.py` 已添加 aiomysql 回退：
```python
try:
    import asyncmy
    ASYNC_DRIVER = "asyncmy"
except ImportError:
    ASYNC_DRIVER = "aiomysql"
```

### 5.2 sentence-transformers 安装慢

**现象**：`pip install -r ai/requirements.txt` 耗时过长

**原因**：sentence-transformers 依赖 torch，下载量大。

**解决方案**：
```bash
# 先单独安装 PyTorch（可使用国内镜像）
pip install torch --index-url https://download.pytorch.org/whl/cpu
# 再安装其他依赖
pip install -r ai/requirements.txt
```

---

## 六、Git 问题

### 6.1 .env 文件被误提交

**现象**：敏感配置被推送到远程仓库

**解决方案**：
```powershell
# 从 git 追踪中移除但不删除文件
git rm --cached .env

# 添加到 .gitignore
echo ".env" >> .gitignore

# 提交变更
git commit -m "chore: remove .env from tracking"
```

### 6.2 commit 信息不符合规范

**现象**：PR Review 要求修改 commit 信息

**解决方案**：
```powershell
# 修改最近一次 commit 信息
git commit --amend -m "feat: 正确的提交信息"
```

---

## 七、相关文档

| 文档 | 路径 |
|------|------|
| 测试开发规范 | `automation/docs/testing/testing_guidelines.md` |
| 自动化测试方案 | `automation/docs/automation_strategy.md` |
| 项目架构说明 | `docs/project_architecture.md` |
| 本地部署指南 | `docs/PRODUCT/SETUP.md` |
| 后端代码结构总览 | `backend/CODEBASE_OVERVIEW.md` |
