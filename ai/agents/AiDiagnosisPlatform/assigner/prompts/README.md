# 派单 Prompt 清单

派单里所有给大模型看的正文，只在本目录维护。  
业务代码（`pipeline/` / `filtering/` / `recall/` / `ranking/`）只负责填工单、名单、画像，不写长文案。

审视时从本文件出发，打开对应 `build_*` 即可看到完整拼装。

| 编号 | 名称 | 何时发出 | 拼装 | 调用方 | 温度 / tokens |
|---|---|---|---|---|---|
| Step0-A | 弱信号指定人 | 无 `[指定处理人]`，预判像指定了人才发 | `step0.build_weak` | `dispatch_flow.py` | 0.1 / 120 |
| Step0-B | 同名抉择 | 指定人同名且画像完整度打平 | `step0.build_collision` | `dispatch_flow.py` | 0.2 / 200 |
| Step1-R2 | 部门主判 | 库里有部门画像 | `step1.build_r2` | `llm_dept_signal.py` | 0 / 480 |
| Step1-审查 | 部门复核 | R2+历史融合成建议部门后 | `step1.build_audit` | `dept_audit_signal.py` | 0 / 200 |
| Step1 共用 | 类型尺子 / 产品归属 / 护栏 / 工单字段 | 被 R2、审查引用 | `shared.py` | 上两行 | — |
| Step3-L1 | 纯 LLM 召回 | Step2 未命中模糊截断 | `step3.build_l1` | `llm_recall.py` | 0 / 1200 |
| Step6 | 最终仲裁 | 精排之后 | `step6` 静态段 + `llm_decision._build_prompt` 填排名 | `llm_decision.py` | 0.3 / 400 |

【工单】只由 `shared.ticket_fields_block` 拼，Step0 / Step1 / L1 / Step6 共用。  
L1 看人只由 `shared.engineer_brief_lines` 拼：姓名+ID、职级、部门、公司、责任模块、职责。  
看人反幻觉：`shared.person_anti_hallucination`（L1 / Step6 共用：可推断谁能接，但不能编造其职责）。  
Step6 重派身份只看排名行上的 Step2 标签；有倾向人时补一句「正常不拒绝用户选择」；备注有才带。  
名单是三路并集；总分取命中各路归一分的最高值；每人带「来源」。  
产品附录：摇人吧服务号 / 调度USP / 车端软件 / 车端硬件 各一份；对不上也出「未识别产品」。

没有 prompt 的步骤：Step2 打标、Step4 精排、Step5 负载（关）、Step7 规则兜底。

不在本目录（旁路，不随派单主流程改）：

- `backend/app/services/redispatch_tip_service.py` 里可选的 tip 润色（默认关）

改口径：只改本目录。改完跑对应 `assigner/tests/test_stepN.py`。
