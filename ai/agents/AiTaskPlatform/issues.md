# AiTaskPlatform 事故台账

> 与 [待做.md](待做.md) 分工：**待做**记「还要做什么」；本文件记「已经踩过什么坑、根因是什么、怎么修的」。
> 唯一目的：同一个坑不踩第二次。新增条目按日期开章，用「问题 / 根因 / 修复」三列，修复必须带证据（提交号 / 测试名 / 日志特征）。
> 写法参照 [../AiDiagnosisPlatform/issues.md](../AiDiagnosisPlatform/issues.md)——那份 400 行的 #1–#30 更细，**值得一并读**，我们下面有 4 条教训就是从它那里抄来的。
> 本文件以 AiTaskPlatform 为主；跨平台、或真实位置在诊断侧的条目集中在最后一章，**标明真实位置，不冒领**。

## 当前基线（先看这几条，能少走弯路）

| 项 | 事实 |
|----|------|
| 测试 | `python -m pytest -q` → **940 passed, 27 skipped**（另有 2 个 `dual_tree` 既有失败）。跳过的是需要本地 Qdrant 数据的检索用例。变更轨迹：移除自评机制（#22）−58 条、工单状态（#23）+22 条、Supervisor 追规（#24）+13 条、编排补强（#25）+28 条、回归信号（#26）+24 条 |
| 进程模型 | [../../run.py:446](../../run.py:446) 的 `uvicorn.run` **没有 `workers=`** → **单进程**。所以「进程内缓存/快照」方案成立（[runtime/inject_mailbox.py](runtime/inject_mailbox.py)、[runtime/report_cache.py](runtime/report_cache.py)），但也意味着**重启即失** |
| `.env` | 未纳入版本控制（`.gitignore:22`）。改了必须**手动改 + 重启 AI 服务**，`load_dotenv` 只在启动时读一次 |
| 过程区数据契约 | 后端 [tracing/trace.py](tracing/trace.py) 的 `nest_progress_todos` 产出「分析 →（规划 + 各能力）→ 子步骤/事件」；前端 `frontend/src/shared/components/DiscussionPanel.tsx` 的 `ProgressTodoItem` **递归渲染 `children`**，后端行自带的 `status`/`phase` 优先于 span 状态 |
| 两个 Agent 的关系 | AiTaskPlatform 与 AiDiagnosisPlatform **零交叉导入**，同一件事存在两份独立实现（见最后一章「刻意不共用」） |
| 怎么验证 LLM 行为 | **单跑一次 pass/fail 不构成结论**——`deepseek-v4-flash` + `temperature=0` 也能给出相反判定（实测同一输入 6/6 与 0/8 两种极端，见 #1–#3）。看**多轮稳定性**，不是看单次判定。纯函数逻辑用单测，不需要 LLM。（原 `eval/` harness 已随自评机制一并移除，见 #22） |

## 2026-10-11 讨论问答：初稿总被自评判不通过（#1–#3）

现象：讨论区里 AI 的思考过程明显正确，但最终答复反复被 Evaluator 打回重写。

| # | 问题 | 根因 | 修复 |
|---|------|------|------|
| 1 | 自评判据套的是「诊断报告」口径，与讨论问答场景不匹配 | 判据生成没有按场景适配 | 判据按场景适配（`capabilities/core/evaluator.py`，测试 `capabilities/core/test_evaluator.py`） |
| 2 | evidence 里漏了讨论史，判据拿不到判断依据 | 拼 evidence 时只取附件与检索结果 | 讨论史补进 evidence |
| 3 | 判错时把本来正确的回答覆盖掉了 | 重写路径无条件采纳自评结论 | 判错时保护原稿 |

**教训**：`deepseek-v4-flash` + `temperature=0` **仍然没有确定性**——同一输入实测出现「6/6 不通过」与「0/8 不通过」两种极端。**pass/fail 计数不能当验证手段**，要看多轮稳定性（诊断侧的做法值得抄：给场景编号，跑 3 轮看稳定性，见 [../AiDiagnosisPlatform/issues.md](../AiDiagnosisPlatform/issues.md) 结尾）。

**2026-10-12 用新建的 [eval/](eval/) 复核（含负结果）**：12 条用例 × 2 轮 = **24 次判定、12/12 两轮同判、20/20 条评审意见复核有落点、0 复核失败**，说明这几处修复目前站得住。但复核同时暴露两件事：

- **当初那个故障模式现在复现不出来**：把判据换回诊断口径（`mode="diagnose"`）、甚至把护栏 `_JUDGE_RULES` 整个抽掉，这些用例**照样全部通过**；专门构造「证据塞 8 条无关话题 + 草稿只答一句」的极端输入，三种判据配置也全判通过。所以**手写的陷阱没有区分力，我们还没有真正的回归网**——要建，得从生产日志里捞真实的 evidence/draft 对与当时的判定。
- **量法也换了**：原本按「期望通过/不通过」算准确率，这是错的工具（标准随提问变），已删除。现在只量①判定稳定性②每条评审意见的落点，且落点**用 prompt 复核**（`Evaluator.verify_issues`）而不是字符串匹配——实测机械比对在这件事上 20 条一条都核对不上（换词复述算成没落点、常用词又凑出假重叠）。

**后续（2026-10-14，见 #22）**：以上三条修复后来被**整体移除取代**——判据分场景、补 evidence、
保护原稿，都只是在修一个本就不该存在的机制。`eval/` harness 也随之删除。

## 2026-10-12 评论作者名全部丢失：`【】正文`（#4）

**现象**：讨论史每条评论渲染成 `【】正文`，LLM 分不清哪句话是谁说的，讨论结论容易张冠李戴。

