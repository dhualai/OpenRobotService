# 四维度回归测试（golden）

治「每次跑测试想一出是一出」：固定基准用例集 + 统一 runner。
用例唯一真源在 [cases/](cases/) 下的 4 个 yaml，人可读可改，新场景顺手沉淀。

## 什么时候跑哪个维度

| 改了什么 | 必跑 | 耗时 |
|---|---|---|
| 检索逻辑（retrieval.py / 精排 / 错误码通道） | `retrieval` | 秒级~半分钟，无 LLM |
| 知识库文档改动 + 入库后 | `retrieval` | 同上 |
| prompt / 组织回答逻辑 | `answer` | 每条一次真实 LLM 调用 |
| 提单链路（状态机 / 闸门 / 草稿） | `ticket`（行为层 + pytest 层） | pytest 层 ~1 分钟 |
| 追问话术 / 项目引导 / 拦截铁律 | `flow` | 每轮一次真实 LLM |
| 大改动（核心链路） | 全跑 `python ai/tests/golden/run_regression.py` | 全量 |

## 跑法

```bash
python -X utf8 ai/tests/golden/run_regression.py                    # 全跑
python -X utf8 ai/tests/golden/run_regression.py --suite retrieval   # 只跑检索
python -X utf8 ai/tests/golden/run_regression.py --suite answer --judge   # answer 加 LLM 评分
python -X utf8 ai/tests/golden/run_regression.py --add retrieval --query "错误码10701是什么" --expect "10701"   # 顺手沉淀
```

- Python：本机 conda ai 环境（`C:/Users/PAJ26020/.conda/envs/ai/python.exe`）
- 前置：`ai/.env`（HF_HUB_OFFLINE=1 等，runner 自动加载）；qdrant 嵌入式独占，跑时别并发入库
- `ticket` 维度会自动附带跑 pytest 状态机层（`tests/test_ticket_submit.py tests/test_can_submit.py`，mock 全栈）
- 输出：终端 PASS/WARN/FAIL 表 + 明细 JSON 落 `OpenRobotService_Data/regression_<时间戳>.json`（含回答全文、命中排名，失败可复盘）

## 断言分级

- **strict: true** → 断言失败 = FAIL（阻塞）：用在机械稳定的断言上（检索命中、铁律话术、闲聊短回答）
- **默认（false）** → 失败只 WARN（LLM 行为有波动，软失败提示人工看明细）
- retrieval 维度全部硬断言（无 LLM，纯机械）
- `--judge`：flash 按检查单给 answer 打分，只 WARN 不 FAIL

## 加用例

直接按格式往对应 yaml 追加，或用 `--add` 快速登记（生成 `临时-xxxx` 名，记得改成正式名和完整断言字段）。

### retrieval.yaml（无 LLM，走 `_retrieve_with_context` 全链路）

```yaml
- name: 用例名
  query: 用户问句
  expect_hit: ["期望命中的标题关键词"]   # 任一命中即过；标题来自送 prompt 的资料行
  rank_within: 1                        # 可选：该资料编号须 ≤ 此值
```

### answer.yaml（真实 LLM 单轮，走 `_agent_think_stream`）

```yaml
- name: 用例名
  query: 用户问句
  must_reference: ["回答必须包含的关键词"]  # 任一
  forbid: ["回答不得包含"]                  # 任一
  max_chars: 1200                          # 回答长度上限
  images_in_allowlist: true                # KB 图片全在本轮注入白名单（拦幻觉链接）
  warn_analysis: fault                     # fault=应有分析段开头 / support=不应有伪分析段
  strict: false
```

### ticket.yaml（真实 LLM 多轮，行为层）

```yaml
- name: 用例名
  turns: ["第一轮", "第二轮"]
  expect_review_any_round: true   # 任一轮出现 review 弹窗事件
  expect_ask_any: ["什么问题", "型号"]   # 话术应含追问要素（任一）
  forbid_any: ["项目是必填"]      # 话术不得包含
  strict: false
```

### flow.yaml（真实 LLM 多轮，项目引导/铁律）

字段同 ticket.yaml；runner 对所有 flow/ticket 用例兜底检查项目拦截话术黑名单
（必须选择项目/项目是必填/先选择项目/项目不能为空/必须先选项目）——出现即 FAIL 级问题。

## 维护原则

- 新 bug 修完 → 复现问句沉淀进对应维度（治「跑完就丢」）
- 用例失败先判断是用例过时还是真回归：检索类看 JSON 里的 docs 明细，LLM 类看回答全文
- 场景要贴近真实用户问法（口语、带现象描述），不要照抄文档标题当 query
