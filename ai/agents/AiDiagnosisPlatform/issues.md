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

## 2026-08-20 生产日志修复

生产实录三问题（13:35-13:48 日志）：A) LLM 服务抖动致补充轮 32.7s（流式重试自救，不修）；
B) 同抖动窗口 `_build_ticket` 20s 超时 → 工单降级默认值（标题退化成原话硬截 20 字）；
C) 54 轮长会话话题漂移（USP服务器重启 → 回休息站），problem_summary 停在旧话题混入新工单，
decide_fields 还在旧状态上判缺「问题现象」多追问一轮。

| # | 问题 | 根因 | 修复 |
|---|------|------|------|
| 25 | 工单生成单次调用无重试，抖动一次即降级 | `complete` 非流式路径无重试（重试只在流式路径有） | `_build_ticket` 改两次尝试（超时/返回不可解析都重试），期间用户看「正在生成工单」动画无感知；两次都挂才走默认值兜底 |
| 26 | 长会话话题切换后 problem_summary 不刷新，旧话题混入新工单 | 诊断单轮分支只答不记；submit 轮没人要求 LLM 核对 summary 是否仍对应当前话题 | DIAGNOSIS_PROMPT 加铁律：「状态」里的问题与用户本轮问题不一致时必须更新 problem_summary（与 #24 required_fields 同机制，判断仍全在 LLM；`_apply_state_update` 在 escalated 阶段本就接受覆盖，resolved 拦截保留防闭环） |
| 27 | 14:38 测试单描述里没有车型 XSC121——用户可见信息丢失 | 三重叠加：(a) 收集轮 LLM 自由命名字段为 `vehicle_id`，`_build_ticket` 取数只认 `collected_info['robot_type']`；(b) 描述规则「设备型号等一项都不能丢」是软约束，build LLM 漏了；(c) **弹窗只渲染 标题/描述/期望解决时间/项目**，draft 的 robot_type 只进 DB metadata_info、弹窗不显示——描述是车型唯一用户可见通道 | 描述规则升级为硬规则：「型号/车辆编号必须写进 description 正文——工单表单没有独立的型号字段，描述是它唯一对用户可见的地方，即使已在 robot_type 填过也要写」。项目名不在描述里是设计（三条禁令、单通道防鬼打墙），不是丢失 |
| 28 | 开关打开后触发轮死寂：只输出过渡语「好的，我帮您转工单，我看一下还需要补充哪些信息：」就停（tool_calls=0），用户在对话里无法提单；用户点按钮补完 4 字段后又因 `tool_loop_active` 未置位掉回旧状态机 | 过渡语指令「先说过渡语再调用」被模型执行成「只说过渡语」；`run_tool_loop_stream` 把无工具调用当正常回合结束；空转轮不置粘性续接标志（只有真调了工具的轮次才置） | 三层修复：(1) 提示词硬性化——过渡语和工具调用必须在**同一次回复**里完成（ticket/diagnosis 两分支同改）；(2) 机制层空转纠偏——零工具调用且非放弃轮时，把空转回合+纠偏 system 指令追加进 messages 重跑一遍循环（判断仍在 LLM，代码只发现协议违约，放弃轮由 LLM 固定话术「不转工单」识别沿用既有协议）；(3) 空转/回话轮置 `tool_loop_active=True`（草稿已存在时除外——草稿轮设计就是由意图分类路由）。测试 `TestToolLoopIdleCorrection` 3 例：纠偏后调工具+追问、双空转不无限重试+粘性置位、放弃不纠偏。**部署后首测又冒出双份过渡语**（「我帮您转工单…」「我帮您继续转工单…」连说两遍）——纠偏轮复述了过渡语再调工具；纠偏指令补铁律：「用户已看到你上一条回复，不要输出过渡语、不要复述（会看到两遍）；调工具正文留空，追问直接问句开头」 |
| 29 | 生产实锤（16:29-16:32）：问 3 个字段（时间/车辆编号/任务）用户只答 1 个「早上九点」就弹窗——LLM 自己判的还是 ask（msg 在追问编号），被服务端「补充字段已齐自动 review」强转 submit，编号/任务永远没收 | 快路径 LLM 把三个信息点**打包成一个** required_fields key（`occurrence_details: '卡顿发生的时间、车辆编号及任务信息'`）→ 用户答一项该 key 即非空 → 清单「假齐」 | 三个 LLM 声明 required_fields 的 prompt 全部加铁律「**一项信息一个字段，禁止打包**」（DIAGNOSIS_PROMPT / 快路径 prompt 2.1 / `_compute_ticket_fields`），并给了正反例。测试 `TestRequiredFieldsGranularity` 3 例（三处 prompt 文案断言）。live 复现：修复后字段拆成 occurrence_time/robot_id/task_info 三 key，「早上九点」只填 1/3 继续追问，三项齐才弹窗 ✅ |