**根因**：`TaskComment` **没有 `created_by_name` 列**（后端 API 的该字段是派生值），`getattr(c, "created_by_name", "")` 恒为空串；而 `task_comments.created_by` 里存的是 `wechat_{openid[:10]}`。这行 `getattr` 在 5 处出现，全是死代码。

**修复**（`92da3e84`，测试 `contexts/test_comments_author.py`）：用 `UserService.get_user_map()` 反查真名——它的 key **同时包含 id 和 username**，所以微信 ID 与内部账号都能命中；映射里没有的作者保留原始 ID（不吞掉，便于排查）；完全没有作者时占位 `【?】`。

**教训**：`getattr(obj, "field", "")` 这类「防御性读取」会把「模型里根本没有这个字段」伪装成「这里暂时没值」，静默死代码就是这么长出来的。**取字段前先确认模型/接口里真有这个字段。**

**后续（2026-10-14）：未实名账号加保护。** 上面这条修复只解决了「能还原真名的人」。
**没填实名的账号**仍会把指纹交给模型 —— 映射的取值是 `name or username or user_id`，
所以未实名时值会是 `wechat_{openid[:10]}`（username 形式）**或裸 openid**（users.id 形式）。
现在一律代称「（微信用户N）」，**同人同号、不同人不同号**；不编名字（会与真人重名）、
不写 openid（那是指纹）。口径与诊断侧数据集抽取一致（本文件 #21）。
两种形态都要挡 —— 裸 openid 不以 `wechat_` 开头，最容易漏。
「映射里没有这个人」仍**保留原始 ID**（映射加载失败时全部作者走这条路，
把原始 ID 藏起来等于把故障也藏起来）。测试 `contexts/test_comments_author.py`。

同一次改动还补了两处**让整片退化显形**的地方 —— 真正的优先级是「**有名字的必须一直能读到**」，
指纹泄漏反而是次要的，所以重点放在这条告警线上：

1. `comments._author_display_map()`：映射整片拿不到时打 **ERROR**（原来只有一行 warning）。
   映射一失效，**所有**作者（含有名有姓的）都退化成 ID，模型又开始猜人 —— 就是本条 bug 换形式复现。
2. `user_service._refresh_cache()`：**空结果不缓存**。原来一次异常读到空表会被缓存
   10 分钟（`_CACHE_EXPIRE_SECONDS`），期间姓名整片失效；改为保留旧缓存、不更新时间戳、下次重试。

## 2026-10-12 两处「以为只剩前端 / 以为已经做了」的判断失误（#5–#6）

| # | 我原来的判断 | 实际 | 真相怎么暴露的 |
|---|------|------|------|
| 5 | [待做.md](待做.md) 写 P3b「后端已全量挂 span，**只剩前端**把父子树渲染出来」 | 前端 `ProgressTodoItem` **早就递归渲染 `children`**；真正缺的是后端 `nest_progress_todos` 只按 span **名字**把事件挂到能力行下，没照真实层级重排 | 读前端 `applyAiProgress` → `setAiTodos(incoming)` 原样上树，没有任何拍平 |
| 6 | [待做.md](待做.md) 有 4 条写着「没做」 | 全部**已做并接线**（审批策略、7 步命令、P4 双队列、车型与调度版本注入日志子 Agent） | 逐条 grep 代码 + 调用点 |

**教训**：「文档说没做」不等于没做；「只剩前端」这种**把工作量判在别人身上**的结论尤其要先读对方代码再下。修复提交 `7f34e384`、`68c61bcd`。

## 2026-10-12 `@#` 引用拿不到「帮我分析」报告（#7）

**现象**：讨论区用 `@#` 引用一个工单时，用户常问「他上次那份分析报告说了啥」，而报告**不落库**，事后谁都拿不到，LLM 只能编。

**根因**：diagnose 每次现算报告、有意不落库（报告是那一轮的结论，入库会带来过期与污染）；原实现的 docstring 写着「故不注入、也让 LLM 不要假装看到了它」——**但这条口径从没真正发给过 LLM**，只存在于注释里。

**修复**（`7f34e384`）：新增 [runtime/report_cache.py](runtime/report_cache.py) 留**进程内快照**（TTL 6h / 最多 200 份 / 单份 2 万字），`format_referenced_tickets` 命中即注入原文并标注「本次进程内快照，未落库」；拿不到时注入明确口径要求**如实说明「报告不落库，只能重新点一次『帮我分析』」**。测试：`runtime/test_report_cache.py`、`contexts/test_ref_report.py`。

**教训**：**只在注释里写「不要让 LLM 假装看到了它」，等于没写**——约束必须真的进 prompt（诊断侧 #11/#12 是同一族问题的另一端：服务端拦截了 LLM 的话术，就必须由服务端补发修正/成功消息）。

## 2026-10-12 视觉链路全线 404：`Model not exist.`（#8）

**现象**：生产 10-12 起所有 vision 请求 404，`analyze_images` 报 `ServiceUnavailableError`，任务 Agent 降级成「图片未能解析」。

**根因**：`VISION_MODEL` 指的模型**被下线**了。

**教训（一次被日志推翻的判断）**：我先断言「这是一次从没成功过的切换、URL 拼错了」，随即被日志推翻——40 次 vision 请求全部使用该模型名，其中 09-16 / 09-18 / 09-20 / 09-29 共 **37 次成功**，只有 10-12 才 404。**「配置看起来不对」推不出「从没成功过」，日志能给出反证。**

**修复**：`.env` 改 `VISION_MODEL=qwen3-vl-plus`（**不入库**），用真实截图端到端跑 `analyze_images()` 验证（1 张 23s / 3 张 36s，能准确读出故障码与外设表）。选型靠实测：该账号 `/v1/models` 只列 3 个 VL 模型，`qwen3-vl-plus` 能看图，`qwen-vl-ocr` 只回纯 OCR 文本（无画面理解）。

