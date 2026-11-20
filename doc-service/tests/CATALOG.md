# 测试目录（能力 → 用例）

旧门户验收清单的语义对应到新实现。每条都有自动化；标了「API」的走 PostgreSQL + TestClient。

## 加密与封装

| ID | 断言 |
|----|------|
| T-CRYPTO-01 | 同一 KEK+AAD 可往返解密 |
| T-CRYPTO-02 | 错误 KEK 解密失败 |
| T-CRYPTO-03 | AAD 不一致解密失败（防密文挪到别的资源） |
| T-CRYPTO-04 | 密文 blob 不含明文字节 |
| T-PKG-01 | `packaging/compose.yml` 不把 5432 发布到宿主机 |
| T-PKG-02 | 开发 compose 只绑 `127.0.0.1:5432` |
| T-PKG-03 | `.gitignore` 含 `.env` 与 `secrets/` |
| T-PKG-04 | 无 `TOPO_CONTENT_KEY` 时 Settings 拒绝启动 |
| T-NG-01 | topo-ng compose 不与旧站容器名/端口/路径碰撞，且不发布 5432 |
| T-NG-02 | `update.sh` 排除 `.env`/`secrets/`，不含 pg 客户端；发布包含 alembic |
| T-NG-03 | 推代码要求 `TOPO_NG_DEPLOY`，默认 SSH 不是 9022 |
| T-NG-04 | 数据推送脚本固定失败 |
| T-NG-05 | gitignore 含 `dist/` 与 `deploy/install/*.tar.gz` |

## 鉴权与会话

| ID | 断言 |
|----|------|
| T-AUTH-01 | 健康检查 200 |
| T-AUTH-02 | 未登录 /me → 401 |
| T-AUTH-03 | 错密码 → 401 |
| T-AUTH-04 | 登录后 cookie + /me 含 role |
| T-AUTH-05 | 登出后再 /me → 401 |
| T-AUTH-06 | Bearer agent token 可调 pack |
| T-ACC-01 | 授权包 zip 含 auth.json；guest 403 |
| T-ACC-02 | 授权包 base_url 用页面站点（非 API 端口）；TOPO_PUBLIC_URL 优先 |

## Policy / ACL

| ID | 断言 |
|----|------|
| T-POL-01 | 无授权看不到 encrypted 模块（browse=False） |
| T-POL-02 | 有 module write 可 browse encrypted |
| T-POL-03 | 未加密 + 可看产品 → 可 browse |
| T-POL-04 | guest 不可登记工时 |
| T-POL-05 | pm 不可改功能正文 |
| T-POL-06 | 非 admin 不可改 admin_protected |
| T-POL-08 | 管理员保护模块后不可整模块删除（父族只护自身，不连坐子模块）；保护模块时若无受保护版本则护住最旧版；保护版本后该版本不可删；有编辑权可删未保护模块/多余版本（版本删除仅 API） |
| T-POL-07 | 族 write 下钻到成员；权限目录按拓扑成树，不含【产品】族/发版节点 |
| T-POL-09 | 部门主管不可用户权限管理、不可看更新记录 |

## 拓扑 / 模块

