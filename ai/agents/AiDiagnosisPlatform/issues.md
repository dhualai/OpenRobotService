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
| 30 | 生产实锤（0825 同事报）：按钮提单显示「工单信息不足，还差 {'page module:'问题页、{'occurrence time:」——显示英文字段且残缺不完整 | LLM 把 required_fields 写成**嵌套对象**（`"page_module": {"page module": "问题页"}`），采纳代码 `str(v)[:20]` 把嵌套 dict 字符串化再截 20 字符，残片（`{'occurrence time': ` 以冒号断尾）被当中文标签存进清单、判缺提示原样显示 | `_sanitize_required_fields` 统一类型清洗：value 必须是非空字符串，含 `{`/`}`/引号的标签（合法中文短标签绝不会出现）判为残片丢弃；四处采纳点（`_apply_state_update` / `_compute_ticket_fields` 初次+重试 / `_adopt_ticket_fields`）+ `_load_agent_state` 加载共用——已污染的存量会话清洗为空归 None，重新 decide 自愈。字段数不足由既有 <2 不采信/重生成机制兜住。测试 `TestRequiredFieldsSanitize` 6 例，102 passed |
| 31 | 生产实锤（0825 用户报）：用户对话里明确说了「新车，XSC111，没路径」「无法移动」，转单后 AI 仍追问「车辆编号是什么」「具体故障现象」，用户被逼重答自己刚说过的话，答烦开始敷衍（用「无法移动」回答发生时间），最后收集轮超限强弹 | **转单首轮没人对照对话核查清单**：意图轮 4016 已提前 decide（清单可能把对话里说过的列为缺口，尤其同会话第二单切片含上一单 XSC151 噪音），门槛段判缺只看 collected_info——对话里已陈述的信息没有任何机制进 collected_info（`_backfill_collected_info` 专门干这个但只有 submit() 调它，对话路径在门槛处就拦下改追问，永远走不到；按钮路径 3130 又明确禁用回填防幻觉） | 门槛段判缺前（4112 区域）加转单首轮回填：判据「required_fields 非空 + 未进收集模式（ticket_collecting 空 and collect_rounds==0）」——此刻对话里没有服务端追问，不存在「提问当答案」误提取源（4116 注释的顾虑针对收集轮）；收集轮不回填（每轮已结构化提取）。decide 列字段与 backfill 提取对话证据两个 LLM 判断交叉仲裁，假缺口消掉只问真缺。测试 `TestBackfillOnFirstSubmit` 2 例（说了的不重问 / 收集轮不回填），104 passed |
| 32 | #31 的 decide 质量根因排查（用户问：判断字段的模型是 flash 不开思考吗？不开给的字段太蠢） | flash 关思考确实列已说过的字段；但**开 low 思考实测更糟（0825 live 数据）**：延迟 14.6-20.3s（超 15s wait_for——超时路径 `_decide_ticket_fields` except 锁空清单直接弹窗，行为倒退）、一次 rf=None（flash 把思考文本写进 content 非分离 reasoning_content，格式不稳）；字段质量跑偏成**AI 诊断排查项**（任务ID/错误码/定位坐标/地图版本——助手诊断文本里提的技术参数，用户根本答不出来） | decide/backfill 三处维持 `thinking=False`（1-2.5s 稳定），质量靠三层：(a) **删除 prompt 里的字段示例**（「如车辆编号、故障码」「如发生时间、现场位置」两组——live 实测删掉后发生时间/现场位置 3/3 不再出现，是抄示例的重灾区）；(b) prompt 铁律——用户已提及绝不列缺口、字段必须是用户（现场人员）能直接回答的信息（AI 侧排查参数不列）；(c) #31 的 backfill 交叉仲裁兜底——车辆编号删示例后仍 3/3 被列（不是抄示例，是 flash 常识认为工单必问，模型能力短板），只能靠 backfill 从对话提证据消掉。live 验证事故场景：decide 列 {车辆编号,故障场景,+1}，backfill 提出 vehicle_id=XSC111 + 故障现象=没路径，最终真缺只剩 1 个 |

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

## 2026-08-20 附件-工单绑定（只带本单相关附件）

需求：一个对话里生成工单，不再把会话里**所有**历史附件都带上——只附加与本次
提单过程相关的。难点场景：第一单提交后用户发图问了个问题、之后换话题提第二单，
那张诊断图不该混进第二单。

原设想（提单后状态切换切窗口 + problem_summary 变更轮放宽 + 弹窗手动加附件兜底）
经代码检验被否掉两点：

- **弹窗没有附件编辑 UI**（只渲染 标题/描述/优先级/项目）——漏选无法在弹窗补救；
- **话题锚点会误杀最常见流程**：「先发故障图、再描述问题」的图在锚点之前上传，
  会被窗口切掉；#26 的话题刷新又是尽力而为，锚点会漂。

落地为两层：

| 层 | 边界 | 机制 | 性质 |
|---|------|------|------|
| 机制层 | 跨单 | `_reset_state_after_submit` 把 raw `agent_state.attachments` 清空（写 raw dict 再过 `_save_agent_state` 透传；attachments 不在 AgentState 字段里） | 确定性，无判断 |
| 判断层 | 跨话题 | `_build_ticket` prompt 注入「附件候选清单」（文件名 + VLM 摘要截 120 字），输出 `attach_files` 序号数组 → 映射回条目 | 判断全交大模型 |

- **信号源**：router `/qa/upload` 写附件条目时把 VLM 摘要（截 160 字）存进 `desc`
  （多图批次共享同段摘要）——`_build_ticket` 的对话记录 `sanitize_images=True`
  物理屏蔽图片内容，没有 desc LLM 就没有取舍信号。desc 随条目透传进
  tasks.attachments（额外键，无害）。
- **降级 = 现状**：`attach_files` 缺失/类型不对/LLM 整体失败 → 全带（不静默丢证据）；
  显式 `[]` → 尊重（LLM 判定都无关）。序号宽松解析（字符串 `"2"` 也认），越界忽略。
- **配套不变量**：`_attach_chat_snapshot` 读 turns 不受清空影响；提单后 turns 归档，
  下一单的对话快照本就只覆盖新对话。补充轮重建草稿会重新取舍（每次 build 现选）。
- 测试：`TestAttachmentBinding` 6 例（消费清空 / 选择映射 / 字符串序号 / 空 [],
  缺字段全带 / 无附件零开销），94 passed。

## 2026-08-20 跨单预填泄漏（#30）

用户实测：同一会话连提多单，第二单把**第一单对话里**提到的项目名又预填上了。

根因：`context_start` 实际**恒为 0**（全文件只有初始化 0 / load / 提单后归 0，
从未被置成非 0——注释里「提单后前移」的设计从未接线），提单归档
`turns[context_start:]` 是空操作，第一单的「给XX项目提单」一直留在最近对话
窗口。第二单快路径 LLM 看到项目名在对话里 + 注入的名下项目列表 +「用户提到
就照抄」规则，无法区分那句属于已提交的上一单 → 照抄泄漏。

修复（判断仍全交 LLM，代码只提供分界事实）：

- `_reset_state_after_submit` 记**内容锚点** `ticket_boundary_prefix` = 提交时
  最后一轮内容前 40 字（内容锚点而非索引：turn buffer max_turns=10 截断会让
  索引漂移，内容匹配免疫）；AgentState 新字段，save/load 持久化。
- `_format_conversation` 加 `boundary_prefix` 参数：锚点轮后插分隔线
  「───── 以上对话已随上一张工单提交归档；以下是新对话 ─────」。锚点不在当前
  切片（from_turn/max_turns 截掉，全是新对话）时不插。
- `_proj_block` 照抄规则收紧：分隔线**之前**（含助手旧回执）出现的项目名不算
  本次提到、禁止照抄；只有分隔线之后**用户**明确提到（或明确指代「还是那个
  项目」）才照抄；无分隔线以全对话为准。
- 注：`_build_diagnosis_prompt` 三个变体共用 conversation_text，分隔线全量生效；
  `_build_ticket`/`_compute_ticket_fields` 的切片未传锚点（描述侧跨单污染是
  #26 同源问题，另行处理，本次不动）。
- 测试：`TestTicketBoundaryPrefill` 3 例（锚点记录 / 分隔线插入与截掉不插 /
  快路径 prompt 规则），97 passed。

## 2026-08-25 聊天记录附件四项修复

用户反馈四个附件问题，根因与修复：

1. **用户/U老师区分度差**（对话全连在一起）：`_turns_to_markdown` 每轮只有
   `角色：内容` 纯文本前缀。改为每轮 `---` 分隔 + 粗体角色 + emoji
   （👤 **【用户】** / 🤖 **【U老师】**）+ 可选时间戳（MySQL 源有 `created_at`
   显示 `MM-DD HH:MM`，memory 源省略）。附件预览是 ReactMarkdown 渲染，
   这些语法均生效（md 无法真上色，此为用户确认的替代方案）。
2. **补充轮只剩一个字**（当时显示完整，退出再进只剩单字，附件同）：真根因是
   **producer 落库竞态覆盖**。补充轮走 `_agent_think_stream` 兜底路径（pipeline
   4220：收集模式 `_suppress_msg` 吞掉流式正文后逐字符重发整段 message）——
   首字符触发 `_persist_bg` 的 `create_task`（快照=单字「已」），其余 27 字全被
   0.8s 节流挡住；流结束的最终全量落库先 `await` 让出事件循环，锁队列里那个
   单字 task 后拿到 `_persist_lock` 执行，**把完整内容覆盖回首字快照** → DB
   终态=「已」。拦截轮（4095）/review 轮（4194）整段一次 yield，节流快照与
   最终内容相同故不显形；普通诊断流渐进节流，覆盖差异小。修复：最终落库
   （含异常路径）前先 `gather` 排空 `_persist_tasks`，保证终态写入永远最后。
   asyncio 调度模拟复现验证：旧序 `['full','Y']`→终态 Y；新序 `['Y','full']`
   →终态 full。此前误诊的两层防御（status 清空刷新 last_persist、`_do_persist`
   重试换 session）保留作为纵深。存量脏数据（本次事故前的单字消息）由
   `_attach_chat_snapshot` 生成附件时自愈：DB 消息 ≤4 字且 memory 同角色消息
   以其为前缀 → 用 memory 完整版替换。

   **排查期间的意外发现（已修）**：`ai/api/router.py` 的 `[sse]` 与 tool_loop 的
   `[tool_loop]` 日志走 `logging.getLogger(__name__)` → root propagate 链路，
   在 uvicorn reload spawn 的 worker 进程里整类丢失（ai.log 一条没有，REPL
   单进程则正常）——本次事故的落库过程因此完全无法从日志取证，只能靠 DB/时序
   推理。两个模块已改 `get_logger()`（name="AI"，直挂 file handler、
   propagate=False，与 pipeline 同链路，生产验证可靠）。
3. **附件图片全裂**：`_rewrite_images` 把 KB 图改写为相对路径
   `{media_url_prefix}/kb/...`（挂在 AI 服务 8401），附件在 backend 前端
   预览/下载打开时该路径 404。修复：`_embed_kb_images` 后处理把 KB 图片
   读本地文件（`_KB_DIR`）内嵌为 base64 data URL——附件自包含，任何
   环境可渲染。缺失文件保留原 URL 降级；累计 2MB 预算后剩余保留原 URL。
4. **附件范围全量**（跨工单消息全进附件）：`_attach_chat_snapshot` 优先取
   MySQL 全量历史。修复：提单时间锚点——`AgentState.last_ticket_submitted_at`
   （`_reset_state_after_submit` 记录，两条提单路径共用），附件只取
   `created_at` **严格大于**锚点的消息（提单收尾话术归上一单）；锚点缺失
   （首次提单/老会话）回退全量。
   **上线首测又漏（时区）**：锚点过滤全灭、上一单收尾轮照混——DB
   `created_at` 是 naive UTC（后端 utcnow 写入），锚点
   `datetime.fromtimestamp()` 默认转本地 naive，两侧差 8h，所有消息恒小于
   锚点被全滤 → rows 空 → 回退 memory.turns（无分割）。修复：锚点用
   `fromtimestamp(ts, tz=utc)`、DB 侧 `fromisoformat().replace(tzinfo=utc)`，
   两侧统一 aware UTC（解析失败的消息宁可保留）。
5. **用户上传图片附件不可见**（用户实测：IMG_5344.jpg 在附件里"无法可见"）：
   用户消息 content 只存 VLM 文字描述（无 md 图片链接），图片本体在 MinIO
   附件条目（`agent_state.attachments`，object_path/filename/desc）里——
   聊天记录 md 天然"看不到图"。修复：`create_chat_markdown_attachment`
   加 `user_images` 参数，`_attach_chat_snapshot` 在 reset 清空前收集本单
   周期的图片条目传入；图片下载（`fget_object`，`asyncio.to_thread` 包）
   → base64 内嵌进 md。预算与 KB 图共享 2MB；失败/超预算降级文字。
   **部署纪律（0825 生产实锤）**：曾在生产只部署 pipeline.py（新版 import
   is_image_entry）而 chat_snapshot.py 是旧版 → ImportError → 两单（578/580）
   附件整体消失。pipeline.py 与 chat_snapshot.py **必须同批上线**（代码里
   有标注，不做运行时免疫——用户拍板，部署靠纪律保证）。
6. **工单详情里 base64 图片全部不显示（前端根因，0825 用户实锤）**：附件 md
   下载到本地 IDE 打开能看到图，工单详情预览看不到。根因不在 md：前端
   react-markdown v9+ 默认 `defaultUrlTransform` 协议白名单只有
   http/https/irc/ircs/mailto/xmpp，**data: 一律返回空串**（node 实测
   `defaultUrlTransform('data:image/png;base64,...') === ''`）→ img src 被清空
   → 裂图。受影响的不止用户图，KB 图内嵌（问题 3）在工单详情同样全灭。
   修复（前端 4 处）：新增 `frontend/src/shared/utils/markdown.ts` 的
   `urlTransformAllowDataImage`（仅放行 `data:image/` 前缀，不放开任意
   data: 协议），三个 ReactMarkdown 使用点（AttachmentViewer md 预览 /
   MarkdownRenderer / TaskDetailPage 诊断报告）统一传 `urlTransform`。
7. **图片位置（0825 用户报：图片堆在 md 末尾「对话中的图片」节，应在上传
   那轮的位置）**：原实现 `_append_user_images` 把所有图追加末尾。改为
   **按上传轮内联**：router 上传时用户消息 content 固定含文件名
   （「我上传了 N 个文件：['x.jpg']。图片主要内容为：…」，router.py:785），
   `_turns_to_markdown` 渲染用户轮时按文件名匹配 content，原图 base64 直接
   插在该轮内容后（desc 已在消息里不重复）；匹配不到的条目（上传轮被窗口
   截掉）降级末尾节（带 desc 引用）。下载拆出 `_prepare_user_images`
   （to_thread），渲染拆出 `_render_image_md`；`_turns_to_markdown` 返回值
   变 `(md, 剩余预算)`，预算顺序改为用户图先扣、KB 图接力。
8. **单字截断复发 + md 三轮同文（0825 10:46 生产实锤，用户日志取证）**：
   (a) 补充轮 AI 追问后两轮 DB 又只剩「好」——**旧竞态未修**，因为单字
   排空修复（gather `_persist_tasks`）和 [sse] 日志修复都只在 router.py，
   生产只部署了 pipeline.py + chat_snapshot.py。铁证：日志里 10:46:29-
   10:47:04 三轮请求**一条 `[sse] sid=... q=...` 入口日志都没有**
   （新版 router.py 每轮必打一条 INFO）；pipeline 日志全在说明服务与 AI
   logger 正常。(b) md 里三轮追问显示成第一轮的原话——LLM 实际输出三句
   不同的话（msg_preview 各异），旧版「截断自愈」拿 ≤4 字 db 残片在
   memory 里**搜第一条前缀命中**，AI 连续追问都以「好的，」开头 → 后两轮
   全被填成第一轮的完整内容。修复：自愈与尾部对齐合并为 `_same_turn`
   位置对齐——db 与 memory 按序一一对应，前缀（短侧 ≤4 字）只做「同一轮
   被截断」的**确认**（用 memory 对应位置的完整版替换），绝不做搜索；
   memory 窗口外的截断保留原样（宁显残缺不编造）。verify 用真实事故内容
   （三轮原话 + 两个「好」残片）验证三轮各自恢复。**部署清单：router.py
   必须随 pipeline.py 同批上线并重启**（单字根治在 router，md 恢复在
   pipeline）。
9. **图片内联修复上线后图片仍落末尾（0825 用户实锤，问题 7 的真根因）**：
   内联靠「上传轮 content 含文件名」定位，但**上传轮根本不在最终 turns
   里**——三层丢失链：(a) 上传走 `/qa/upload` 只 `mgr.add_turn` 写 Redis
   （router.py），MySQL 消息表由前端每轮 appendMessage 写，上传轮天然
   不落库；(b) Redis 是 10 轮滑动窗口（memory.py add_turn 后
   `turns[-max_turns:]`），画地图单上传后又有 12+ 轮对话，提单时上传轮
   早被滑出窗口——用户贴的 md 对话从「这个图画的怎么样」开始、无「我
   上传了…」轮即铁证；(c) 旧 md 是提单时一次性生成存 MinIO 的静态文件，
   旧结构永不重生。即使部署了内联修复，新提的单照样落末尾（匹配必然
   落空 → 兜底节）。修复：上传时随附件条目记 `uploaded_at`（naive UTC
   ISO，对齐 DB created_at 口径）+ `upload_message`（与 add_turn 同一
   原文）——附件条目在 metadata，**不受窗口截断**；`_attach_chat_snapshot`
   读 MySQL 后按批次重建合成用户轮、按 created_at 插回 db 历史（早于
   工单分割锚点的批次随锚点过滤归上一单；仍在 memory 窗口内的上传轮，
   对齐后天然只出现一次，对齐中途断裂时去 db 前缀副本保 memory 版）。
   合成轮 content 含文件名 → 内联匹配命中，图片插回发图那轮。老附件
   条目（无 uploaded_at 字段）不重建，向后兼容。**注意：老会话的旧条目
   没有这两个字段，图片仍落末尾——只对修复部署后的新上传生效。**
   展示口径（用户拍板）：「我上传了 N 个文件：…」是 router 注入给 LLM 的
   上下文文字，对话界面这轮显示的就是图片本身——`_turns_to_markdown`
   对上传轮（「我上传了 」/「[上传了附件] 」开头）命中图片时**只渲染图
   片**，注入文字与 desc 都不渲染（memory 窗口内的真上传轮同一规则）；
   没命中图片的批次（日志压缩包等非图片）保留原文避免空轮。

**顺带修的既有 bug**：`db_turns[:matched] + mem_turns` 拼接在 MySQL 完整时
把 memory 窗口前段重复一遍（附件出现重复轮）。改为从 matched 向前顺序延伸
找 memory 窗口在 db 里的起点（`first_idx`），起点前用 db、之后整体用 memory。

- 测试：`python -m ai.tools.verify_attachment_fix`（格式/KB图内嵌/用户图内嵌/
  分割/自愈/无重复，离线 patch 不动 DB/MinIO）全过；回归 96 passed
  （1 个 `test_fast_lane_prefill_into_draft` 为 stash 验证过的既有失败，与本次无关）。

## 2026-08-25 跨单字段污染：decide/backfill 读了上一单对话（#588 实锤）

用户测试单 #588（AI 平台图片分析 bug），decide 第二轮补充字段问
「任务编号和末端站点名称」——快递物流字段，来自**上一单**的补充轮问答。

**根因**：提单归档只切 `turns[context_start:]`（丢上上单），**上一单自己的
对话（含补充轮问答）保留在 turns**（续接轮指代解析「还是上次的车」要用，
不能清）。`context_start` 只在提单后归零、新问题开始时从不前移 →
decide（`_compute_ticket_fields`）/backfill（`_backfill_collected_info`）
的对话切片 `from_turn=context_start=0` 从头读 → 上一单补充轮里问过的
「任务编号」「末端站点」平铺在本单对话前，flash 把它们当本单「对话中
还没说过的信息缺口」列出来问。backfill 同通道更糟：上一单的**字段值**
（任务编号=KDxxx）可能被直接回填进本单 collected_info（串单）。

分界机制其实早就有：`ticket_boundary_prefix`（提交锚点，content 前 40 字）
+ `_format_conversation(boundary_prefix=...)` 在锚点轮后插分隔线
「───── 以上对话已随上一张工单提交归档；以下是新对话 ─────」——但**只有
项目预填传了**（防跨单项目泄漏，#30），decide/backfill 都没传。

**修复**（pipeline.py）：
1. `_compute_ticket_fields` 加 `boundary_prefix` 参数，调用方从
   `agent_state.ticket_boundary_prefix` 传入，切片插分隔线
2. decide prompt 加铁律：分隔线之前是上一张已提交工单的旧对话，那里
   出现过的字段/问答与本单无关，**严禁照着旧对话的字段样例列本单待补
   字段**，缺口只从分隔线之后判断；无分隔线以全对话为准
3. backfill 同样传锚点 + 铁律：上一单问答里的值**严禁提取为本单字段值**
   （除非分隔线后用户明确指代「任务号和上一单一样」）

锚点轮被 Redis 窗口滑掉时不插线（此时窗口内全是锚点之后的轮，无需
分界），铁律的「无分隔线以全对话为准」兜底——设计自洽。

- 测试：`TestDecidePrevTicketIsolation`（test_ticket_submit.py）——归档后
  真实 turns 形态（上一单补充轮问答→锚点轮→本单对话），捕获真实 prompt
  断言：分隔线在锚点轮后、本单对话前，旧问答在线上方，铁律在场。
  2 passed；回归 55 passed（1 个既有失败同上）。

### decide prompt 重构：角色 + 推理步骤 + analysis CoT（2026-08-25 补）

用户要求增强待补字段质量（规范 prompt + 思维链）。**思维链形式拍板**：
prompt 内 `analysis` 字段（输出 JSON 先写 4 步分析再出字段），**不是**
thinking 模式——0825 实测 thinking 15-20s 超 15s timeout 且一次字段跑偏，
analysis 只 +2~3s 且推理过程进日志（`[compute_fields] analysis:`）可归因。
历史工单 few-shot 注入（查同类工单字段做参考）暂缓，本批不做。

新 prompt 四段式：`# 角色`（工单信息架构师）→ `# 推理步骤`（①问题域
→②开工要素→③对照对话筛已说/未说→④定字段，analysis 每步一行共 4 行，
限制长度防拖慢）→ `# 红线`（🔴 全部铁律保留原句，含分隔线隔离/两层
2-4 个/一项一字段/只列未说缺口/用户能答/项目不进）→ `# 输出`（单 JSON
含 analysis/ticket_type/required_fields）。重试 prompt 同步改为「重走
推理步骤（尤其第 2、4 步）」。解析向后兼容（旧格式无 analysis 也能取
字段）。

- 真实 LLM 冒烟（`python -m ai.tools.smoke_decide_prompt`，#588 场景 +
  分隔线）：analysis 4 步完整可见且推理正确，字段「触发入口页面/复现
  图片示例/操作步骤」贴题，物流字段（任务编号/末端站点）零残留。
- 回归 55 passed（1 个既有失败同上）。

### decide 字段与已锁定问题不匹配：通用模板凑数（2026-08-25 补）

用户实测：充电桩不伸出，排查已锁定「换桩正常→单个充电桩硬件问题」后
提单，decide 仍按通用设备工单模板问「车辆编号」——修桩不修车，字段
与已收敛的问题无关。

**修复**（prompt 两处）：
1. 推理步骤第 1 步加「排查是否已锁定到具体部件/单点」；第 2 步开工要素
   强调「问题已锁定到具体部件时，要素只围绕该部件收敛」（示例：锁定了
   单桩硬件就只要桩编号/位置和现象，车辆编号不列）；第 4 步挑选条件
   增加「且与处理该问题直接相关」
2. 红线新增：「字段必须服务于**本问题**的定位/复现/处理，不是同类工单
   的通用模板——对话已锁定问题部件/原因时，只收处理该问题所需信息，
   『这类设备工单通常都收 XX』不构成理由（修充电桩不需要车辆编号）」

真实 LLM 冒烟（充电桩场景，decide 触发于「新提一个工单吧」）：字段
收敛为「故障桩编号位置/故障是否持续/故障发生时间」，车辆编号消失。
中文 key 偶发（桩编号或位置）但全链路按字符串匹配可跑通，不拦。

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