A 不修：外部抖动，重试已救回轮次；调 read timeout 会误杀长思考的流间隙。
话题残留的根治是开 `AI_TICKET_TOOL_LOOP`（submit_ticket 参数每次带 LLM 现写的
problem_summary，状态跟本次调用走，天然无残留）；#26 是老路径上的止血。

## 已知限制（接受）

| # | 问题 | 说明 |
|---|------|------|
| 1 | 单轮"全信息+转单"flash 偶发不直接 submit | flash 模型过度澄清，多问一句；用户答后下一轮提。多轮主流流程不受影响（3/3 稳）|
| 2 | 项目匹配需 `helpdesk_724` 库 | 沙箱无此库走"平台项目"兜底；生产环境正常匹配 |
| 3 | 按钮路径 need_fields status + token 双发 | 前端可能显示重复（低优）|

## 2026-08-20 项目预填（对话识别 → 草稿预填，弹窗仍可改）

背景：领导要求"对话中能获取到项目信息就预填充，用户修改写回工单"。当年 project 移出对话链路
是因为全量项目库模糊匹配错配 + LLM 幻觉 + 服务端循环兜底（鬼打墙三源）。本次改法的不同：

- **匹配域收敛**：不匹配全量库，只匹配"该用户名下项目"（`user_project_roles` 排除 global，
  与后端 `GET /api/admin/projects/me` 同源）。LLM 不再自由生成项目名，而是从注入列表**照抄**。
- **严格校验防幻觉**：`_match_project_choice` 只接受与列表 name/code 精确相等（strip 后）；
  抄不齐 = 幻觉信号 → 置空走弹窗。宁空勿错，不做模糊容错。
- **单向管道（防鬼打墙铁律）**：project 不进 `state.collected_info`、不进 required_fields
  判缺、不触发追问循环。路径只有：工具参数 `project_choice` → `_build_ticket(prefill_project=)`
  → draft → 弹窗（overrides 覆盖优先）→ confirm_submit。与 `requested_assignee` 同款。
- **服务端只做一次单向校验**（列表内 or 置空），无循环兜底。
- **预填播报单一信息源**：LLM 在调工具前说话时校验还没发生，若让它播报"项目已填 XX"
  可能与校验结果口径不一（抄错被拒 → 气泡说填了、弹窗是空）。因此 prompt 明确禁止 LLM
  播报预填，播报统一由服务端兜底文案在草稿生成时给出（说的是校验后的真实值）；
  LLM 自己说了收尾话（已流式）时则不再补发，弹窗所见即所得。
- **降级 = 现状**：`_get_user_projects` 任何失败/为空 → 不注入 prompt → LLM 无从照抄 →
  draft project 空 → 弹窗搜索选择（旧行为）。
- **性能**：查询毫秒级 + per-username 缓存 5min（提单低频，同用户二次调用零 DB）；
  仅提单/诊断工具循环调用，普通问答轮零开销；prompt 增量几百字符对首 token 无感。
  兜底实测（本机 DB 不可达）：1s 超时截断（实测 ~1.16s 含日志）+ 失败负缓存 60s
  （命中 ~0.005ms）——DB 故障时最坏单轮多等 1 秒、后续每轮零开销，不逐轮重付超时。