| ID | 断言 |
|----|------|
| T-TOPO-01 | graph 不含无权限 encrypted 节点 id |
| T-TOPO-02 | 无权 GET 模块 → 403，body 不含标题 |
| T-TOPO-03 | 有权 GET 模块得到 markdown/html |
| T-TOPO-04 | 库内 documents.body_blob 不含明文 |
| T-TOPO-05 | 嵌套宿主自身未完成时图/页/动效跟宿主自身 band（不按子模块最低进度）；自身已完成才取子模块最不成熟态（页芯片「子模块-…」）；早于当前版的版本页显示已完成；自身未完成时可改生命周期，已完成派生或历史版只读 |
| T-TOPO-14 | v1.0 嵌套宿主自身已完成时图/页取子模块当前展示态中最不成熟者（不按宿主版本把子模块历史版当成已完成） |
| T-TOPO-06 | 节点未写 nested_board 时不拿同名子图文件派生状态；缺 lifecycle 按已完成导入 |
| T-TOPO-07 | 连线可落到包络组；from_side/to_side 随图返回 |
| T-TOPO-08 | 父族包络导入为模块；图上标题可打开详情 |
| T-TOPO-09 | 模块页子功能拓扑：族裁切同板成员（优先于独立子图文件）；有 nested_board 则画子图；【产品】族/发版页不展示 |
| T-TOPO-10 | 画板 PUT 可新增节点并写回标题；新建模块生命周期默认设计中（pre） |
| T-TOPO-11 | 功能生命周期不可回退到更早阶段 |
| T-TOPO-12 | 功能模块页可按 versions 切换；历史版正文独立；早于当前版显示已完成且生命周期只读 |
| T-TOPO-13 | 模块正文 HTML 保留 TeX 分隔符；SVG/div 整块保留（空行不拆成段落） |
| T-TOPO-15 | 正文内静态 `id.html` / `id-v1.0.html` 链到 SPA 模块路由（历史版带 `?v=`）；历史版正文文件名若不是模块 id，则链到本模块上一版本 |
| T-TOPO-16 | 子页版本记录只在工程包导入时写入；页面改正文不产生修订；回滚不新增条目 |
| T-TOPO-17 | 【产品】旁指标模块：graph 带 metrics_id；USP 灌入 agv-eval 子图与正文；ORS 宿主空图；不进工时/权限树 |
| T-TOPO-19 | USP 产品旁 TEST：graph 带 test_sso；能看 USP 的人可 POST /test-sso；未配 license 503；ORS 无此入口 |
| T-TOPO-18 | 画板 PUT 不得把节点上的 version 写回 current_version；悬空 current_version（无对应 ModuleVersion/Document）回落到已有最高版本，不 500、不伪造新版正文 |
| T-TOPO-20 | 模块页 JSON 带 board_rel（节点所在板）；后退回到该板而非默认算法拓扑 |

## 工时

| ID | 断言 |
|----|------|
| T-HRS-01 | 超过 hard cap 拒绝 |
| T-HRS-02 | band=done 且已投入 → 再改工时 403 settled |
| T-HRS-03 | 产品页工时按 changelog（相对上一版新增/升级）汇总；累计只计已结算周；嵌套宿主上卷子模块；已发布非 DEV 仅累计 |
| T-HRS-04 | 已结算工时按版本锁定；未结算的本周工时在当前版本尚无登记时跟到当前版本 |
| T-HRS-05 | 模块工时块累计/预期/本周均为合计；条上为个人本周与个人预期；预期上限 64；预期合计为各条个人预期之和 |
| T-HRS-06 | 导入/同步后若模块仅一名参与者，模块预期工时记为该用户该版本的个人预期 |
| T-HRS-07 | 个人预期为 0 的空行不计入预期合计 |
| T-HRS-08 | 本周工时不得超过该用户当前预期工时 |
| T-HRS-09 | 周日 23:59 后结算：本周加入累计、从预期扣除、本周清零；预期不低于 0 |
| T-HRS-10 | 模块页工时两块：自身已完成无数据不显示、有数据只出累计、未完成出累计/预期/本周+登记；子孙平铺本周有自身登记的模块（不上卷、不嵌套） |
| T-HRS-11 | 拓扑珠子 / 需求分组进度：嵌套宿主（含 kind=group 的组节点）本周工时计入自身，并与子孙本周工时相加；product_hub / metrics 仍不进珠子；模块页自身块仍不上卷 |
| T-HRS-12 | 子模块在同一发版另有涵盖版本时，宿主上卷按子模块自己的版本取工时和预期，不沿用宿主版本 |

## 需求

