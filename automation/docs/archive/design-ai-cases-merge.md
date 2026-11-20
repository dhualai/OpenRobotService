# 设计:AI 用例转正合并工具(cli-merge-ai-cases.py)

> 状态:设计稿(待人工确认) | 作者:automation 测试架构 | 日期:2026-08-07
> 目标:打通"AI 生成 → 人工确认 → 一键合并进正式用例库 → pytest + Allure"半自动闭环的最后一环

---

## 1. 背景与缺口

### 1.1 用例分层模型(已确认)

```
PRD(不动)
   │ AI 流水线(一次)
   ▼
references/generated-cases/{run_id}/   ← 只读档案:生成一次,永不改动(追溯/对照用)
   │ 人工确认 + 合并(一次,单向复制)
   ▼
testdata/cases/api-test-cases.xlsx     ← 执行库:pytest 真正跑的;合并后也不动
   │
   ▼
pytest + Allure
```

- `references/generated-cases/` 是**只读档案**:由 PRD 驱动生成一次;PRD 不动则不再生成、不改动。合并不写它。
- 合并且是**一次性动作**:确认一次 → 合并一次 → 之后两侧均不再变动。
- 仅当 PRD 变更 → 新 run_id 生成 → 新的确认合并,且**只追加不修改**既有用例。
- testdata 既有用例(92 条)不受任何影响。

AI 流水线(`ci_ai_gen/run_pipeline.py`)已能产出与平台格式一致的产物:

```
automation/references/generated-cases/{run_id}/
├── cases.xlsx    # 11 列与 testdata/cases 完全一致(id/module/method/path/auth/role/payload/expected_status/expected_fields/type/note)
├── cases.json    # 原始用例(含 req_id/type/precondition)
└── analysis.md   # REQ 功能点清单(含模块归属依据)
```

**当前缺口**:产物与正式库 `testdata/cases/api-test-cases.xlsx` 无法直接合并:

| 差异项 | AI 产物(demo-008) | 正式库要求 |
|--------|------------------|-----------|
| sheet | 单 sheet(`ai_generated`) | 按模块:call/tasks/admin/auth |
| module 字段 | 中文功能点(如"用户管理") | 标准模块名(call/tasks/admin/auth) |
| id | `TC001` 等(与模块无关) | `CALL-001` 等模块前缀编号 |
| 去重 | 无(每次全量重生成) | 与已有用例按接口去重 |
| Mock 支持 | 未检查 | 需能被 `src/mocks/backend_mock.py` 执行 |

demo-008 的 373 条用例至今无一条转正,全靠人工 Excel 复制(违反"用例与代码分离、可维护"目标)。

## 2. 方案

新增 `automation/scripts/cli-merge-ai-cases.py`,输入 AI 产物,归一化后按模块追加到正式 Excel。

### 2.1 命令

```powershell
# 预览合并计划(不写入)
python scripts/cli-merge-ai-cases.py --run-id demo-008 --dry-run

# 正式合并
python scripts/cli-merge-ai-cases.py --run-id demo-008

# 指定产物文件 / 自定义模块映射 / 跳过 Mock 不支持项
python scripts/cli-merge-ai-cases.py --cases path/to/cases.xlsx [--module-map map.yaml] [--skip-unsupported]
```

### 2.2 归一化规则

1. **module 判定(按 path 前缀映射,事实驱动,不猜 REQ)**:

   | path 前缀 | module | 依据 |
   |-----------|--------|------|
   | `/api/call/*`、`/api/conversations*`、`/api/qa*`、`/api/messages*`、`/api/my-tasks*` | call | 我要摇人 |
   | `/api/tasks/*`、`/api/integrations*` | tasks | 系统任务(含催办/上报,与现有 sheet 一致) |
   | `/api/admin/*` | admin | 后台管理 |
   | `/auth/*`(login/me) | auth | 认证 |
   | `/api/wechat/*` | wechat(新建 sheet) | mock 已支持;需配套 test_wechat.py(见 §2.4) |
   | `/api/ai/*` 及其他 | **pending,不写入** | 无测试文件对应,人工决策 |

   - 映射表内置,支持 `--module-map <yaml>` 覆盖(格式:path前缀 → module)。

2. **id 重编**:丢弃 `TCxxx`,按现有 `_next_id` 逻辑生成 `{MODULE}-{n:03d}`(复用 cli-import-cases.py 逻辑)。

3. **去重**:按 `(module, method, path, payload)` 与正式库比对,已存在跳过(比 id 去重更可靠,因为 TC id 每次都变)。

4. **Mock 支持检查**:内置 MockBackend 前缀清单(`/health`、`/auth/login`、`/auth/me`、`/api/tasks`、`/api/wechat`、`/api/admin`、`/api/conversations`、`/api/qa`、`/api/messages`、`/api/my-tasks`、`/api/ai`、`/api/integrations`),不命中的在 note 标注 `Mock未支持`,默认仍写入(半自动:人工确认时可见),`--skip-unsupported` 可剔除。

5. **note 拼接**:保留 `需求:REQ-xx | 标题 | 前置:...`,追加 `类型:{type}`(正/异常/边界/权限/状态流转),人工确认时可读。

### 2.3 只读与安全约束