改动点：
- `ticket_tool.py`：TOOL_SCHEMA / TOOL_SCHEMA_SUPPLEMENT 加可选参数 `project_choice`
  （不进 required、不参与判缺）
- `pipeline.py`：
  - `_get_user_projects(username)`：跨库查用户项目，缓存 5min（`_USER_PROJECTS_CACHE`）
  - `_ticket_tool_loop_branch` / `_diagnosis_tool_loop_branch`：拉列表 → system prompt 注入
    「用户名下项目列表」+ 照抄规则 → draft_ready 后 args 提取 `project_choice` 严格校验 →
    `_build_ticket(prefill_project=...)`；预填成功时兜底话术改为"项目已预填为「XX」（可在
    弹窗中修改）"
  - `_build_ticket` 加可选参数 `prefill_project`，生成后代码层覆盖 `project`/`project_id`
    （函数内 LLM prompt 仍固定空字符串，analysis 的 project 依旧被忽略）
- 兼容性：confirm_submit 对预填的标准全名 `_resolve_project` 精确命中（score=1.0），幂等不漂移；
  闭环 guards（last_ticket_context 无 project、context_start 跨成功消息）原样保留。
- 测试：`tests/test_ticket_submit.py::TestProjectPrefill`（严格校验 4 例 + build_ticket
  预填/默认空 + 预填后 `_check_required_fields` 不再缺 project），22 passed。

### 首触轮体感优化：工具循环过渡语（2026-08-20 补）

工具循环首触轮原本两段 LLM 往返全程无字（参数生成轮正文为空 → 判缺 → 追问轮才出字），
用户干等 3s+。改为**过渡语叙事化**：提示词要求每次调 submit_ticket 的同一轮先说一句
以冒号收尾的过渡语（「好的，我帮您转工单，我看一下还需要补充哪些信息：」/
「收到，我核对一下还缺什么：」），紧接工具调用。停顿被叙事覆盖——用户等的不是
"卡了"而是"正在查"，第二轮追问（「还需要一个联系电话」）正好是对过渡语的呼应。

- 基建零改动：tool_loop 同轮 content+tool_calls 的 token 本就随到随发。
- 红线进提示词：过渡语禁止完成时话术（已提交/已生成，嘴嗨教训 #11）、
  禁止播报项目预填（校验前播报会与弹窗口径不一）。
- **配套修掉一个潜伏重发 bug**：terminate 轮（草稿就绪）done 事件 final_text 恒为空，
  `final_streamed` 恒 False——过渡语一旦在终止轮说出，服务端收尾会整段重发、气泡双份。
  两分支（ticket/diagnosis）均改为累计 `_streamed_text`：已流式说过渡语时收尾只补
  `\n\n信息齐了，已生成工单草稿…` 尾巴（与预填播报单一信息源兼容——尾巴仍是
  校验后的服务端文案）。
- 生效条件：`AI_TICKET_TOOL_LOOP=1` / `AI_DIAGNOSIS_TOOL_LOOP=1`（当前环境未开，
  走老快路径，无过渡语需求——老路径单次 LLM 直接出字）。
- **生产首开实锤与补丁（#28）**：开关打开后触发轮模型只说过渡语就结束回合
  （0 次工具调用），气泡停在冒号上死寂。提示词补「同一次回复」硬性要求 +
  机制层空转纠偏（零工具调用注入纠偏指令重跑一遍）+ 空转轮置粘性续接，
  详见 #28 行。

### 弹窗回写验证（2026-08-20 补）

回写路径专项核对 + 3 个用例（`TestPrefillWriteback`，76 passed）：

| 场景 | 行为 | 结果 |
|---|---|---|
| 预填 + 不动项目直接确认 | draft 值直通入库，`_resolve_project` 幂等（全名自匹配 score=1.0） | ✅ |
| 预填 A + 弹窗改 B | 前端选项目时**成对**写 `project`+`project_id`（ChatPanel.tsx onChange），overrides 成对覆盖 | ✅ |
| 预填 + 只改名不带 code（双工单兜底名 / 直调 API） | **一致性护栏**：overrides 的 project ≠ 草稿且无新 code → 清掉预填残留旧 code，再由 `_resolve_project` 命中时重写 | ✅ |

