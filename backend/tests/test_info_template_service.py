"""详情模板服务测试 —— 校验规范化 / 节点清单派生 / 保存前预览。

新版模板不再是「一份 JSON + 逐项目同步」：全局节点定义直接就是
project_info_node 里 project_id IS NULL 的那些行，保存即全项目生效。
因此这里测的是三件事：
  1. normalize_template_nodes —— 管理员提交的树是否合法、规范化成什么形状；
  2. InfoTemplateService._collect_actions —— 树展平成待落库节点清单（父在子前、
     已存在节点沿用库里 id、新节点按标题路径派生稳定 id 与 node_key）；
  3. preview_sync —— 保存前的「会停用哪些字段」预览。

不连库：需要读库的两处用假 session 顶掉（SessionLocal 是模块级符号，替换即生效）。
运行方式（反射 runner；**必须先 import app.core.db**——conftest 会把 create_engine
换成 MagicMock，若任由 admin 包在之后懒加载 app.core.db，event.listen 会对
MagicMock 引擎报 InvalidRequestError）：
    python -c "
    import app.core.db
    import tests.conftest
    import tests.test_info_template_service as t
    import inspect
    n=0
    for name, obj in vars(t).items():
        if inspect.isclass(obj) and name.startswith('Test'):
            inst = obj()
            for m in dir(obj):
                if m.startswith('test_'): getattr(inst, m)(); n+=1
    print('PASS', n)
    "
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import app.modules.admin.services.info_template_service as tpl_mod
from app.modules.admin.services.info_template_service import (
    InfoTemplateService,
    normalize_template_nodes,
    template_to_legacy_tree,
)


def _tpl():
    """一份贴近真实模板的两级结构（含 select 与 3 层深的车型分支）。"""
    return [
        {
            "id": "t-base", "title": "基础信息",
            "children": [
                {"id": "t-cust", "title": "客户信息"},
                {"id": "t-type", "title": "项目类型", "value_type": "select",
                 "options": ["试点项目", "PK项目"]},
            ],
        },
        {
            "id": "t-hw", "title": "硬件",
            "children": [
                {"id": "t-veh", "title": "车辆",
                 "children": [{"id": "t-m1", "title": "车型1"}]},
            ],
        },
    ]


def _flat():
    return InfoTemplateService()._collect_actions(
        normalize_template_nodes(_tpl()), {}, (), None)


def _by_name(nodes=None):
    """提交的树里全是中文标题 → node_key/id 都按标题路径派生，用标题索引更好认。"""
    return {a["node_name"]: a for a in (nodes if nodes is not None else _flat())}


def _row(node_id, key, name, *, parent=None, order=10, status='active'):
    return SimpleNamespace(id=node_id, node_key=key, node_name=name,
                           parent_id=parent, sort_order=order, status=status)


class TestNormalize:
    def test_规范化补齐默认字段(self):
        nodes = normalize_template_nodes([
            {"id": "a", "title": " 基础信息 ", "children": [
                {"id": "b", "title": "客户信息"},
                {"id": "c", "title": "项目类型", "value_type": "select",
                 "options": ["试点项目", " PK项目 "]},
            ]},
        ])
        top = nodes[0]
        assert top["title"] == "基础信息"                     # 去空白
        assert top["sort_order"] == 10                        # 按下标派生的步长 10
        assert top["value_type"] == "text" and top["content_type"] == "text"
        assert top["required"] is False
        # allow_custom 一律 true：所有节点都能被各项目增补（2026-09-18 取消开关），
        # 提交里显式带 false 也被归一（老前端/历史模板提交的树）
        assert top["allow_custom"] is True
        assert normalize_template_nodes([{"title": "甲", "allow_custom": False}])[0]["allow_custom"] is True
        # 同级第二个节点位次 20
        assert [c["sort_order"] for c in top["children"]] == [10, 20]
        assert top["children"][1]["value_type"] == "select"
        # 选项去空白
        assert top["children"][1]["options"] == ["试点项目", "PK项目"]

    def test_同一父节点下同名被拒(self):
        # 查重在 save_template 里由 _assert_keys_unique 负责，不在规范化阶段
        svc = InfoTemplateService()
        for tree in (
            [{"title": "基础信息"}, {"title": "基础信息"}],               # 顶层同层
            [{"title": "甲", "children": [{"title": "子"}, {"title": "子"}]}],  # 子层同层
        ):
            try:
                svc._assert_keys_unique(normalize_template_nodes(tree))
            except ValueError as exc:
                assert "同名" in str(exc), exc
            else:
                raise AssertionError(f"同层同名节点应被拒绝：{tree}")

    def test_不同分支下同名允许(self):
        # 硬件/厂家 与 网络/厂家 是合法结构，seen 不能跨分支共用，否则改个名字就存不下去
        svc = InfoTemplateService()
        svc._assert_keys_unique(normalize_template_nodes([
            {"title": "硬件", "children": [{"title": "厂家"}]},
            {"title": "网络", "children": [{"title": "厂家"}]},
        ]))

    def test_拒绝非法模板(self):
        cases = [
            ([], "不能为空"),
            ([{"title": "  "}], "标题不能为空"),
        ]
        for nodes, keyword in cases:
            try:
                normalize_template_nodes(nodes)
            except ValueError as exc:
                assert keyword in str(exc), f"{keyword} 未出现在：{exc}"
            else:
                raise AssertionError(f"应拒绝：{keyword}")

    def test_下拉节点允许带子节点(self):
        # 车型1 就是「下拉选中型号 + 数量子节点」：值类型与子节点互不排斥
        got = normalize_template_nodes([
            {"title": "硬件", "children": [
                {"title": "车辆", "children": [
                    {"title": "车型1", "value_type": "select", "options": ["XC1051"],
                     "children": [{"title": "数量"}]},
                ]},
            ]},
        ])
        model = got[0]["children"][0]["children"][0]
        assert model["value_type"] == "select"
        assert model["content_type"] == "select"
        assert model["options"] == ["XC1051"]
        assert model["children"][0]["title"] == "数量"

    def test_根节点值类型归一为text(self):
        # 一级标签只作分组、自己不填值：传什么类型都归一到 text
        got = normalize_template_nodes([
            {"title": "硬件", "value_type": "select", "options": ["托盘"]},
        ])
        assert got[0]["value_type"] == "text"
        assert got[0]["content_type"] == "text"

    def test_超过四层被拒(self):
        leaf = {"title": "第五层"}
        tree = [{"title": "一", "children": [{"title": "二", "children": [
            {"title": "三", "children": [{"title": "第四层", "children": [leaf]}]}]}]}]
        try:
            normalize_template_nodes(tree)
        except ValueError as exc:
            assert "层" in str(exc), exc
        else:
            raise AssertionError("超过 4 层应被拒绝")

    def test_无id节点允许提交(self):
        # id 由服务端派生（按标题路径），前端新建节点不必自带 id
        assert normalize_template_nodes([{"title": "新分组"}])[0]["id"] is None


class TestCollectActions:
    def test_父母在子之前且父子关系正确(self):
        flat = _flat()
        names = [a["node_name"] for a in flat]
        by_name = _by_name(flat)
        assert names.index("硬件") < names.index("车辆") < names.index("车型1")
        assert by_name["车辆"]["parent_id"] == by_name["硬件"]["id"]
        assert by_name["车型1"]["parent_id"] == by_name["车辆"]["id"]

    def test_节点类型按结构与层级判定(self):
        by_name = _by_name()
        # 顶层一律 root（与迁移播种同规则），哪怕它有子节点
        assert by_name["基础信息"]["node_type"] == "root"
        assert by_name["硬件"]["node_type"] == "root"
        # 非顶层有子节点是 group，末级是 field
        assert by_name["车辆"]["node_type"] == "group"
        assert by_name["客户信息"]["node_type"] == "field"

    def test_已存在节点沿用库里的id与key(self):
        base = _by_name()["基础信息"]
        existing = {base["id"]: _row(base["id"], "base", "基础信息（旧名）")}
        flat = InfoTemplateService()._collect_actions(
            normalize_template_nodes(_tpl()), existing, (), None)
        got = next(a for a in flat if a["id"] == base["id"])
        assert got["node_key"] == "base"       # 改名不换 key（外部锚点不失效）
        assert got["node_name"] == "基础信息"   # 标题以提交为准

    def test_标题路径派生稳定id与key(self):
        flat = InfoTemplateService()._collect_actions(
            normalize_template_nodes([
                {"title": "base", "children": [{"title": "customer_info"}]},
            ]), {}, (), None)
        by_name = _by_name(flat)
        # 三段路径全是 ASCII → key 就是点分路径
        assert by_name["customer_info"]["node_key"] == "base.customer_info"
        # 同一个标题路径每次派生同一个 id（重启/重复保存不新建节点）
        again = _by_name(InfoTemplateService()._collect_actions(
            normalize_template_nodes([{"title": "base", "children": [{"title": "customer_info"}]}]),
            {}, (), None))
        assert again["customer_info"]["id"] == by_name["customer_info"]["id"]

    def test_纯中文标题退回id派生key(self):
        by_name = _by_name()
        # 标题含中文：不能拼出半截 key，整体退回 tpl.<id>
        assert by_name["基础信息"]["node_key"].startswith("tpl.")
        assert by_name["硬件"]["node_key"].startswith("tpl.")
        # 两个不同的中文顶层节点 key 不重复
        assert by_name["基础信息"]["node_key"] != by_name["硬件"]["node_key"]

    def test_select选项落到config(self):
        assert _by_name()["项目类型"]["config"] == {"options": [
            {"value": "试点项目", "label": "试点项目"},
            {"value": "PK项目", "label": "PK项目"},
        ]}

    def test_无选项的节点config为空(self):
        assert _by_name()["客户信息"]["config"] is None


class TestLegacyTree:
    def test_补上展示用的children与options(self):
        tree = template_to_legacy_tree(normalize_template_nodes(_tpl()))
        top = {n["title"]: n for n in tree}
        assert [c["title"] for c in top["基础信息"]["children"]] == ["客户信息", "项目类型"]
        # 没有选项的节点也给空列表，前端不用判 undefined
        assert top["基础信息"]["children"][0]["options"] == []
        assert top["硬件"]["children"][0]["children"][0]["title"] == "车型1"


class _FakeSession:
    """顶掉模块级 SessionLocal：query() 链上两种调用都能返回预设行。"""

    def __init__(self, global_rows, project_count=0):
        self.db = MagicMock()
        self.db.query.return_value.filter.return_value.order_by.return_value.all.return_value = global_rows
        self.db.query.return_value.filter.return_value.count.return_value = project_count

    def __enter__(self):
        self.old = tpl_mod.SessionLocal
        tpl_mod.SessionLocal = lambda: self.db
        return self

    def __exit__(self, *exc):
        tpl_mod.SessionLocal = self.old


class TestPreviewSync:
    def test_预览列出将被停用的字段(self):
        base = _by_name()["基础信息"]
        rows = [
            _row(base["id"], "tpl.x", "基础信息"),
            _row("t-old", "tpl.old", "废弃字段", parent=base["id"]),
            _row("t-off", "tpl.off", "早就停用的", parent=base["id"], status="disabled"),
        ]
        with _FakeSession(rows, project_count=7):
            info = InfoTemplateService().preview_sync(_tpl())

        # 提交的树里没有 t-old → 停用；t-off 已停用，不重复计数
        assert info["dry_run"] is True
        assert info["disabled_titles"] == ["废弃字段"]
        assert info["deleted"] == 1
        assert info["projects"] == 7          # 全局字段改动覆盖全部项目
        assert info["changed_projects"] == 0  # 不再有逐项目改动

    def test_预览统计新增与沿用(self):
        base = _by_name()["基础信息"]
        with _FakeSession([_row(base["id"], "tpl.x", "基础信息")]):
            info = InfoTemplateService().preview_sync(_tpl())

        # 提交的树共 6 个节点：基础信息沿用，其余 5 个新建
        assert info["updated"] == 1
        assert info["added"] == 5
        assert info["deleted"] == 0

    def test_预览不报错于跨分支同名(self):
        # 不同分支下的同名末级字段是合法的，预览不应因它抛错
        with _FakeSession([]):
            info = InfoTemplateService().preview_sync([
                {"title": "硬件", "children": [{"title": "厂家"}]},
                {"title": "网络", "children": [{"title": "厂家"}]},
            ])
        assert info["added"] == 4