## 2026-10-12 死代码清理与文档同步（#9）

净 **−147 行**（18 文件，`68c61bcd`）：`DISCUSS_*_KEYWORDS`/`TEXT_ATTACH_EXTS`/`query_matches`、`MAX_ROUNDS_PER_TASK`/`SupervisorError`、`TodoList.next_pending/all_done`、`AiTaskAgent._save_analysis_context/_index_solution`（含随之只写的 `self._memory`）、`_TS_JSDOC_RE`、`_split_level_body`/`LogIndex.count_in_window`、`SemanticCodeIndex.position_of`、`ManualGuide.relevant_signals`、`get_resolution_worker`，以及 12 个未用 import（用 Pylance `source.unusedImports` 做的）。

**注意**：删 `_index_solution` **不是功能回归**——方案入库的闭环由 `ai/core/knowledge_worker.py` → `solution_sink.process_ticket` → `retriever.index_task_resolution` 承担，仍然活着。

**教训**：删之前先确认「是不是有另一条路承担了同一职责」，否则会把唯一实现删掉。

## 2026-10-12 配额从未生效：`max_usage_per_session` 是死代码（#10）

**现象**：`project_info` 与 `log_analyze` 都声明了 `max_usage_per_session = 3`（[capabilities/tools/project_info.py:395](capabilities/tools/project_info.py)、`capabilities/log_analyzer/log_analyze.py:58`），看起来是个硬上限。

**根因**：计数器 `_usage_count` 曾是**能力实例**的属性（[capabilities/core/base.py](capabilities/core/base.py)，本次已删），而**没有任何一条取用路径复用实例**——[capabilities/core/registry.py](capabilities/core/registry.py) 的 `get()` 明确「每次新建」（`cap_cls()`）；supervisor 每次派发前都重新 `get`（[capabilities/core/supervisor.py](capabilities/core/supervisor.py) 的 `_run_one`）；`discuss_flow` 的兜底补派也一样（[handlers/discuss_flow.py](handlers/discuss_flow.py) 的 `_ask_reanalyze_log`）。实例用完即弃 → `_claim_usage()` 每次看到的 `_usage_count` 都是 0 → **上限永远达不到**。

实测（同一份代码）：

```
复用同一个实例        -> [True, True, True, False, False]   # 机制本身是好的
真实的调用路径        -> [True, True, True, True, True]     # 永远不拦
```

`git log -S max_usage_per_session` 显示它随首个公测版本 `f3cd531f`（2026-08-23）进来，**至今没有生效过**。全仓也没有任何测试提到配额。

**为什么一直没被发现**：唯一可能造成实例复用的钩子 `_instantiate` 从来没有被任何能力实现过（registry 的 docstring 里写着这件事，那个死分支已被删掉）；而「配额」这种机制**不生效时是静默的**——没有报错、没有日志，只是那个上限不存在而已。

**遗留的错误描述（已一并更正）**：

- [待做.md](待做.md) §7 写「同一轮最多调 3 次」并标为已落地——既不是「同一轮」，也**根本没生效**，已移回「还没做」并写明原因（本次已真收口，见下）；
- `registry.py` 的 docstring 至今宣称有 `get_singleton()` 复用单例，**该方法全仓不存在**，已改成警告。

**已修复（真收口）**：机制下沉到 supervisor 的机制层——

- 新增 [capabilities/core/usage.py](capabilities/core/usage.py)：台账挂 `runtime_ctx`，**按请求**计数（不再挂实例）；
- `registry.limit_of(name)` 直读类属性，不实例化能力；
- 三道拦：`_guard_decision` 把超出上限的计划步骤**直接不排**；`_dispatch_group` 派发前 `claim`，用满就**不执行**；`run()` 把已用满的能力从可选集里 `drop_exhausted` 摘掉；
- 被拦下时写一条**明确指令**进汇总正文（`_synthesize(results, notes)`），让整理答复的模型给出**有界**结论，而不是以为还有更深的分析；
- 删掉 base.py 里那套挂在实例上的 `_usage_count` / `_claim_usage`（它不可能生效），并把「配额由谁管」写进注释，防止后人再挂回实例上。

测试 [capabilities/core/test_usage_quota.py](capabilities/core/test_usage_quota.py) 7 例，其中一例专门锁「换个实例不能让计数归零」这条回归；全量 `862 passed, 27 skipped`。真实能力实测：`project_info`（上限 3）→ `[True, True, True, False]`，`drop_exhausted` 只摘它一个，说明进得了最终正文。

**边界（有意）**：只管 **LLM 自主调度** 的次数。`discuss_flow` 里代码确定性的兜底调用（按用户要求补做一次日志分析）不计入——那是产品承诺，不该被配额吃掉。

**教训（本条最值钱）**：把软约束写成代码，不等于它就是硬的。**声明一个上限，必须同时有一个测试能证明它会拦**——否则它只是一句写错位置的注释。诊断侧的同类教训是反方向的：prompt 里的软约束换个模型就失效，最终靠**物理移除工具**才收口（见 [../AiDiagnosisPlatform/issues.md](../AiDiagnosisPlatform/issues.md) 的「死磕保险」）。

## 2026-10-13 判官自己的输出被截断 → 自评静默跳过（#20）；未实名账号没有名字可用（#21）

> 这两条都是**改用生产真实用例评测后第一次跑出来的**。手写的 12 条用例跑 3 轮干净得像没问题的代码；
> 换成真实材料，第一轮就掉出来一个生产 bug。**这就是「用例要有区分力」的实际回报**——
> 手写用例没有区分力时，该做的不是把尺子擦亮，是换一把。