| ID | 断言 |
|----|------|
| T-REQ-01 | 有评论时非 admin 改正文 → 409 |
| T-REQ-02 | 非法状态流转 → 400 |
| T-REQ-03 | 需求正文入库为密文 |
| T-REQ-04 | 列表含创建者 / 最近修改时间 / 优先级（P0–P4，默认 P2）；详情页创建者可改，非创建者 403 |
| T-REQ-05 | 项目经理新建需求 tag=project；产品经理及其他 tag=product |
| T-REQ-06 | 创建者和管理员可删需求；其他人 403 |
| T-REQ-07 | 草稿发起评审后待评审；全部通过转入待关联；拒绝/待明确需原因并回草稿；再发起保留历史轮次 |
| T-REQ-08 | 待关联被功能关联后转入开发中；不可取消关联；所链功能均已完成则需求已完成；当前工时=本周/剩余 |
| T-REQ-09 | 改正文写修订；相同正文不写；可回滚；详情带 revision_count |
| T-REQ-10 | 正文内嵌 PNG 从 HTML 拆出；include_markdown=false 不回 markdown |
| T-REQ-11 | 部门主管可新建/改自己的需求并上传工程包；不可改他人需求 |

## 发布

| ID | 断言 |
|----|------|
| T-REL-01 | 非授权改 covers → 403 |
| T-REL-02 | 授权可写 covers 并读回 |
| T-REL-03 | 产品页三栏入库为密文，读回明文；页面只读展示，不可直接编辑 |
| T-REL-04 | 无权限改标签 → 403 |
| T-REL-05 | 分配 STABLE/DEV 后图上锚点标题/版本跟随 tag；非锚点版本节点不出现 |
| T-REL-06 | 点 DEV 锚点打开当前 tag 指向的发版（covers 汇总工时）；未分配锚点 tag_unassigned |
| T-REL-07 | changelog 相对上一 semver 的 covers 差；说明列页面只读 |
| T-REL-09 | 未发布版本可删（含已打标签）；已发布 400；非授权 403 |

## 工程包 / Agent

| ID | 断言 |
|----|------|
| T-PACK-01 | Bearer agent token 可 import，模块正文更新为包内 doc.md |
| T-PACK-02 | 无效 agent token import → 401 |
| T-PACK-03 | 非 zip → 400 not_zip |
| T-PACK-04 | 缺 PACK.json → 400 missing_pack |
| T-PACK-05 | PACK 产品与 URL 不符 → 400 product_mismatch |
| T-PACK-06 | PACK 模块与 URL 不符 → 400 module_mismatch |
| T-PACK-07 | 缺 doc.md → 400 missing_doc |
| T-PACK-08 | 导出后页面改正文再 import 旧包 → 409 edit_conflict，正文保持页面版 |
| T-PACK-09 | 工程师无模块 write 时 import → 403，正文不变 |
| T-PACK-10 | 相同正文再 import 不写修订；正文变化的 import 写修订并进更新记录（actor=上传者） |

## 上传 / 多用户 / ACL（`test_upload_acl.py`）

