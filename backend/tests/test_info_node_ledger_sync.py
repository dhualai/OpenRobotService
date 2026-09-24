"""企业微信台账同步（info_node_ledger_sync_service）纯函数测试 —— 不连库、不调外部服务。

覆盖：台账取值口径（_value_text）、project 行 → 台账列还原（含 adapter 兜底值的跳过）、
列 → 条目（空值/定位列过滤）、按值指位（值 = 某个下拉的可选项，列名对不上也认；多个候选
与单字值都不认）、分组指位（同名分组 / 台账列名是分组名去限定词）、
未匹配条目的归属建议与备注（同名分组 / 同名但装不下 / 包含关系相近 / 相近的是分组 /
都给不出），以及 build_sync_preview 的三组分桶与元信息（打桩本地上下文）。
2026-09-22 起还覆盖「一键导入全部项目」（import_all_projects）：写入口径（只落将填写 +
将覆盖）、跳过/失败的分桶与「单个项目失败不中断整批」。
"""
import json
from types import SimpleNamespace

from app.modules.admin.services import info_node_ledger_sync_service as sync_service
from app.modules.admin.services import info_node_service as node_service_module
from app.modules.admin.services.info_node_service import info_node_service as node_service


def _tree():
    """贴近真实模板的一小棵树：分组 / 空字段 / 已有内容字段 / 下拉多选项 / 选项装不下的下拉。"""
    return [
        {
            "id": "r1", "title": "基础信息", "content_type": "text", "value": None, "sort_order": 0,
            "children": [
                {"id": "c1", "title": "客户信息", "content_type": "text", "value": "中力", "sort_order": 0, "children": []},
                {
                    "id": "c2", "title": "项目区域/地点", "content_type": "text", "value": None, "sort_order": 1,
                    "children": [
                        {
                            "id": "c21", "title": "区域选项", "content_type": "select", "sort_order": 0,
                            "value": json.dumps(
                                {"selected": "", "options": ["大陆(China Mainland)", "亚洲其他"]},
                                ensure_ascii=False,
                            ),
                            "children": [],
                        },
                    ],
                },
                {
                    "id": "c3", "title": "项目类型", "content_type": "select", "sort_order": 2,
                    "value": json.dumps({"selected": "", "options": ["试点项目", "推广项目"]}, ensure_ascii=False),
                    "children": [],
                },
            ],
        },
        {
            "id": "r2", "title": "人员信息", "content_type": "text", "value": None, "sort_order": 1,
            "children": [
                {"id": "p1", "title": "销售", "content_type": "text", "value": None, "sort_order": 0, "children": []},
                {"id": "p2", "title": "实施", "content_type": "text", "value": "老王", "sort_order": 1, "children": []},
            ],
        },
    ]


def _flat():
    return sync_service.import_service.flatten_tree(_tree())