### #20 判官输出被 `max_tokens=200` 截断

| | |
|---|---|
| 问题 | 真实数据集跑 40 轮判定，**4 轮**打 `[evaluator] 自评解析失败`（10%）；连带复核也记成「没跑成」（这 4 轮复核本来就是 None） |
| 生产后果 | 解析失败按设计是 **fail-open**：直接用初稿、只打一个 trace 标记（见本文件开头「设计约定」）。所以这 10% 的回答**根本没被自评过**。更糟的是它和严重程度**正相关**——问题清单越长（回答越糟）越容易撞上限，**最该被拦的回答最容易跳过审查** |
| 根因 | `Evaluator.judge()` 的 `max_tokens=200`。判官输出是 JSON，问题清单长时自己被截断：实测 raw=396 字符，断在 `"reasoning": "…断言根因是锁` 之后，**没有闭合 `}`** |
| 证据 | 2026-10-13，**同一份 20 条真实用例**走生产同一条 `judge()` 路径对照：`cap=200 → 未解析 3/20`（日志全部 `truncated=True`，长度 335～379）、`cap=800 → 未解析 0/20`。另一组探针在同一条用例上复现：cap=200 断、cap=800 正常；20 条实测输出 126～448 字符，正常态离上限很远，只有清单长时才撞顶 |
| 修复 | `Evaluator.JUDGE_MAX_TOKENS = 800`（带说明）；解析失败日志补 `len=` 与 `truncated=`——原来只打一句「自评解析失败」，连是截断还是格式跑偏都看不出来（上面 A/B 那三行日志就是新日志的第一批受益者） |
| 复核 | 改后跑 2 轮基准：**未解析 0/40**；改动前的旧数据集上那一轮是 4/40（含一次「翻转」，也是截断造成的——判官返回 None 被记成一轮缺失） |

**为什么之前没人发现**：手写用例的草稿都短、判官问题清单基本为空（0～1 条），从来没撞过上限；
而失败方式是**静默的**——不抛错、不阻塞，只是那条回答没人评。

> **后续（2026-10-14，见 #22）**：这条修复本身是对的（判官输出不该被截断），但它修的是一段
> 已被整体删除的代码。**唯一仍然有效的是教训本身**：静默失败的机制不会自己暴露——
> 声明一个上限/阈值，必须同时有一个能证明它被撞到时会怎样被发现的观察点。

### #21 未实名账号没有名字可用（抽取侧）

| | |
|---|---|
| 问题 | 「真名保真」导出材料时，**未实名的账号**（`users.name` 为空）没有名字可用 |
| 两种错法 | ① 用 `user_service` 的显示名（`name or username`）→ 微信账号的 username 就是 `wechat_{openid[:10]}`，等于把指纹当名字写进语料；② 全写成一个「（微信用户）」→ **两个不同的人合并成一个人**，讨论读成「一个人自相矛盾」，判官据此挑出的问题全是假的 |
| 事实 | `users` 291 行里 **8 行 name 为空，全是微信账号**；`task_comments` 5987 条里 **87.2%** 的作者是 `wechat_…` username（当前都能解析出真名，所以现在没暴露——一有人未实名就会） |
| 修复 | 统一「（微信用户N）」占位，**同人同号**：`wechat_<openid[:10]>`（username 形式）与裸 openid（users.id 形式）按前缀/openid 头 10 位认成同一人；两种模式都不编名字、不写指纹。`_names_from_map` 不再把 `wechat_…` 当人名登记。映射整体加载失败时所有作者按同一条路降级并打警告（不能出现「一半真名一半占位」的混合物） |
| 顺带修掉自己写出来的 bug | `scrub` 原来按「已登记串」做子串替换，短的 `wechat_oD5oY3abc` 命中更长的 id 后**残出 `defgh`**（把 openid 尾巴留在正文里）。改成整串匹配 + 词边界，测试 `test_masking_a_wechat_id_never_leaves_a_tail` |
| 边界 | 正文里出现**未登记的裸 openid**（既不是已知作者、也没带 `wechat_` 前缀）不会被替换——没有可靠的识别方式，硬匹配长串会把日志里的 hash 一起换掉。当前 20 条真实用例里实测 0 次（含 8 个未实名账号的 openid 与头 10 位） |

> **后续（2026-10-14，见 #22）**：本条发生在**已删除**的抽取脚本里，但结论对生产同样成立——
> 讨论史里**两个不同的人被合并成一个人**（或反过来，一个人被拆成两个），LLM 读到的就是
> 「一个人自相矛盾」，据此得出的结论全是假的。`contexts/comments.py` 的 `author_display_name()`
> 遇到映射里没有的作者**保留原始 ID 不吞掉**，就是同一条道理：宁可显示得难看，不可张冠李戴。

## 2026-10-14 自评（判官）机制整体移除（#22）

**现象**：讨论区问「两个人的分歧是什么」，**过程区（流式草稿）里 U老师 正确列出了两人观点，
最终回复却说**「证据里只有【用户】描述现象和【U老师】分析根因，并没有出现"两个人"的讨论或分歧」。
（事故 2026-10-11 22:37。）

**根因（完整链）**：

1. 生成时 prompt 里有完整讨论史 → 草稿正确（用户看到的就是这段流式草稿）。
2. 自评时判官只拿到 `facultative[:1500]`（能力派发结果），**讨论史根本不在里面** → 判「幻构」。
3. 重写拿着同一份残缺证据、又被要求「不要新增证据之外的事实」→ 写出「没有两个人」这句。
4. 前端收 `rewrite` 事件，把用户刚看着长出来的草稿**整段替换**掉。