- **references 只读**:合并工具仅读取 `references/generated-cases/{run_id}/`,绝不写入;产物永不被合并动作修改。
- **testdata 增量**:合并只追加新用例;已存在的 `(module, method, path, payload)` 一律跳过;既有用例与既有编号不重排、不修改。
- **一次确认一次合并**:dry-run 输出合并计划,人工确认后才写盘;合并前自动备份 `api-test-cases.xlsx` 为 `api-test-cases.xlsx.bak`。

### 2.4 输出```
Merge plan for run demo-008 (373 cases):
  call:   +N  (skip X dup, Y mock-unsupported)
  tasks:  +N
  admin:  +N
  auth:   +N
  wechat: +N  (new sheet — requires test_wechat.py, see below)
  pending: N (paths without module mapping: /api/ai/...)
Dry-run mode: api-test-cases.xlsx not modified
```

合并完成后提示下一步:
1. `pytest tests/{module}/ -v` 验证新用例(Mock 未支持的会失败,人工决定修 Mock 或改用例)
2. 如有新 sheet(如 wechat):`python scripts/cli-generate-test-modules.py` 生成测试文件
3. `--alluredir` 出 Allure 报告

### 2.5 涉及文件

| 文件 | 动作 |
|------|------|
| `automation/scripts/cli-merge-ai-cases.py` | 新增(约 200 行,复用 cli-import-cases 的 Excel 写入与编号逻辑) |
| `automation/scripts/tests/` | 新增 `test_cli_merge_ai_cases.py`(临时产物文件合并、去重、pending、mock 检查、dry-run 不写盘) |
| `automation/AGENTS.md` | 追加命令到 CLI 工具段(§常用命令) |
| `.agents/skills/automation-testing/SKILL.md` | §5 补充"合并转正"命令 |

### 2.6 风险

| 风险 | 对策 |
|------|------|
| 路径前缀映射错导致用例进错 sheet | 映射表集中可审;dry-run 预览 + 人工确认 |
| 373 条一次并入导致执行大面积失败(Mock 缺口) | note 标记 + 分模块分批合并;失败由 mock 修复或用例调整 |
| Excel 被误覆盖(此前 cli-init-cases 事故) | 合并前自动备份 `api-test-cases.xlsx.bak`;dry-run 默认开启提示 |
| 新 sheet(wechat)没有测试文件 | 合并后提示用 cli-generate-test-modules.py 生成,或暂不建 sheet(映射到 pending) |

---

## 3. 待确认问题

1. 微信接口(`/api/wechat/*`):新建 wechat sheet + 测试文件,还是本轮先不转正(pending)?
2. Mock 不支持的用例:默认写入并标注,还是默认剔除?
3. demo-008 373 条是否全量合并,还是先合并 P0/P1(按 type/req_id 过滤)?

## 4. 确认决策与实现记录(2026-08-07)

| # | 问题 | 决策 |
|---|------|------|
| 1 | 微信接口 | ✅ 新建 wechat sheet(合并工具自动创建),后续生成 test_wechat.py |
| 2 | Mock 未支持 | ✅ 默认写入并标注 `Mock未支持`,`--skip-unsupported` 可剔除 |
| 3 | 合并范围 | ✅ 全量 dry-run 预览,确认后合并 |

已交付 `automation/scripts/cli-merge-ai-cases.py`:

```
用法:
  python scripts/cli-merge-ai-cases.py --run-id demo-008 --dry-run   # 预览
  python scripts/cli-merge-ai-cases.py --run-id demo-008             # 合并(自动备份 .bak)
  python scripts/cli-merge-ai-cases.py --cases <path> [--module-map map.yaml] [--skip-unsupported]
```

规则(与 §2 设计一致):
- 模块按 path 前缀映射(call/tasks/admin/auth/wechat;`/api/auth/*` 归 auth;未映射 → pending 不写入)
- id 重新编号(`{MODULE}-{n:03d}`);按 (module, method, path, payload) 去重,幂等
- Mock 支持检查:wechat 精确到子路由(仅 health/get_menu/create_menu/send_message/根),其余前缀匹配;不支持的标注 `Mock未支持`
- references 只读;合并仅追加;写盘前自动备份 `api-test-cases.xlsx.bak`
- 新增 wechat sheet 时提示生成测试文件(`cli-generate-test-modules.py`)

**demo-008 dry-run 结果(2026-08-07)**:

| 模块 | 新增 | Mock 未支持 | 说明 |
|------|------|------------|------|
| call | 72 | 72 | 真实后端 `/api/call/*` vs mock `/api/qa/*` 路径差异,需扩 mock 或归一化路径 |
| tasks | 102 | 0 | 可直接执行 |
| admin | 160 | 0 | 可直接执行 |
| auth | 8 | 8 | `/api/auth/*` 与 mock `/auth/*` 差异 |
| wechat | 31 | 24 | 仅 5 种子路由 mock 支持 |

**遗留决策**:call 72 条与 auth 8 条的路径契约差异(真实 `/api/call/*`、`/api/auth/*` vs mock `/api/qa/*`、`/auth/*`)——合并后执行将失败,需:① mock 增加 `/api/call`、`/api/auth` 前缀别名;或 ② 合并时归一化路径。建议先合并非差异部分。
