# OpenRobot MCP Server

把 OpenRobot 自动化测试运行时暴露给 Codex、Claude Code、Cursor、Windsurf
等支持 MCP 的 AI IDE。

## 工具

| 工具 | 用途 |
|---|---|
| `run_scenario` | 提交 pytest 场景，返回 run_id/trace_id |
| `get_run_status` | 查询运行状态、退出码和摘要 |
| `inspect_trace` | 查看步骤、产物和运行目录 |
| `diagnose_run` | 对失败运行做分类和自诊断 |
| `diagnose_environment` | 检查运行时、依赖、API、AI、MySQL、Redis、Qdrant |
| `run_ai_eval` | 提交 AI L1/L2/L3 评测 |
| `generate_ticket_cases` | 从工单生成候选功能测试用例 |

## 场景\n\n`framework / api_mock / business_chain / contract / real_smoke / real_lifecycle / infrastructure / ai_eval / ui_smoke / ui_e2e`\n\n## 启动

```powershell
cd D:\WorkCode\OpenRobotService
pip install -e automation
python -m automation.mcp_server
```

SSE 模式：

```powershell
python -c "from automation.mcp_server.server import main; main('sse', host='127.0.0.1', port=8001)"
```

也可以使用：

```powershell
python automation\scripts\cli-mcp-server.py
```

## Codex 配置

将以下内容加入 `~/.codex/config.toml`：

```toml
[mcp_servers.openrobot]
command = "python"
args = ["-m", "automation.mcp_server"]
cwd = "D:\\WorkCode\\OpenRobotService"

[mcp_servers.openrobot.env]
PYTHONUNBUFFERED = "1"
PYTHONPATH = "D:\\WorkCode\\OpenRobotService"
```

重启 Codex 后，在对话中可以要求：

- “运行 business_chain Fast 场景，返回 run_id。”
- “查看 trace，告诉我哪一步失败。”
- “诊断 test 环境，检查后端、AI、MySQL、Redis、Qdrant 是否可达。”
- “为工单 837 生成候选测试用例，产物进入 pending_review。”

## 安全边界

- AI 只负责探索、生成和诊断，最终 CI 断言仍由 pytest 执行。
- `generate_ticket_cases` 只生成候选文件，不会自动合入正式用例。
- 生产工单扫描是只读的，只读取生成用例所需的最小字段。
- 密钥、Token、数据库密码不会写入 Trace。
- 环境不可达或工具报错时，先调用 `diagnose_environment`，不要盲目重跑。