def _row(**overrides):
    """project 行（只带用例关心的列；_ledger_values 是 getattr 读的，用 SimpleNamespace 就够）。"""
    fields = {
        "name": "江苏南京本川XSC仓储项目", "code": "69",
        "status": "即将进场", "recent_delivery_date": "2026-09-19 11:20",
        "project_region": "大陆（China Mainland）", "sales": "张三",
        "field_engineer": "赵六", "project_type": "普通项目",
        "total_vehicle_count": 6, "undertake_status": "是",
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _values(**overrides):
    """_ledger_values 的输出形态（台账列名 → 值），含定位列与空列。"""
    values = {
        "项目编号": "69",
        "项目名称": "江苏南京本川XSC仓储项目",
        "更新时间": "2026-09-19 11:20",
        "销售": "张三",
        "实施工程师": "赵六",
        "项目区域": "大陆（China Mainland）",
        "项目类型": "普通项目",
        "总车数": 6,
        "是否承接": True,
        "附件示例": [{"url": "https://x/a.png"}],
        "空字段示例": "",
    }
    values.update(overrides)
    return values


# —— 取值口径 ——

def test_value_text_matches_wecom_adapter():
    assert sync_service._value_text(None) == ""
    assert sync_service._value_text("  张三  ") == "张三"
    assert sync_service._value_text(True) == "是"
    assert sync_service._value_text(False) == "否"
    assert sync_service._value_text(6) == "6"
    # 选项/成员类字段是 list，取首个非空字符串
    assert sync_service._value_text(["张三", "李四"]) == "张三"
    assert sync_service._value_text(["", "李四"]) == "李四"
    # 图片/附件/位置这类结构化值没有可写进节点的文本
    assert sync_service._value_text([{"url": "https://x/a.png"}]) == ""
    assert sync_service._value_text({"text": "会议室A"}) == ""
    assert sync_service._value_text([]) == ""


# —— project 行 → 台账列 ——

def test_ledger_values_restores_ledger_column_names():
    values = sync_service._ledger_values(_row())
    # 字段名换回台账列名——比对靠的是这个名字，错一个字就整列匹配不上
    assert values["项目名称"] == "江苏南京本川XSC仓储项目"
    assert values["项目生命周期"] == "即将进场"
    assert values["更新时间"] == "2026-09-19 11:20"
    assert values["项目区域"] == "大陆（China Mainland）"
    assert values["是否承接"] == "是"
    assert values["总车数"] == 6
    # 行里没值的列不出现（None 与空串等价：都没什么可同步的）
    assert "承接描述" not in values and "部署版本" not in values
    # 加工过的字段不还原成台账列：「项目类型」只认原文那一列
    assert "category_basis" not in values and values["项目类型"] == "普通项目"


def test_ledger_values_skips_adapter_empty_fallbacks():
    # adapter 对空的「项目生命周期」列兜底成「待开始」——不是台账原文，别同步进节点
    assert "项目生命周期" not in sync_service._ledger_values(_row(status="待开始"))
    # 其余状态值都是台账原样落库的（STATUS_MAP 是同义词表），照旧带上
    assert sync_service._ledger_values(_row(status="正在实施"))["项目生命周期"] == "正在实施"


def test_ledger_items_skips_empty_and_locator_columns():
    items = sync_service._ledger_items(_values())
    titles = [item["title"] for item in items]
    # 定位列只剩「项目名称」（= project.name，不是信息节点）、空字段没什么可同步
    assert titles == ["项目编号", "更新时间", "销售", "实施工程师", "项目区域", "项目类型", "总车数", "是否承接"]
    assert "项目名称" not in titles
    assert "空字段示例" not in titles and "附件示例" not in titles

    first = items[0]
    assert first == {
        "title": "项目编号", "value": "69",
        "node_title": None, "quantity": None, "suggested_parent_path": None,
    }
    # 布尔列按中文落进节点
    assert next(item for item in items if item["title"] == "是否承接")["value"] == "是"


# —— 分组指位 ——

def test_find_value_group_prefers_exact_name_then_containment():
    flat = _flat()
    # 同名分组
    same = sync_service._find_value_group(flat, "项目区域/地点")
    assert same is not None and same["id"] == "c2"
    # 台账列名是分组名去掉了限定词（项目区域 ⊂ 项目区域/地点）
    near = sync_service._find_value_group(flat, "项目区域")
    assert near is not None and near["id"] == "c2"
    # 同名但能填值的节点不是分组：它自己就是归属，不用另找
    assert sync_service._find_value_group(flat, "销售") is None
    # 反向不认：分组名比列名短，那更像另一件事
    assert sync_service._find_value_group(flat, "项目区域/地点/省份") is None


def test_pin_group_values_points_at_dropdown_child():
    flat = _flat()
    items = sync_service._ledger_items(_values())
    sync_service._pin_group_values(flat, items)

    # 「项目区域」是分组名去限定词 → 值指到分组下唯一装得下它的下拉「区域选项」
    # （台账值是全角括号，选项是半角：规范化后相等）
    pinned = next(item for item in items if item["title"] == "项目区域")
    assert pinned["node_title"] == "区域选项"
    # 选项装不下的（项目类型没有「普通项目」）不动，交给 match_items 与备注
    assert next(item for item in items if item["title"] == "项目类型")["node_title"] is None
    # 非分组的同名列不看：普通字段自己就是能填值的节点
    assert next(item for item in items if item["title"] == "销售")["node_title"] is None


def test_dropdown_child_taking_only_accepts_a_single_candidate():
    flat = _flat()
    group = next(node for node in flat if node["title"] == "项目区域/地点")
    assert sync_service._dropdown_child_taking(flat, group, "大陆(China Mainland)")["id"] == "c21"
    # 值不在可选项里 → 不给（宁可不猜）
    assert sync_service._dropdown_child_taking(flat, group, "火星") is None

    # 两个下拉都装得下这个值 → 无从判断是给谁的，也不给
    flat2 = flat + [{
        "id": "c22", "parent_id": group["id"], "title": "部署区域", "content_type": "select",
        "value": None, "options": ["大陆(China Mainland)", "亚洲其他"], "depth": 3,
        "path": "基础信息 / 项目区域/地点 / 部署区域", "path_titles": [], "has_children": False,
    }]
    assert sync_service._dropdown_child_taking(flat2, group, "大陆(China Mainland)") is None


# —— 按值指位（列名对不上、值对得上） ——

def test_option_taker_requires_a_single_exact_hit():
    flat = _flat()
    # 值正好是「项目类型」的一项
    assert sync_service._option_taker(flat, "试点项目")["id"] == "c3"
    # 精确比：只像不算（「试点项目一期」与选项「试点项目」相似但不等），不做车型放宽
    assert sync_service._option_taker(flat, "试点项目一期") is None
    # 选项里没有这个值
    assert sync_service._option_taker(flat, "火星项目") is None
    # 单字值不当归属依据（满树的下拉都可能是「是/否」），哪怕眼下只有一个装得下
    flat_yn = flat + [{
        "id": "p3", "parent_id": "r2", "title": "是否已在用", "content_type": "select",
        "value": None, "options": ["是", "否"], "depth": 2,
        "path": "人员信息 / 是否已在用", "path_titles": [], "has_children": False,
    }]
    assert sync_service._option_taker(flat_yn, "是") is None

    # 两个下拉装着同一项 → 说明不了值是给谁的，不认
    flat2 = flat + [{
        "id": "c31", "parent_id": "r1", "title": "项目类别", "content_type": "select",
        "value": None, "options": ["试点项目", "推广项目"], "depth": 2,
        "path": "基础信息 / 项目类别", "path_titles": [], "has_children": False,
    }]
    assert sync_service._option_taker(flat2, "试点项目") is None


def test_pin_option_values_matches_by_value_not_name():
    flat = _flat()
    items = sync_service._ledger_items(_values(**{"项目生命周期": "试点项目", "承接描述": "火星"}))
    sync_service._pin_option_values(flat, items)

    # 台账「项目生命周期」在树里没有同名节点，但值「试点项目」是「项目类型」的一项 → 认到它上
    pinned = next(item for item in items if item["title"] == "项目生命周期")
    assert pinned["node_title"] == "项目类型"
    # 认不出的不动，交给 match_items 与备注
    assert next(item for item in items if item["title"] == "承接描述")["node_title"] is None
    # 已经有指位的条目（分组指位先行）不重算
    pinned["node_title"] = "区域选项"
    sync_service._pin_option_values(flat, items)
    assert pinned["node_title"] == "区域选项"


# —— 未匹配条目的归属建议与备注 ——

def test_enrich_unmatched_suggests_group_parent():
    flat = _flat()
    row = {"title": "项目区域/地点", "value": "火星", "suggested_parent_id": None, "suggested_parent_path": None}
    sync_service._enrich_unmatched(flat, row)
    assert row["suggested_parent_id"] == "c2"
    assert row["suggested_parent_path"] == "基础信息 / 项目区域/地点"
    assert "同名分组节点" in row["note"]


def test_enrich_unmatched_explains_select_option_gap():
    flat = _flat()
    row = {"title": "项目类型", "value": "普通项目", "suggested_parent_id": None, "suggested_parent_path": None}
    sync_service._enrich_unmatched(flat, row)
    # 不给归属：前端落到「导入信息」兜底，并由备注说清是选项的问题
    assert row["suggested_parent_id"] is None
    assert row["suggested_parent_path"] is None
    assert "下拉" in row["note"] and "补上选项" in row["note"]


def test_enrich_unmatched_suggests_vicinity_by_containment():
    flat = _flat()
    # 「实施工程师」树里只有「实施」：包含关系相近 → 挂到「实施」所在的层级下（人员信息）
    row = {"title": "实施工程师", "value": "赵六", "suggested_parent_id": None, "suggested_parent_path": None}
    sync_service._enrich_unmatched(flat, row)
    assert row["suggested_parent_id"] == "r2"
    assert row["suggested_parent_path"] == "人员信息"
    assert row.get("note") is None

    # 根节点下的相近节点 → 建议挂在根节点自己下面
    row2 = {"title": "基础信息补充", "value": "x", "suggested_parent_id": None, "suggested_parent_path": None}
    sync_service._enrich_unmatched(flat, row2)
    assert row2["suggested_parent_id"] == "r1"

    # 相近的本身就是分组（项目区域 vs 项目区域/地点）→ 建议落在分组里面，并说明原因
    row3 = {"title": "项目区域", "value": "火星", "suggested_parent_id": None, "suggested_parent_path": None}
    sync_service._enrich_unmatched(flat, row3)
    assert row3["suggested_parent_id"] == "c2"
    assert row3["suggested_parent_path"] == "基础信息 / 项目区域/地点"
    assert "分组节点" in row3["note"]

    # 包含关系也没有（「是否承接」不该被「是否对接」之类的相似度吸走）→ 不给建议
    row4 = {"title": "是否承接", "value": "是", "suggested_parent_id": None, "suggested_parent_path": None}
    sync_service._enrich_unmatched(flat, row4)
    assert row4["suggested_parent_id"] is None
    assert row4["suggested_parent_path"] is None


# —— 整条预览 ——

def _stub(project, values, flat):
    """把本地上下文换成打桩（不打补丁到 match_items：分桶要真实走一遍）。"""
    sync_service._load_local_context = lambda project_id: (project, values, flat)


def _restore(original):
    sync_service._load_local_context = original


def test_build_sync_preview_buckets_and_meta():
    original = sync_service._load_local_context
    project = {"id": "69", "code": "69", "name": "江苏南京本川XSC仓储项目"}
    try:
        _stub(project, _values(), _flat())
        result = sync_service.build_sync_preview("69")
    finally:
        _restore(original)

    # 元信息：台账更新时间（镜像列）+ 参与比对的字段数 + 镜像的列总数
    assert result["project_code"] == "69"
    assert result["ledger_updated_at"] == "2026-09-19 11:20"
    assert result["field_count"] == 8     # 项目编号已按普通列参与比对（不再是定位列）
    assert result["mirror_field_total"] == len(sync_service.PROJECT_LEDGER_FIELDS)

    # 将填写：空节点（「销售」），「项目区域」经分组指位落到「区域选项」
    filled = {row["node_id"]: row["value"] for row in result["fill"]}
    assert filled["p1"] == "张三"
    assert filled["c21"] == "大陆(China Mainland)"
    assert result["overwrite"] == []          # 本次台账值与现有内容不冲突

    # 未匹配：树里没有的列，带上归属建议或说明
    unmatched = {row["title"]: row for row in result["unmatched"]}
    assert "实施工程师" in unmatched and unmatched["实施工程师"]["suggested_parent_id"] == "r2"
    assert "项目类型" in unmatched and "补上选项" in unmatched["项目类型"]["note"]
    assert "总车数" in unmatched and "是否承接" in unmatched


def test_build_sync_preview_reports_conflicts_as_overwrite():
    original = sync_service._load_local_context
    project = {"id": "69", "code": "69", "name": "江苏南京本川XSC仓储项目"}
    # 把树里「销售」「实施」填上内容，台账值不同 → 矛盾（将覆盖）而不是将填写
    flat = _flat()
    for node in flat:
        if node["id"] == "p1":
            node["value"] = "旧销售"
        if node["id"] == "p2":
            node["value"] = "老王"
    try:
        _stub(project, _values(实施="老王"), flat)
        result = sync_service.build_sync_preview("69")
    finally:
        _restore(original)

    overwritten = {row["node_id"]: (row["current"], row["value"]) for row in result["overwrite"]}
    assert overwritten["p1"] == ("旧销售", "张三")     # 矛盾：原内容 → 台账值，等用户点头
    assert all(row["node_id"] != "p2" for row in result["overwrite"])   # 与台账一致 → 不产生变更


def test_build_sync_preview_matches_by_option_value():
    original = sync_service._load_local_context
    project = {"id": "69", "code": "69", "name": "江苏南京本川XSC仓储项目"}
    try:
        # 「项目生命周期」「承接描述」在树里都没有同名节点（老同步会把它们堆进「导入信息」根节点）；
        # 值对上「项目类型」的可选项时认到那个节点上，对不上的仍留在未匹配
        _stub(project, _values(**{"项目生命周期": "试点项目"}), _flat())
        result = sync_service.build_sync_preview("69")
    finally:
        _restore(original)

    filled = {row["node_id"]: row["value"] for row in result["fill"]}
    assert filled["c3"] == "试点项目"
    assert result["overwrite"] == []
    unmatched_titles = {row["title"] for row in result["unmatched"]}
    assert "项目生命周期" not in unmatched_titles
    # 同名但装不下（下拉里没有「普通项目」）的仍按未匹配 + 补选项的说明处理
    assert "项目类型" in unmatched_titles


def test_build_sync_preview_fills_project_code_node():
    """台账「项目编号」不再是定位列：树里有同名节点（模板默认有）就直接填进去。"""
    original = sync_service._load_local_context
    project = {"id": "69", "code": "69", "name": "江苏南京本川XSC仓储项目"}
    flat = _flat() + [{
        "id": "c9", "parent_id": "r1", "title": "项目编号", "content_type": "text",
        "value": None, "options": [], "depth": 2,
        "path": "基础信息 / 项目编号", "path_titles": [], "has_children": False,
    }]
    try:
        _stub(project, _values(), flat)
        result = sync_service.build_sync_preview("69")
    finally:
        _restore(original)

    filled = {row["node_id"]: row["value"] for row in result["fill"]}
    assert filled["c9"] == "69"
    assert "项目编号" not in {row["title"] for row in result["unmatched"]}


def test_build_sync_preview_rejects_project_without_nodes():
    original = sync_service._load_local_context
    project = {"id": "69", "code": "69", "name": "空树项目"}
    try:
        _stub(project, {}, [])
        try:
            sync_service.build_sync_preview("69")
        except ValueError as exc:
            assert "还没有信息节点" in str(exc)
        else:
            raise AssertionError("空树项目应当报 ValueError（接口层 400）")
    finally:
        _restore(original)


# —— 一键导入（全部项目）：只落「将填写 + 将覆盖」，不建节点 ——

def _preview(fill=None, overwrite=None, unmatched=None):
    return {"fill": fill or [], "overwrite": overwrite or [], "unmatched": unmatched or []}


def _preview_row(node_id, title, value, content_type="text"):
    return {"node_id": node_id, "path": f"基础信息 / {title}", "title": title,
            "content_type": content_type, "current": "", "value": value}


def _stub_projects(rows):
    """把批量导入要用的 SessionLocal 换成假会话：只需要 query(...).order_by(...).all()。"""
    class _Query:
        def order_by(self, *_args):
            return self

        def all(self):
            return rows

    class _Session:
        def query(self, *_args, **_kwargs):
            return _Query()

        def close(self):
            pass

    node_service_module.SessionLocal = lambda: _Session()


def _stub_batch(previews, set_value):
    """打桩 build_sync_preview（按项目号给预览或抛异常）与 info_node_service.set_value。"""
    def fake_preview(project_id):
        result = previews[project_id]
        if isinstance(result, Exception):
            raise result
        return result

    sync_service.build_sync_preview = fake_preview
    node_service.set_value = set_value


def _restore_batch(original_preview, original_set_value, original_session):
    sync_service.build_sync_preview = original_preview
    node_service.set_value = original_set_value
    node_service_module.SessionLocal = original_session


def test_import_all_projects_writes_fill_and_overwrite_with_reason():
    """两类都写：将填写在前、将覆盖在后；每个值都带 change_reason 与操作人。"""
    calls = []
    previews = {
        "1": _preview(fill=[_preview_row("n1", "项目编号", "69")],
                      overwrite=[_preview_row("n2", "实施", "赵六")],
                      unmatched=[{"title": "项目类型"}, {"title": "总车数"}]),
        "2": _preview(),                      # 台账值与节点现值全一致：跑过但不用写
    }
    originals = (sync_service.build_sync_preview, node_service.set_value, node_service_module.SessionLocal)
    try:
        _stub_projects([("1", "项目一"), ("2", "项目二")])
        _stub_batch(previews, lambda project_id, node_id, value, operator=None,
                    operator_name=None, change_reason=None:
                    calls.append((project_id, node_id, value, change_reason, operator, operator_name)))
        summary = sync_service.import_all_projects(operator="admin", operator_name="管理员")
    finally:
        _restore_batch(*originals)

    assert calls == [
        ("1", "n1", "69", sync_service.IMPORT_ALL_CHANGE_REASON, "admin", "管理员"),
        ("1", "n2", "赵六", sync_service.IMPORT_ALL_CHANGE_REASON, "admin", "管理员"),
    ]
    assert summary["project_total"] == 2 and summary["project_written"] == 1
    assert summary["project_no_change"] == 1 and summary["project_skipped"] == 0
    assert (summary["filled"], summary["overwritten"]) == (1, 1)
    # 未匹配只计数、不落库（要用户去详情模板补字段，而不是就地造节点）
    assert summary["unmatched"] == 2
    assert summary["project_failed"] == 0 and summary["failures"] == []


def test_import_all_projects_skips_project_without_nodes():
    """还没有信息节点的项目算「跳过」而不是失败（与单项目同步的 400 同一情形）。"""
    originals = (sync_service.build_sync_preview, node_service.set_value, node_service_module.SessionLocal)
    try:
        _stub_projects([("1", "空树项目")])
        _stub_batch({"1": ValueError("该项目还没有信息节点，请先在编辑页新建节点后再同步")},
                    lambda *a, **k: (_ for _ in ()).throw(AssertionError("跳过就不该写值")))
        summary = sync_service.import_all_projects()
    finally:
        _restore_batch(*originals)

    assert summary["project_skipped"] == 1
    assert summary["project_failed"] == 0 and summary["failures"] == []
    assert summary["project_written"] == 0 and summary["filled"] == 0


def test_import_all_projects_records_failure_and_keeps_going():
    """单个项目写不进去不中断整批：失败记明细，后面的项目照写。"""
    written = []
    previews = {
        "1": _preview(fill=[_preview_row("n1", "销售", "张三")]),
        "2": _preview(fill=[_preview_row("n2", "销售", "李四")]),
        "3": _preview(fill=[_preview_row("n3", "销售", "王五")]),
    }

    def fake_set_value(project_id, node_id, value, **_kwargs):
        if project_id == "2":
            raise PermissionError("该节点属于其它项目，不能写入本项目")
        written.append((project_id, node_id))

    originals = (sync_service.build_sync_preview, node_service.set_value, node_service_module.SessionLocal)
    try:
        _stub_projects([("1", "项目一"), ("2", "项目二"), ("3", "项目三")])
        _stub_batch(previews, fake_set_value)
        summary = sync_service.import_all_projects()
    finally:
        _restore_batch(*originals)

    assert written == [("1", "n1"), ("3", "n3")]
    assert summary["project_failed"] == 1
    assert summary["project_written"] == 2        # 失败的那个不算「已写入」
    assert summary["filled"] == 2
    assert [item["project_id"] for item in summary["failures"]] == ["2"]
    assert "基础信息 / 销售" in summary["failures"][0]["reason"]
    assert "不能写入本项目" in summary["failures"][0]["reason"]


def test_import_all_projects_records_project_level_failure():
    """连预览都跑不了的项目（如项目行在、树读挂了）同样只记这一条，不影响别人。"""
    originals = (sync_service.build_sync_preview, node_service.set_value, node_service_module.SessionLocal)
    try:
        _stub_projects([("1", "项目一"), ("2", "项目二")])
        _stub_batch({"1": RuntimeError("数据库连接中断"),
                     "2": _preview(fill=[_preview_row("n2", "销售", "李四")])},
                    lambda *a, **k: None)
        summary = sync_service.import_all_projects()
    finally:
        _restore_batch(*originals)

    assert summary["project_failed"] == 1 and summary["project_written"] == 1
    assert summary["failures"][0]["project_name"] == "项目一"
    assert "数据库连接中断" in summary["failures"][0]["reason"]
