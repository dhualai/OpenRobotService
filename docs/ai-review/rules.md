# AI 检视规则（rules）

> 本文件是 `quick_screen.py` 内置规则的文档镜像，两处需同步维护。
> 分级原则：**高危（blocker）= 值得推企微打断人**；建议关注（warning）= 只落评论。
> AI Review 的头号死因是狼来了，高危档宁缺毋滥。

## 一、快筛规则（确定性，路径与正则）

### 路径规则

| 匹配 | 级别 | 含义 |
|---|---|---|
| `backend/alembic/**` | 高危 | 迁移脚本不入远端库、由专人执行；DDL 不可自动回滚 |
| `**/.env*` | 高危 | 环境配置变更影响所有环境，且有密钥泄漏风险 |
| `deploy/deploy.py` `deploy/notify*.py` `backend/scripts/schema_sync.py` | 高危 | 部署链路本体 |
| `.github/workflows/*` | 关注 | CI/CD 行为变更（曾有旧分支合并覆盖门禁的事故） |
| `deploy/**` 其余 | 关注 | 部署配套脚本 |
| Dockerfile / docker-compose / supervisor / nginx 配置 | 关注 | 运行环境 |
| `requirements*.txt` / `package.json` | 关注 | 依赖引入 |

### 内容规则（只检查 diff 新增行）

| 特征 | 级别 | 含义 |
|---|---|---|
| `sk-…` / `AKIA…` / `password= "…"` 字面量 | 高危 | 疑似密钥硬编码 |
| `DROP COLUMN` / `drop_column` / `ALTER … MODIFY/CHANGE COLUMN` | 高危 | **schema 门禁盲区**（见下） |
| `os.system` / `shell=True` / `eval` / `exec` | 关注 | 命令注入/动态执行 |
| 字符串拼接 SQL | 关注 | SQL 注入 |
| `pickle.load` / `yaml.load(` | 关注 | 反序列化 |
| `allow_origins=["*"]` | 关注 | CORS 全开 |
| `datetime.now()` | 关注 | 时间字段 UTC 纪律（历史事故 PR #333） |

## 二、深度检视重点（LLM 逐条对照）

按《Security Rules for AI Code Generation V5》12 条反向验收：

1. **SQL 注入**：参数化查询？还是字符串拼接？
2. **命令注入**：外部输入进 shell？argv 形式？
3. **路径穿越**：文件操作是否固定基目录 + 边界校验？
4. **SSRF**：外联 URL 是否白名单 + IP 校验？
5. **认证与属主**：接口是否鉴权 + 资源属主校验（越权）？
6. **密钥管理**：硬编码密钥？密钥落日志？CSPRNG？
7. **反序列化**：是否进具体类型？
8. **XSS**：用户输入渲染是否自动转义？
9. **开放重定向**：redirect 目标是否同源白名单？
10. **文件上传**：扩展名 + MIME 校验？
11. **沙箱**：无头浏览器是否开启沙箱？
12. **日志脱敏**：手机号/密钥/token 是否脱敏？

### schema 盲区（结构门禁明确不覆盖，AI 必须盯）

`schema_sync.py` 门禁**只处理**「可空或带默认值的新增列 + 缺索引」，以下变更**它检测不到也不会拦**，只能靠人和 AI：

- 删列、改列名、改类型/长度、改枚举值（`DROP/MODIFY/CHANGE`）
- 外键与 CHECK 约束变更
- 数据回填、`alembic_version` 链同步

AI 发现此类变更 → 一律报**高危**，理由固定提及「门禁不覆盖、不可自动回滚」。

### 其他语义层重点

- 跨模块副作用：改共享模型/工具函数时，其他调用方是否兼容；
- 前后端契约：接口字段改名/删字段，前端是否同步；
- 迁移与代码的时序：代码用了新列但迁移脚本是否配套（部署不跑迁移，代码比库新会 1054）。