| ID | 断言 |
|----|------|
| T-UPL-01 | export 含 edit_token |
| T-UPL-02 | edit-token?since=当前 → unchanged |
| T-UPL-03 | since 过期 → export_required |
| T-UPL-04 | 无 since 只返回当前 token |
| T-UPL-05 | 匹配 token 的 import 成功 |
| T-UPL-06 | 他人先改 → 409 edit_conflict + hint，正文为他人版 |
| T-UPL-07 | 过期 token 但正文已一致 → 幂等 200 |
| T-UPL-08 | 两人先 export，后 import 者 409 |
| T-UPL-09 | 无 token 的包 → 409，须重新 export |
| T-UPL-10 | 页面改正文后旧包 409 |
| T-UPL-11 | 成功 import 返回新 token |
| T-UPL-12 | 无写权限 edit-token → 403 write_revoked |
| T-UPL-13 | 收回写权限后 export → 403 write_revoked |
| T-UPL-14 | 收回后 import 失败，正文不变 |
| T-UPL-15 | 收回提示含写权限 |
| T-UPL-16 | 收回后 edit-token 403 |
| T-UPL-17 | 收回后加密模块 GET 403 |
| T-UPL-18 | writable-modules 不再列出 |
| T-UPL-19 | 图上隐藏加密节点 |
| T-UPL-20 | 图 elided ≥ 1 |
| T-UPL-21 | 授予后 writable 列出该模块 |
| T-UPL-22 | 授予后 can_edit true |
| T-UPL-23 | 授予后可 export |
| T-UPL-24 | 授予后可 import |
| T-UPL-25 | 授予后加密模块可浏览 |
| T-UPL-26 | 同一 agent token 授予后立即生效 |
| T-UPL-27 | 域授权列出该域模块 |
| T-UPL-28 | can_edit 走实时查询而非授权包快照 |
| T-UPL-29 | 宿主 write 可改新增子模块 |
| T-UPL-30 | writable 含新增子模块 |
| T-UPL-31 | 子模块 pack import 成功 |
| T-UPL-32 | 无宿主 write 不可改子模块 |
| T-UPL-33 | 族 write 下钻到新成员 |
| T-UPL-34 | 嵌套新建写入 parent_id；加密宿主下子模块默认加密 |
| T-UPL-35 | 产品 ALL 可改新子模块 |
| T-UPL-36 | 收回宿主后失去子模块写权 |
| T-UPL-37 | 域授权覆盖嵌套子模块 |
| T-UPL-38 | 级联可浏览加密子模块 |
| T-UPL-39 | 仅模块 write 时根图 can_edit false（无【展开】） |
| T-UPL-40 | 产品 ALL 根图 can_edit true |
| T-UPL-41 | 管理员根图可编辑 |
| T-UPL-42 | 主管根图可编辑 |
| T-UPL-43 | 宿主 write 时子图 can_edit true |
| T-UPL-44 | 无关模块子图 can_edit false |
| T-UPL-45 | 访客根图不可编辑 |
| T-UPL-46 | 产品经理不可改拓扑 |
| T-UPL-47 | 宿主 write 可保存子拓扑 |
| T-UPL-48 | 仅模块 write 不可保存根拓扑 |
| T-UPL-49 | 域授权可改对应域板、不可改根图 |
| T-UPL-50 | 授权包列出 edit-token / writable / module / draft_export / req_export 接口；AGENTS.md 含行文强制约束与末行校验码；校验码落后 409 且 hint 要求全文重读尤其行文 |
| T-UPL-51…69 | 未登录 401；坏 token；换发 agent token；会话内收回权限立即生效；guest/pm/pjm 即使有 grant 也不可 export；tester 可写；降为 guest / 去掉产品后 403；主管无产品 403；admin 免 grant；未知模块 404；坏产品 400 |
| T-UPL-70…100 | base_edit_token；根目录 doc.md；空正文/Unicode；收回优先于冲突；冲突不写修订；改写 import 写修订；三人并发；页面+agent 同账号冲突；非法 zip；read 授权不等于写；usp ALL 打不开 ORS；`*` 即产品 ALL；模块页 edit_token 含规则版本；无规则版本的 since 必须重新 export；规则版本落后即使正文不变也 409 |
| T-UPL-101…150 | 孙模块级联与收回；只授子模块不能改父板；空子图 can_edit；加密继承；族不进 writable、不解锁根【展开】；algo 域可改根图；backend 不能改 frontend；ALL 可改各域板；guest 嵌套图隐藏加密节点；授权包/agent 随授予收回即时变化；页面 PUT/生命周期在收回后 403 |

## 管理

| ID | 断言 |
|----|------|
| T-ADM-01 | 管理员可见更新记录：标题为业务对象（平台·域 / 模块名），不出现数据文件名；含工程包导入、状态/进度、拓扑编辑；非管理员 403 |
| T-ADM-02 | 更新记录默认保留 7 天，超期滚动删除；列表不再出现过期条目 |

## 讨论区

| ID | 断言 |
|----|------|
| T-DRAFT-01 | 登录可建草稿；草稿列表仅本人；他人 GET/PATCH/DELETE 404 |
| T-DRAFT-02 | 改正文可回读；正文入库密文 |
| T-DRAFT-03 | 分页 page/size；空标题 400；未登录 401 |
| T-DRAFT-04 | 发布进公共；默认全员可见；点名可见时仅名单内可读；能打开即可改正文，删除/发布仍仅创建者 |
| T-DRAFT-05 | 能打开的帖可留言；空内容 400；不可见帖 404 |
| T-DRAFT-07 | 讨论区工程包含 AGENTS.md 与校验码；校验码不对 409；Agent PATCH 正文 409，须 pack import |
| T-REQ-08 | 需求工程包含 AGENTS.md 与校验码；校验码不对 409；Agent PATCH 正文 409，须 pack import |