**决定：整体移除**（不是修判据）。理由——这机制不是「判得不够准」，是**结构上就不该存在**：

| # | 事实 | 证据 |
|---|------|------|
| 1 | **判官比生成者更弱**：同一个模型 `deepseek-v4-flash`，生成者 `thinking=enabled`，判官 `thinking=disabled`（`complete()` 默认 `thinking=False`） | `ai/core/llm.py:450,103-104` vs `:989-990` |
| 2 | **判官的信息严格少于生成者**：生成者看得到描述/讨论史/引用/能力/记忆/画像；判官只拿到 `工单标题+用户问题` + `evidence[:2000]`，**工单描述完全没传** | `prompts/prompts.py:132-171` vs `handlers/discuss_flow.py` 原 1313-1325 |
| 3 | **讨论区的修正分支是死代码**：`mode="discuss"` 硬编码 → 必返回 `rewritten=False, advisory=True` → 对正文**零改动** | 原 `capabilities/core/evaluator.py:330-341` |
| 4 | **反思常常根本不跑**：`if facultative and ...` —— 纯讨论问答不派能力时 `facultative=""` → 整块跳过 | 原 `handlers/discuss_flow.py:1307` |
| 5 | **结论没有出口**：advisory 只进 `_trace`，而流式接口把它 pop 掉（生产前端走的正是流式） | `ai/api/router.py:1809` |
| 6 | **诊断路径相反且更危险**：无条件触发、证据手写拼接 `[:2000]`、**唯一真的会整篇重写**的地方 | 原 `handlers/diagnose_flow.py:792-817` |
| 7 | **判得准不准从未被量过**：真实集上判「不通过」占 **24/40 = 60%**，且同输入 `temperature=0` 能给 6/6 与 0/8 两种极端 | `issues.md` #1–#3 |

**一句话**：我们让一个信息更少、还不能推理的调用，去整篇重写一个信息更全、会推理的调用写出的答案。
讨论区零收益，诊断报告有真实风险；修它要动判据、输入、应用规则、评测口径、前端节点，
成本远超收益，且**无法验证判得对不对**（判定从未落库，没有 ground truth）。

**移除范围**（净删代码）：

- `capabilities/core/evaluator.py` + 两个判官测试文件。
- `handlers/discuss_flow.py` 的自评块、`eval_used`（只写不读的死变量）、`early_stopped`（移除后无读者）。
- `handlers/diagnose_flow.py` 的自评块与 `eval_used`。
- **整个 `eval/` 目录**（它存在的唯一目的就是测判官）。
- `capabilities/__init__.py` 的导出与各处 docstring 提及。
- ⚠️ **不要碰** `.github/workflows/ai-eval.yml`——它跑的是 `pytest tests/ai/`，是**另一套**自动化评测。

**保留**：`draft_snapshot` / `on_rewrite`（澄清附录与记忆标记仍会追加正文、仍需替换前端草稿）；
`facultative`（生成侧 prompt 仍要用）；`terminate`（Supervisor 的短路边）。

**教训（本条最值钱）**：把一个软约束写成代码，不等于它就是硬的；反过来，
**一个"检查"如果它的输入比被检查者的输入少，它就不是检查，是猜**。
以后再要加自评，先满足两条：①检查者拿到与生成者**相同**的输入；②它的产出**不能是整篇重写**。

**副作用（需留意）**：不再有任何自动质检，诊断报告初稿直接呈现。考虑到判官 60% 的
「不通过」率且无法验证，这大概率是净收益，但灰度时留意报告质量体感。

## 2026-10-14 工单 Agent 状态：把「全量对话史」换成「结构化状态 + 近期原文」（#23）

**背景（这不是事故，是一个被推翻的默认）**：有人问「讨论区两个人的分歧是什么」。当时
每轮 @U老师 都把该工单**全部评论原文**塞进 prompt —— 长工单 token 浪费；而只有一句话的
`metadata_info.ai_summary` 又太粗（丢关键信息）。中间那层不存在。

**先说清楚一个容易搞错的前提**：AI **没有**「不知道之前发生了什么」。原事故里它的草稿
正确写出了两个人，说明它读到了讨论史 —— 破坏发生在生成之后（见 #1–#3）。所以这个改动
解决的是**成本与 prompt 有界**，不是「记性」。后人不要拿它当「AI 记性差」的证据。

**设计：分层，不是二选一**

    进 prompt = 结构化状态（覆盖较早讨论的九栏）+ 最近 N 条原文（逐字）+ 本轮问题/引用/附件

| 担心 | 为什么不成立 |
|---|---|
| 全量浪费 token | 原文只留最近 N 条（`AGENT_STATE_RAW_WINDOW`），预算封顶 |
| 只读摘要丢信息 | ①关键信息通常就在最近几轮，那部分是**原文**；②更早的压成**结构化字段**而非散文，标识逐字保留 |

**两个关键决策（后人最容易改错的）**

1. **状态允许有损，因为它不是唯一副本。** 原文永远在 `task_comments` → 状态坏了**删行重建**即可。
   所以**不需要** `AiDiagnosisPlatform.AgentState` 那套三态迁移纪律（那边是 session 维度、
   Redis 里唯一副本，坏了真没了）。形状借鉴、实现不共用（见「刻意不共用」）。
2. **留了逃生口：`discussion_read` 能力。** 状态漏了什么，由调度 LLM 主动回去读原文。
   这正是它与「一句话摘要」的本质区别 —— 摘要是终点，状态只是索引。
   **别把状态做成"必须完备"**，那会退化成又一个会丢信息的摘要。

**分工（配额机制正好支持这个切法）**

