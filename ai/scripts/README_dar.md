# 直答率周流程工具链（dar）

每周从服务器导对话数据 → 本地重放/LLM 批判 → 标注 → 周报的完整流水线。
单入口 `dar_weekly.py`，七步按需组合，缺省跑本地全流程（除 export）。

**推荐入口：直答率工作台（dar_studio）**，图形界面双页签——

```bash
python ai/scripts/dar_studio.py     # → http://127.0.0.1:9527
```

- **周流程 · 指标生成**：固定连生产库（数据从生产导）。点选步骤、实时日志流、
  产物预览（周报/明细）、**人工标注入口**（打开标注工具新标签页）。
- **在线测试**：固定打测试环境真实服务（代码/知识库先上测试验证）。
  登录（测试环境账号）后发问题走线上全链路（意图→检索→回答→提单），
  流式展示阶段/回答/结果详情；转工单可「生成草稿→确认提单」（落测试库）。
  **检索探针**：单问题看知识库命中，源可选测试/生产（只读）/本地。

## 环境要求

- 本机 conda `ai` 环境（Python 3.14，`sentence-transformers` 等已装）
- `HF_HUB_OFFLINE=1`（ai/.env 已配；embedding 离线，否则检索重放挂）
- 本地 qdrant + redis 起着（本地源检索重放用）
- ssh 免密到 `usp-a@125.122.97.107:8802`（export / 远程知识库指针用；凭据只在服务器端解析）

## 用法（命令行）

```bash
# 全流程（test 环境数据，缺省）
python ai/scripts/dar_weekly.py export prepare l1 retrieval l3 tool report

# 只重出周报
python ai/scripts/dar_weekly.py report

# 连生产环境导数据（目录自动隔离到 export_dar/prod/）
python ai/scripts/dar_weekly.py --env prod export

# 附注随周报落盘
python ai/scripts/dar_weekly.py --env test --note "本周上线了检索域保底" report

# 检索探针（单问题看命中；test=测试服务在用的知识库，prod=生产）
python ai/scripts/dar_probe.py --q "AGV 怎么上线部署" --qdrant prod
```

`--env test|prod` 影响所有子步骤（数据目录 `Desktop/export_dar/{env}/`、
人工标注文件 `Downloads/manual_segmentation[_prod].json`、周报输出位置）。
子脚本单独跑时用环境变量 `DAR_ENV=prod python ai/scripts/dar_l3.py --all`。

**检索源**：`DAR_QDRANT`（缺省随数据环境：prod 数据→生产 qdrant，test→本地）。
生产/测试远程知识库走 `dar_qdrant.py`：ssh 隧道（本地 16333→服务器 6333，
测试生产同一 qdrant 实例、不同指针文件）+ 三域指针临时切换（company/industry/team，
dispatch 不碰）+ 退出自动恢复。测试与生产 qdrant 指针当前一致，测试先更新后会分叉——
这正是探针 test/prod 两个选项的意义。

## 七步

| 步骤 | 做什么 | 产物（processed/ 下） |
|---|---|---|
| export | ssh 读服务器 .env 连接串导四表 csv.gz；记版本锚点 meta.json | ../{users,conversations,messages,tasks}.csv.gz |
| prepare | 切分会话为回合（dar_prepare） | conversations_split.jsonl |
| l1 | LLM 逐会话切话题+判咨询+**话题类型**（dar_l1） | conversations_classified.jsonl、direct_answer_review_*.csv、direct_answer_summary_*.json |
| retrieval | 每段重放真实检索，LLM 判 KB 能否支撑直答；**chunks 落盘** | retrieval_check_*.json |
| l3 | 三件套 judge（时间线+检索+成单信号+忠实性），--all 全段预标 | l3_judge_*.json（校准）/ l3_judge_all_*.json（预标） |
| tool | 生成标注工具单 html（预标行+**检索命中折叠块**） | segmentation_tool.html |
| report | 聚合周报 json + **Markdown** | weekly_YYYYMMDD.json / .md |

增量：retrieval/l3 同日重跑只补新段；l1 有 `--replay` 读已落盘判定。
人工标注：浏览器开 segmentation_tool.html，审完导出 manual_segmentation*.json
放 `Downloads/`，重跑 `l1 --review` + `report` 让人工标签进周报。

## 周报解读

- **三口径**：L1=1−转工单率（上界，机器信号）；L2=人工六类标签（端到端）；
  L3=AI judge（需校准）。三者同看，单看任何一个都会误判。
- **同分母对比**：基准=人工已标真实组段，三口径同场——消除各自剔除规则的失真，
  这是唯一可直接比较三口径的表。
- **下钻矩阵**：用户×话题类型的 L1 段级直答率，定位「谁的问题没被直答」。
- **失败清单**：L3 预标未直答/未覆盖段按类型分组=知识缺口清单（type 即聚类维度），
  按它补知识库。
- **KB 缺口率**：真实组检索判定 no 占比——知识库没有答案的比例（覆盖层问题）。
  与 L3「未直答」交叉可区分「没检索到」vs「检索到没答好」。
- **L3 precision**：预标四类各自 precision（人工为基准）；总体 ≥90% 可放权
  （预标直接采信，人工只抽检）。
- **滚动校准**：本周 vs 上周校准集三分类对齐率走向。
- **版本锚点**：export 时的 git HEAD + 当周 test 分支合入清单，周报可归因到部署。

## 已知限制

- 检索重放=本地 qdrant 当前状态，≠线上当时检索（线上未留档 hits）；
  verdict 只作参考，重大结论以人工标注为准。
- L3 judge 用 DAR_MODEL（0909 起缺省 deepseek-v4.1-flash-expires-on-0910——
  pro 在中转上频繁超时/不可用；模型 0910 过期后换回或用环境变量改）。
  L1 切分/检索 no 判定同用此模型。flash 判定偏摇摆/偏宽是已知倾向，
  precision 指标就是监控它的。
  注意：已落盘判定会增量复用，切模型后旧结果不自动重判（删对应产物才会）。
- 标注进度存 localStorage，换浏览器/清缓存会丢——审完及时导出。

## 单测

```bash
python -m pytest ai/tests/test_dar_chunks.py -q
```
