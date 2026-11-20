# 本地测试环境配置

## 已完成

仓库根目录已创建 `.venv`，并安装：

- pytest / pytest-asyncio / allure-pytest
- httpx / pydantic / pydantic-settings
- mcp / schemathesis
- redis / qdrant-client / pymysql
- tenacity / python-dotenv
- playwright / openai
- openpyxl

当前本地 venv 测试结果：

```text
411 passed
42 skipped
0 failed
```

## 使用

```powershell
cd D:\WorkCode\OpenRobotService

# 设置当前终端环境变量
. .\automation\scripts\setup-local-env.ps1

# 跑全量测试
.\automation\scripts\run-local-tests.ps1

# 只跑 Mock API
.\automation\scripts\run-local-tests.ps1 tests -m api --no-trace -p no:cacheprovider
```

脚本默认追加：

```text
--alluredir=automation/output/allure-results
```

测试结束后会自动生成并打开 `automation/output/allure-report`。如需关闭自动打开：

```powershell
$env:ALLURE_AUTO_OPEN = '0'
```

## 仍需外部服务

本地未安装 Docker / WSL，因此以下服务没有启动：

- MySQL 3306
- Redis 6379
- Qdrant 6333/6334
- 后端 8400
- AI 服务 8401

对应测试会明确 skip，不会伪装成通过。

### 启动本地容器（需要 Docker Desktop）

```powershell
cd D:\WorkCode\OpenRobotService
docker compose -f automation\docker\docker-compose.test.yml up -d
```

### 启动后端

```powershell
cd D:\WorkCode\OpenRobotService\backend
pip install -r requirements.txt
python main.py
```

### 启动 AI 服务

```powershell
cd D:\WorkCode\OpenRobotService
pip install -r ai\requirements.txt
python -m uvicorn ai.app.main:app --host 0.0.0.0 --port 8401
```

AI 服务需要 `DEEPSEEK_API_KEY` 或 `LLM_API_KEY`。

## UI Smoke

Playwright 需要 Chromium v1243。当前机器已有其他 Chromium 缓存，但 venv 的 Playwright 需要重新安装：

```powershell
cd D:\WorkCode\OpenRobotService
.\.venv\Scripts\python.exe -m playwright install chromium
```

启动前端：

```powershell
npm ci --prefix frontend
npm run dev --prefix frontend -- --host 127.0.0.1 --port 5173
```

另开终端：

```powershell
$env:PLAYWRIGHT_BASE_URL = 'http://127.0.0.1:5173'
$env:PLAYWRIGHT_HEADLESS = '1'
.\.venv\Scripts\python.exe -m pytest automation\tests\ui -m ui -v
```

Call 工作台的 E2E 还需要：

```powershell
$env:PLAYWRIGHT_E2E_ENABLED = '1'
$env:PLAYWRIGHT_USERNAME = 'u1_auto'
$env:PLAYWRIGHT_PASSWORD = '123456'
```

## 真实环境

真实后端和 AI 评测需要：

- `REAL_API_BASE_URL`
- `REAL_SSH_HOST` / `REAL_SSH_PORT` / `REAL_SSH_USER` / `REAL_SSH_KEY`
- `REAL_U1_USERNAME` / `REAL_U1_PASSWORD`
- `REAL_U2_USERNAME` / `REAL_U2_PASSWORD`
- `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL`

不要把密钥写入仓库或 Trace。