- **引用一句话再 @U老师** = **确定性预加载**（`format_quoted_comment_block` 取前后各 K 条），
  `usage.py` 明写「代码里确定性的兜底调用不计入配额」—— 它是产品承诺，必须必然发生；
- **其余「状态不够用」** = 交给调度 LLM 调 `discussion_read`，占配额（设了 3）。

**落表为什么单列**：同 `TaskSpecDoc` 的理由 —— AI 侧 `upsert_task` 会**整体重建**
`tasks.metadata_info`，状态放那里会被反复覆盖。（顺带发现：现有 `ai_summary` 就在那个位置，
同一会话重复转单时会被抹掉。**本次没动它**，但记一笔。）

**刷新走 Redis 队列而不是 `asyncio.create_task`**：回复落库后 `LPUSH`，Worker 起 N 个消费者
`BRPOP`（照 `resolution_worker` 的形状）。好处是**幂等** —— 同一工单被重复入队，第二次跑时
水位线已推进、无可折叠 → 自动跳过，正好处理「用户连着追问三次」。

**最大的长期风险：滚动合并会「传话失真」。** 每轮以上一轮状态为输入，错误逐轮累积
（所有滚动摘要的通病）。缓解：①状态条目带出处，可回溯核对；②随时可整条重建。
若实测确有失真，再加「每 K 轮从原文重建一次」。

**灰度**：`ENABLE_AGENT_STATE` 默认 **0**，关时 `comment_lines` 完全不动 ——
**行为与改造前逐字一致**，可随时回滚。

**测试**（22 条，纯逻辑不调 LLM / 不连库）：`contexts/test_agent_state.py`、
`contexts/test_quoted_context.py`、`services/test_agent_state_worker.py`。
其中锁死三条：最近 N 条**不折叠**进状态、状态为空**不注入**状态块（短工单无回归）、
解析失败**不推进**水位线。

## 2026-10-14 Supervisor 加一轮追规：从「一次规划就派发」到「行动→看结果→再规划」（#24）

**问题（不是 bug，是能力缺口）**：Supervisor 一直是「`_plan()` → `_dispatch()` → 汇总」，
**规划只做一次、结果不再回流**。于是 KB 没命中 / 日志没读出根因时，它**没有第二次机会** ——
只能靠一次规划里把要派的都想到。这是"不够智慧"的根，比缺联网更关键。

对比：仓库里早有两份多轮实现（`AiDataAnalysisPlatform/agent.py` 的 ReAct 循环、
`AiDiagnosisPlatform/tool_loop.py`），`ai/core/llm.py` 的 `complete_with_tools`/`stream_with_tools`
也早写好 —— **Supervisor 一处没用**。本次没有换轨到原生工具调用（那是更大的重写，还得处理
DeepSeek 的 reasoning_content 回传协议），而是**把现成的 `_plan`/`_dispatch` 包进循环**，
保留护栏、配额、进度、注入这些已经工作的机制。

**设计：最多 2 轮（`_MAX_ROUNDS`），四个关键点各对应一个已探明的坑**

| 点 | 为什么 |
|---|---|
| **次轮排除已派过的能力** | `results` 以**能力名**为 key，同一能力跑两次后者会**覆盖**前者；上层逐 key 读 `text`，覆盖等于吞掉证据 |
| **每轮重算 `drop_exhausted`** | 否则次轮会排进必然被 `claim` 拦下的步骤，产生 `ok:False` 噪音 |
| **`terminate` 每轮末尾检查** | 历史方案已验证就该硬停，别白跑下一轮昂贵能力 |
| **`is_simple()` / 空 plan = 停** | 复用现成字段，**不改 `SupervisorDecision` 的 schema**（那套健壮 JSON 解析保持原样） |

**次轮喂给规划器的只有「结果」，没有「过程」**：`_executed_block` 只带「派了什么 + 拿到了什么」
（各自截断 400 字、整段 2000 字）。能力内部的多轮（如 LogSubAgent 的 `log_r1/log_r2`）
走进度广播和 span 树，**是给前端过程区看的，一个字都不进 prompt**。

**顺带修掉两个「机制没接线」的问题（同一类，之前也踩过）**

1. **配额提示从没到过模型**：`cap_usage.notes()` 生成的「已达上限，请给出有界结论」
   只写进 `run()` 的 `final_text`，而 `discuss_flow` **从不读 `final_text`**（0 处引用）——
   docstring（`_synthesize`）写着「**必须进正文**」，但它没有正文。现在 `run()` 单独返回
   `note` 键，`discuss_flow` 追加进 `facultative`。
2. **配额拦下被渲染成「分析失败」**：`_run_one` 配额拦下返回 `{ok: False, error: ...}`，
   与真失败同形，上层会走「真失败」分支说「日志分析失败」。现在加独立标记
   `meta.blocked = "quota"`，上层据此只说「已到调度上限，基于现有信息给结论」。

**教训（与 #22 同源）**：`final_text` 是**死输出**——一个精心拼好的汇总契约，却没有消费者。
**产出没有读者，等于没产出**；而它旁边那句「必须进正文」的注释，反而让人以为已经接好了。
找这类问题的办法还是那条：**从消费者往回数引用**。

**测试**：`run()` 此前**零单测**（`test_usage_quota.py` 全在绕开 LLM 直调 `_guard_decision` /
`_dispatch_group` / `_synthesize`），而它是每次 @U老师 的必经之路。新增
`capabilities/core/test_supervisor_run.py`（13 条）：单轮基线（simple 不派、派了会跑、
results 以能力名为 key）、次轮看得到已执行且不能重派、`terminate` 早停、上限就是 2、
`plan` 累计而 `complexity` 取首轮、两轮 span 顺序、`note` 不污染 results、已执行段会截断。
全量 **884 passed / 27 skipped**。

**风险**

