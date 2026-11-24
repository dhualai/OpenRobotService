# 项目踩坑知识库（pitfalls）

> AI 深度检视时优先对照本清单——这些都是本项目**真实发生过**的事故或强约定，
> 通用代码审查给不了这个上下文。每条含：规则、历史出处、命中时怎么报。

## 1. 时间字段必须 UTC

- **规则**：落库时间一律 `datetime.now(timezone.utc)`，禁止 `datetime.now()` 裸用。
- **出处**：公告 `published_at` 生产事故（PR #333）——本地时间落库整体偏移 8 小时。
- **怎么报**：diff 新增行出现 `datetime.now()` 且语义是"记录时间" → 建议关注。

## 2. 权限码注册位置

- **规则**：新权限码只能在 `permissions.get_all_permissions` 处注册；
  塞进接口函数体内注册会死锁。
- **出处**：公告管理权限码修复（PR #331）。
- **怎么报**：diff 出现"在 API 函数体内调用权限注册" → 高危。

## 3. workflow 文件与门禁覆盖事故

- **规则**：`.github/workflows/**` 与 `deploy/**` 的变更必须人工逐行看；
  曾有 PR merge main 时旧版文件覆盖删除 73 行 CI 门禁（PR #338/#363）。
- **怎么报**：路径规则已覆盖；AI 需额外检查"workflow 步骤是否被删/降级"。

## 4. 部署链路不跑迁移

- **规则**：deploy 链路无 alembic 步骤；alembic 脚本**不推远端**，由专人执行。
  代码比库新会直接 1054 报错。
- **怎么报**：代码引用新字段但 alembic 未配套（或反之）→ 高危。

## 5. schema 门禁盲区

见 rules.md「schema 盲区」：删列/改名/改类型/改枚举，门禁检测不到，AI 必须盯。

## 6. 前端测试纪律

- **规则**：测试断言禁止依赖样式类名（重构即碎），用 testid / getByRole / aria-*；
  vitest 走 esbuild **不查类型**，前端改动静默类型错误会溜进 CI 构建 job 卡部署。
- **怎么报**：新增 `.spec.ts` 里出现 `toHaveClass`/类名选择器断言 → 建议关注。

## 7. 仓库结构注意

- `ai/tests/` 与 `ai/scripts/` 被 .gitignore **整体忽略**，往这两处搬文件必须
  `git add -f`，否则提交会"只删不建"。
- `main` 分支被保护规则冻结，不要往 main merge；一切合入走 `test`。

## 8. 敏感信息纪律

- Webhook URL 路径含机器人密钥，日志只打主机名；
- LLM API key 只走环境变量/Secrets，绝不 print；
- 中文提交信息用 UTF-8 文件 + `git commit -F`，避免编码乱码。
