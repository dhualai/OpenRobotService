# task-22-ai-cases-merge-tool

## 本次目标

实现 AI 产物 → 正式用例库的合并转正工具(`cli-merge-ai-cases.py`),打通"AI 生成 → 人工确认 → 一键合并 → pytest + Allure"半自动闭环最后一环。

## 阅读内容

- `automation/scripts/cli-import-cases.py`(Excel 写入/编号逻辑复用)
- `automation/src/mocks/backend_mock.py`(token 机制、wechat 路由支持范围)
- `automation/src/runner/cases.py` / `executor.py`(正式库消费方式:sheet 名 = 模块)
- `automation/docs/design-ai-cases-merge.md`(设计文档,含已确认决策)

## 修改文件

| 文件 | 动作 |
|------|------|
| `automation/scripts/cli-merge-ai-cases.py` | 新增:合并工具(前缀映射/重编号/去重/Mock 支持检查/备份/dry-run/next-steps 提示) |
| `automation/scripts/tests/test_cli_merge_ai_cases.py` | 新增:11 条测试(分类/追加/幂等/跳过/skip 无空 sheet/steps 保留) |
| `automation/docs/design-ai-cases-merge.md` | 改:确认决策 + 实现记录 + demo-008 dry-run 结果 |
| `automation/README.md` | 改:常用脚本表加 merge 工具 |
| `.agents/skills/automation-testing/SKILL.md` | 改:CLI 速查加 merge 命令 |

## 测试结果

```
11 passed (scripts/tests/)
313 passed, 28 skipped (全量回归)
```

## demo-008 dry-run 结果

```
call +72(72 Mock未支持:真实 /api/call/* vs mock /api/qa/*)
tasks +102(0)
admin +160(0)
auth +8(8:真实 /api/auth/* vs mock /auth/*)
wechat +31(24:mock 仅 5 种子路由)
pending 0
```

## 风险/过程问题

| 问题 | 处理 |
|------|------|
| 测试曾污染正式库(TASK-037) | 根因:早期测试无隔离;已改为 tmp 副本 + monkeypatch;git restore 恢复后重建 TASK-032 |
| `.bak` 文件被覆盖损坏 | 删除损坏 .bak;合并工具 backup 仅作用于 EXCEL_PATH(monkeypatch 下为副本) |
| call/auth 路径契约差异(真实 vs mock) | 如实标注 Mock未支持,遗留决策:扩 mock 前缀别名或合并归一化路径 |
| 连字符文件名无法 import | 测试用 importlib + sys.modules 注册 |

## 下一步建议

1. 决策 call(72)/auth(8)路径差异:mock 加 `/api/call`、`/api/auth` 别名路由,或合并时归一化
2. wechat sheet 转正后生成 `test_wechat.py`(cli-generate-test-modules.py)
3. demo-009 用更新后的 case_gen 提示词重跑,验证 flow 全链路用例真实生成
4. 执行正式合并 + pytest + Allure 全流程演练
