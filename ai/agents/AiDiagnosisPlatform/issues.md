# 提单流程设计 & 问题清单

## 当前架构（LLM 驱动，2026-08-01 重构定稿）

```
排查(诊断) → 用户转单 → 【_decide_ticket_fields】LLM 定 2-3 个必补字段 → 工单填写模式逐个补 → project+字段齐 → 提单
```

核心原则：
- **字段由 LLM 动态决定**（`_decide_ticket_fields` 转单时调一次，按问题类型从词表里选 2-3 个）
- **提单门槛**：project 铁律 + LLM 定的 required_fields 全非空（`_assess_ticket_readiness`）
- **闭环**：`_can_submit` 基于 `last_submitted_ticket` + 新 problem，防重复提单
- **无服务端字段兜底**：删了关键词 force-submit、B 触发器；完全信任 LLM 的 action=submit
- **防鬼打墙**：诊断/收集轮次上限（`_MAX_DIAGNOSIS_ROUNDS=6`、`_MAX_COLLECT_ROUNDS=4`）
- **对话/按钮两路径**：共享 state、`_can_submit`、`_assess`、`_reset_state_after_submit`，不会错位

## 已修复（本次重构）

| # | 问题 | 修复 |
|---|------|------|
| 1 | 关键词 force-submit 笨拙（"我不转工单"误命中） | 删关键词，改 LLM 意图判断（action=submit） |
| 2 | `_assess` 硬编码多字段清单，与 prompt 的 required_fields 脑裂 | 改 project + LLM-decided required_fields |
| 3 | LLM 提的 project 被 `_apply_state_update` 丢弃（旧 pending_submit 遗留 guard） | 删 guard |
| 4 | `_resolve_project` 定义了从没调用，项目匹配断路 | 接到 `_build_ticket`，简称→真实项目名 |
| 5 | 闭环绕过：LLM 从成功消息/last_ticket_context 重提 project 提第二单 | context_start 跨过成功消息 + last_ticket_context 不带 project/主题 |
| 6 | submit() 从 memory 重载拿到旧 state，第二单误杀 | 提单前先存盘 |
| 7 | 流式 `_suppress_doomed_submit`、preflight 快照等屎山 | 全删，逻辑归位到 post-LLM 单点 |
| 8 | submit()/confirm_submit() 各抄一份收尾代码 | 抽 `_reset_state_after_ready` 共用 |
| 9 | `_decide_ticket_fields` 凭空发明 error_location 野字段卡提单 | 限定 `_TICKET_FIELD_VOCAB` 词表 |
| 10 | flash 单轮过度澄清不肯 submit | 接受现状（多轮主流流程稳定；单轮多问一句后下一轮提） |
| 11 | 嘴嗨：LLM 流式说"工单已提交"但被服务端拦截，修正消息被吞（_msg_yielded 时兜底不再发） | 拦截块追加 `\n\n⚠️ + 修正` token（去重复"好的"）+ prompt 禁止 submit 话术只许写"好的"。服务器实测 db_id=195 全流程通过 |
| 12 | 反噬：prompt 禁止抢答"已提交"后，LLM 只回"好的"，**成功**提单消息也被兜底吞掉——用户不知道是否提单成功 | 成功路径补发 `\n\n✅ + 已生成工单` token（同样去重复"好的"）。本地实测提单轮用户可见"✅ 已为「安吉北区」生成工单" |
| 13 | **USP 偏置**：领导测试发现问服务号权限问题，AI 答的是调度 USP 的内容顺带服务号（检索侧正常，问题在提示词）——(a) persona 把产品定义为 USP 且"严禁超出 AGV/AMR 和 USP 领域"；(b) 知识库优先级未列服务号 platform 类别；(c) `_sub_labels` 无 platform 键，chunk 显示泛化标签 | 提示词 4 处：persona 改为"你服务两大产品：USP + 摇人吧服务号平台本身"，用户问服务号自身问题（权限/工单可见范围/菜单/账号）必须答服务号、严禁张冠李戴；知识库优先级新增 **🎫 服务号平台（标题含「摇人吧服务号平台手册」）** 为第 1 类；`_sub_labels` 加 `"platform": "🎫 服务号"`；chunk 标题格式说明补 `🎫 服务号 N：`。实测 4 问法（为什么我看不到工单/权限是怎么配置的/工单状态/服务号能做什么）全部正确归到服务号平台，"权限是怎么配置的"不再编造 RBAC 权限码 |

## 已知限制（接受）

| # | 问题 | 说明 |
|---|------|------|
| 1 | 单轮"全信息+转单"flash 偶发不直接 submit | flash 模型过度澄清，多问一句；用户答后下一轮提。多轮主流流程不受影响（3/3 稳）|
| 2 | 项目匹配需 `helpdesk_724` 库 | 沙箱无此库走"平台项目"兜底；生产环境正常匹配 |
| 3 | 按钮路径 need_fields status + token 双发 | 前端可能显示重复（低优）|

## 稳定性（live，3 轮）

- 主流多轮流程 I1/I2（排查→转单→补充→提单）：**3/3 ✅**
- 闭环 C2（新问题→第二单）：3/3 ✅
- 意图 D1/D3/H3（否定/闲聊/别转单）：3/3 ✅
- 单元测试：66/66 ✅

## 测试

```bash
python -m pytest ai/tests/test_conversation_logic.py ai/tests/test_can_submit.py ai/tests/test_ticket_submit.py -q
# live（需起 run.py）
python -m ai.tools.live_ticket_test --only I1,I2,C2 --rounds 3
```