- **没有开关**（用户明确要求直接生效）：出问题只能回滚代码，不能改配置降级。
- **配额更快吃满**：多轮 = 更多能力调用 → 更早触顶 `max_usage_per_session`。
  这正是上面第 1、2 条要一起修的原因，否则用户会看到一堆莫名其妙的"失败"。
- **不做「同能力多轮重试」**（如换 query 重读日志）：`results` 的形状表达不了。
  真要做需先改它的契约。

## 2026-10-15 编排能力的五项补强（#25）

起因是问「相比 Claude 这类强 Agent 还缺什么」。结论：**最大的短板不是缺联网**，而是
编排层的能力缺口。按价值排序做了五项，都不新建机制、都复用现成的东西。

| # | 缺什么 | 怎么补 | 关键约束 |
|---|---|---|---|
| **B** | 能力之间**互相喂养**：两波派发，后一波看不到前一波的结果，「日志读出型号 → 再查该型号 KB」这条链是断的 | plan step 加可选 `after: [能力名]`，按依赖分波；声明了依赖的步骤**自动拿到上游结果**接在自己的 query 后 | **没有任何步骤写 `after` 时，沿用改造前的顺序**（检索先、日志后）——同一套机制表达，不是第二条代码路径；依赖落空要摘掉；**成环不卡死**（并作一波照跑 + 告警）；不写 `after` 就不注入（别给 query 掺水） |
| **C** | 追规那轮把「已派过的能力」**全**禁了 —— 连**失败的**也禁，于是「一次 LLM 抖动毁掉整轮」没人兜 | 把「已派过」细化成「**已成功**」：只有 `ok 且有正文` 的才摘掉；失败的可以换个说法再派一次，且成功的结果会覆盖那条失败记录 | **不是**加一层盲目重试循环：多数能力失败是**返回 failure 而非抛异常**，且 LLM 客户端内部已重试 3 次；用追规轮做重试天然有上限（2 轮）、有规划、不多加机制 |
| **E** | 「用户在质疑此前结论」靠一张**硬编码关键词表**，换个说法（「你这结论站不住」）就失效 | 折进**本来就要做的**那次意图分类：prompt 加 `challenge` 字段，新增 `classify_full()`（`classify()` 保持兼容） | **判据取并集（语义 OR 关键词），关键词表故意保留**：分类抖动时语义判断拿不到，而「用户质疑必真重跑」是**产品承诺**，不能因一次分类失败就失守 |
| **D** | 调度靠「让模型吐 JSON 我们再抠」，`_parse_decision` 那套多层容错本身就是为「模型 JSON 不老实」写的 —— 脆弱性自找 | `_plan` **优先原生工具调用**（`submit_plan` 工具，参数由接口解析成 dict），拿不到回退 JSON | **只调一轮**（计划是最终产物，不回传工具结果）→ **绕开** DeepSeek「tools + 思考」的 reasoning_content 多轮回传协议（那才是 D 真正危险的部分）；**回退网就是原路径**，新路径不可能更差 |
| **F** | 多轮上线后**没有成本/延迟护栏** | **安全阀**（不是预算）：`SUPERVISOR_MAX_RUN_SECONDS`（热生效，默认 300s）超时后**不再开始新一轮**，把已拿到的结果返回 | **不打断已经在跑的能力**（打断会丢证据）；只在「已经有结果」时才挡（第一轮就超时是环境异常，交给上层）；默认值**故意宽松**——它只该在病态情况兜住；另加耗时/轮次日志，**将来定预算看分布，别拍脑袋** |

**这五项共同的一条纪律**：**每一项都要保证「不加它也不比原来差」**——
B 的默认顺序不变、C 不影响成功的、E 保留关键词兜底、D 回退原路径、F 只在病态触发。

**测试**：`capabilities/core/test_supervisor_run.py` 扩到 28 条（B/C/D/F 各覆盖），
新增 `test_router.py` 8 条（Router 此前**没有任何测试**）。
其中专门锁「别做过头」的：成功后仍禁重派、工具路径的计划**同样过护栏**、
工具路径也支持 `after`、超时只挡新轮且第一轮照跑。

**没做的（有意）**：联网检索（KB 已覆盖手册/FAQ/错误码/VDA5050/行业标准，真需外网的比例小，
且网页进 prompt 要配套反幻觉）、代码执行（不是通用编程 Agent）。

## 工具链与协作约定（#11–#14，全是本仓真实踩过的）

| # | 坑 | 现象 | 正确做法 |
|---|----|------|----------|
| 11 | PowerShell 5.1 按 **ANSI** 读 `.ps1` | UTF-8 无 BOM + 中文的脚本解析失败（`sync.ps1` 直接语法错） | `.ps1` 必须带 UTF-8 BOM（`d18b052a`）。本仓 `daily-sync-test.ps1`、`scripts/setup_mysql.ps1` 本来就有 BOM；`automation/scripts/*.ps1` 纯 ASCII 无风险 |
| 12 | `2>&1` + `$ErrorActionPreference='Stop'` | git 的 stderr 进度被 PS 包成 `NativeCommandError` → 脚本中止。我曾据此**误判**脚本有第二个 bug，撤回了 | 别在启用 Stop 的脚本里给 git 加 `2>&1` |
| 13 | `git diff --cached --name-only` 对中文名转义加引号 | 校验「暂存集是否只有我的文件」会看错 | 用 `git -c core.quotepath=false` |
| 14 | `Out-File -Encoding utf8`（PS 5.1）会写 BOM | 提交信息的**主题行开头多一个** `` | 用 `[System.IO.File]::WriteAllText($p, $msg, (New-Object System.Text.UTF8Encoding($false)))` |

