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

## 2026-08-02 真实测试修复

| # | 问题 | 根因 | 修复 |
|---|------|------|------|
| 14 | **连续提单后死锁**：提单后用户问"安吉北区车不跑了"，LLM 一直复读"请描述新现象" | `last_ticket_context` 写"不要自行诊断"，LLM 看到新故障也不敢提取 problem_summary → `_can_submit` 永远 false → 死循环 | 改为"如果用户描述了新问题→正常诊断；只在用户说转工单但无新问题时才拦截" |
| 15 | **弹窗项目名显示用户原话** | `_build_ticket` 匹配成功分支只设了 `result["project_id"]`，漏了 `result["project"]`——始终用 line 1228 初始化的用户原话 | 补 `result["project"] = match.name` |
| 16 | **收集轮不跳过检索** | `ticket_collecting` 只在 LLM 说 submit 被拦截时设（line 1905），LLM 说 ask 逐个收字段时不设 | 新增：LLM 说 ask 且 required_fields 已有值且有缺失 → 提前设 ticket_collecting；ticket_collecting block 结束时刷新为当前缺失字段 |
| 17 | **三路检索 16 个 chunk 全塞 prompt** | 无排序无截断，team(6)+company(4)+industry(3)+cheduan(3) 全量入参 | 合并→按 score 降序→去重→取 top 6（`_MAX_RETRIEVAL_DOCS=6`） |
| 18 | **`_TICKET_FIELD_VOCAB` 固定词表限制** | `_decide_ticket_fields` 只接受 10 个预定义字段，登录问题被要求 robot_type | 删除词表，LLM 自由选字段；prompt 给参考但不限制 |
| 19 | **`_decide_ticket_fields` 标签过长** | LLM 把 value 写成整句话（"需要工程师协助取消正在执行的任务或充电操作"），追问话术臃肿 | prompt 限 ≤8字 + 代码 `[:12]` 兜底截断 |
| 20 | **旧话题污染检索** | `problem_summary` 无截断拼入检索 query，"充电验证"带偏"自动门对接"的 embedding | 用户查询≥10字时不拼 problem_summary；始终截断 hypotheses[:50] |
| 21 | **提单后 LLM 看不到新消息** | `_reset_state_after_submit` 设 `context_start=len(turns)=10`，下一轮 `add_turn` 后 buffer 满截断（max_turns=10），turns 仍为 10，`turns[10:]` 返回空列表 → LLM 对话为空 → 输出问候 | `_build_diagnosis_prompt` 增加越界保护：`context_start >= len(turns)` 时 clamp 到 `max(0, len-4)`（≈2 轮对话） |
| 22 | **第三轮提单鬼打墙：phase 语义混淆** | `_apply_action_phase` 在每次 answer 时设 `phase="resolved"`，"刚答完诊断"和"刚提完工单"共用同一 phase → `_apply_state_update` 的 resolved guard（line 766）误拦正常诊断的 problem_summary 更新 | 删除 `answer → resolved`，`resolved` 只由 `_reset_state_after_submit` 在提单成功后设置 |
| 23 | **第三轮提单鬼打墙：ticket_type 被覆盖** | `_decide_ticket_fields`（独立 LLM 调用，无完整对话上下文）会覆盖主 LLM 判定的 ticket_type（problem→support），required_fields 与实际故障不匹配，LLM 陷入收集死循环 | 加 `if not agent_state.ticket_type:` guard，只在主 LLM 未判定时才设 |
| 24 | **第三轮提单鬼打墙：required_fields 不准确** | 主 LLM action=submit 时不设 required_fields，留空等 `_decide_ticket_fields` 兜底——兜底的独立 LLM 没有完整对话上下文，判断易出错 | prompt 指示 LLM 在 action=submit 时同时写入 required_fields，不依赖兜底 |

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
- 单元测试：61/66 ✅（5 个提交路径测试依赖 `helpdesk_724` DB，沙箱不可用）

## 测试

```bash
python -m pytest ai/tests/test_conversation_logic.py ai/tests/test_can_submit.py ai/tests/test_ticket_submit.py -q
# live（需起 run.py）
# A-I: 原始场景  J: 2026-08-02 真实 Bug 回归
python -m ai.tools.live_ticket_test --only J1,J2,J3,J4,J5,J6,J7,J8,J9
python -m ai.tools.live_ticket_test --only I1,I2,C2 --rounds 3
```