第三列场景是真回归点：双工单勾选框不受 project_id 显隐控制，预填后仍可勾选；前端发
`project='摇人吧服务号提单', project_id=''`，后端空值跳过规则会保留预填旧 code，兜底名
若匹配不上项目库就错位入库（名字新项目 / code 旧项目）。旧版 draft.project_id 恒空无此问题。
护栏为一次单向清空（confirm_submit 内 overrides 应用后），无循环。

**测试基建债（记录不修，避免影响存量 73 例）**：conftest 的 `platform` fixture 把
`confirm_submit` 整个替换成简化 mock——空值也覆盖（真实现跳过）、无归一化、无护栏、
返回假工单结构（仅 4 键）。存量 override 相关测试（如 `test_button_confirm_override_merges`）
测的是 mock 行为而非真实现。`TestPrefillWriteback` 用 `platform_real_confirm` fixture 绕开：
恢复真实现 + sys.modules 注入假 `task_adapter`（隔离 MySQL，比 conftest 注释里"不能 patch"
的方案更彻底）+ `_resolve_project`/`_attach_chat_snapshot` 置 no-op。后续如修 conftest，
`_mock_confirm_submit` 的空值语义和返回结构应与真实现对齐。

前端配套（前端同事做，不在本仓库改动内）：弹窗项目下拉默认展示该用户名下项目。

## 2026-08-20 生产回退：工具循环撤出生产，项目预填移植到老快路径

生产开了 `AI_TICKET_TOOL_LOOP` 后体感太差（触发轮死寂 #28 → 补丁后双过渡语，
提单轮节奏不稳），用户拍板**不追求提单轮速度、撤开关回老快路径**：

- 生产服务器删掉 `AI_TICKET_TOOL_LOOP` 环境变量即回退；工具循环代码全部保留
  （flag-gated 休眠），#25/#26/#27/#28 修复对两条路径都有效。
- **项目预填照搬到老快路径**（用户唯一要求保住的能力）：
  - 取数条件：`ticket_fast_lane or ticket_collecting or ticket_draft` 任一命中才拉
    用户项目列表（普通问答轮零开销，缓存 5min 不变）。
  - 注入点：`_build_diagnosis_prompt` 三种变体全注入（收集轮小 prompt、快路径
    prompt、主 DIAGNOSIS_PROMPT 由草稿轮铁律附加），输出模板加平级字段
    `project_choice`。
  - 解析：`_parse_agent_output` 白名单 return 补 `project_choice` 键（**移植时
    踩的坑**：原实现重建 dict 只带白名单键，顶层新字段会被静默丢弃——测试
    实锤 draft 弹了但预填空）。
  - 单向管道不变：`pending_prefill_project` 进 AgentState（save/load 持久化），
    `_build_ticket(prefill_project=)` 覆盖 draft 的 project/project_id；三个
    build 入口（get_ticket / submit / prepare_ticket）都传；取消提单和
    `_reset_state_after_submit` 清空，不泄漏到下一单。
  - 预填播报：draft 生成后按校验后真实值说「项目已预填为「XX」（可在弹窗中
    修改）」，无预填时话术不变（仍是服务端单一信息源，LLM 不播报）。
  - 测试：`TestOldPathProjectPrefill` 5 例——快路径照抄进 draft+播报、幻觉
    近似名忽略、跨轮保留（轮1识别轮2提交）、取消清空、普通轮零取数。
    84 passed 全绿。
  - **照抄加固（live 实锤）**：LLM 偶尔把展示格式整行抄进 project_choice
    （「名称（编号: 69）」），精确相等会拒绝、预填靠碰运气。`_match_project_choice`
    补窄规则：列表项**完整 name 作为连续子串出现**即剥离取回——仍是精确值
    匹配（近似拼凑不含完整名照样拒绝），不违反宁空勿错。

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