**提交纪律**：工作区经常有**别人未提交的活**（本项目同时有多人在同一分支作业）。提交时**只显式 `git add` 自己的文件**，绝不 `-A`；提交前后都用第 13 条校验暂存集。

**推送**：`.agents/skills/smart-dispatch-git/scripts/sync.ps1` 的「脏工作区」守卫是为 merge 设计的，别人有未提交改动时会挡住 `-Action push`。**push 不碰工作区**，可直接按技能文档的等价命令 `git push origin ai-feature-dispatch`（禁止 `--force`）。

## 排查方法教训：静态扫描的假阳性（#15）

一次「文档标识符/路径是否还存在」的自动化核查，出现过这些假阳性，**都靠回代码抽样核对才发现**：

- 只拿 `.py` 当语料 → 误报 30 个「文档里的标识符不存在」，实为**前端组件名 / DDL 列名 / 配置键**；
- 路径检查误报文档**故意**引用的「不存在的文件」（那正是它在说的事）；
- `test_*` 函数必然显示为零引用；
- `_CallVisitor.visit_Call` 由 `ast.NodeVisitor` **动态派发**，静态找 `visit_Call` 调用点必然找不到；
- `from __future__ import annotations` 是编译指令，不是未用 import。

**教训**：扫描结果只是**候选集**，下结论前必须回代码核对；说「这个不存在 / 没人用」之前，先问「我的语料是不是漏了一整个语言/目录」。

## 测试基建债（记录，暂不修）

- `ai/requirements.txt` 曾**漏声明 `pytest-asyncio`**（已补）。本仓跑测试前先确认 venv 是 `D:\CodeHub\AI\OpenRobotService\.venv\Scripts\python.exe`。
- 全量首跑出现过 `3 failed, 802 passed`：1 个是真 bug（缓存读取错位）、2 个是过时测试。**判断是不是既有问题要用 `git stash` 做基线**，不要凭印象（本次就是这么定的性）。
- 用户口径：**临时/一次性测试文件留在本地不提交**，只提交代码与长期测试。

---

## 跨平台 / 真实位置在别处的条目（教训通用，别搞错归属）

### 缓存读写契约错位 + 双人反向修复 → 坏合并（#16）

**真实位置**：`ai/agents/AiDiagnosisPlatform/pipeline.py`（`_ALL_PROJECT_NAMES_CACHE` :29/:2407/:2433，`_RECENT_TICKET_PROJECTS_CACHE` :32/:2549/:2586，`_plan_tools` :3294）——**不在 AiTaskPlatform**。

**问题**：写 `_ALL_PROJECT_NAMES_CACHE[:] = [(now + 300, names)]`（单条目列表里装 tuple），读 `now < _ALL_PROJECT_NAMES_CACHE[0]` → `float < tuple` 抛 `TypeError`。比较先于过期判断就抛，**坏条目永不过期**：首次填充后一直崩到进程重启。异常被 `_plan_tools` 的 `except` 吞掉，后果是**意图路由丢失、项目预填失效**（返回兜底的 `"diagnosis"`）。

我曾在提交信息里把它描述成「5 分钟内每次调用都抛」——比实际**轻**了，口径已更正为「首次填充后一直崩到进程重启」。

**坏合并（本条最该记住的部分）**：我改**读取**（`[0][0]`/`[0][1]`），同事改**写入**（`[now+300, names]` 平铺），两人方向相反。git **自动合并成功、无冲突标记**，但产出「平铺写入 + 嵌套读取」的坏混种 → `TypeError: 'float' object is not subscriptable`。

**教训**：合并后的检查点不是「有没有冲突标记」，而是**「不变量还成不成立」**。修同一个 bug 时，先看对方改成什么口径，别各修一半。

### 作者名的第二份实现（#17）

**真实位置**：`ai/agents/AiDiagnosisPlatform/pipeline.py:949` 的 `_lookup_ticket_ref`（客服/诊断入口），与 AiTaskPlatform 的 `contexts/comments.py`（讨论区/`@#` 引用）**是同一件事的两份独立实现**，同一类 `created_by_name` 问题两边都有（`5a908e82` + `ai/tests/test_ticket_ref_comment_authors.py`）。

### 数据库迁移与模型必须成对（#18）

**现象**：`sqlalchemy.exc.OperationalError (1054, "Unknown column 'tasks.archived_by'")`。

**根因**：提交 `9726d417` 改了模型却没写 alembic 迁移（提交信息里就写着 "migration excluded per user request"）。

**修复**：手工加列 + 索引 + 追加枚举值（枚举值**追加在末尾**，零数据重写），脚本幂等、重跑全 SKIP。

**教训**：模型变更与迁移**必须成对**进仓，否则生产第一次查询就炸。

### 基础设施：Qdrant 502（#19）

**现象**：`外部服务不通: qdrant — 列出集合失败: 502 (Bad Gateway)`。

**我先给的判断是错的**（怀疑 `HTTP_PROXY` 劫持 localhost），实测推翻：真因是 **Qdrant 容器 4 天前就退出了**（`Exited(255)`、`RestartPolicy=no`）。

**修复**：`docker start qdrant` + `--restart unless-stopped`，之后 `validate_ai_config()` 三项全绿。

**教训**：502 先查**依赖进程是否活着**，再怀疑代理/网络这类「听起来更聪明」的原因。

## 刻意不共用的决策（记下来，免得后人当 bug 修掉）

- **AiTaskPlatform 与 AiDiagnosisPlatform 的评论作者名映射不抽成共用**：两个 Agent 零交叉导入，各留一份。理由是诊断入口后续可能要对人物信息**脱敏**，`umap` 就是脱敏注入点；此时共用反而互相牵制（用户 2026-10-12 拍板）。
