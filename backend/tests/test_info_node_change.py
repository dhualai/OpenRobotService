"""节点变动历史（编辑历史）纯函数测试 —— 不连库。

新版历史表 project_info_value_history 存的是**原生 JSON 的前后值**，
不再是一句拼好的变更摘要。因此这里测三块：

  1. describe_value —— 各值类型的原生 JSON 怎么转成人话（前端历史弹层直接展示）；
  2. build_value_detail —— 值变动（新增 / 修改 / 清空）的文案；
  3. 结构性操作的文案 —— 改名 / 改值类型 / 增补 / 删除 / 移动 / 导入·模板变更。

运行方式（反射 runner；**必须先 import app.core.db**——conftest 会把 create_engine
换成 MagicMock，若任由 admin 包在之后懒加载 app.core.db，event.listen 会对
MagicMock 引擎报 InvalidRequestError）：
    python -c "
    import app.core.db
    import tests.conftest
    import tests.test_info_node_change as t
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
import uuid
from types import SimpleNamespace

from app.modules.admin.services.info_node_change_service import (
    _new_id,
    build_import_detail,
    build_node_create_detail,
    build_node_delete_detail,
    build_node_move_detail,
    build_rename_detail,
    build_reset_detail,
    build_sync_detail,
    build_value_detail,
    describe_value,
    subtree_node_ids,
)


class TestDescribeValue:
    def test_文字折叠空白(self):
        assert describe_value("text", "  4 台\n叉车  ") == "4 台 叉车"

    def test_多选顿号连接(self):
        assert describe_value("multi_select", ["叉车", "AGV", ""]) == "叉车、AGV"

    def test_单选取已选项(self):
        assert describe_value("select", {"selected": "试点项目", "options": ["试点项目"]}) == "试点项目"

    def test_附件取文件名(self):
        assert describe_value("attachment", {"name": "方案.pdf", "resource_id": 7}) == "方案.pdf"
        # 多附件列表
        assert describe_value("attachment", [{"name": "a.pdf"}, {"name": "b.png"}]) == "a.pdf、b.png"
        # 结构不认识就返回空，不抛异常
        assert describe_value("attachment", {"resource_id": 7}) == ""

    def test_人员取姓名(self):
        assert describe_value("person", {"name": "张三", "id": 3}) == "张三"
        assert describe_value("person", [{"name": "张三"}, {"name": "李四"}]) == "张三、李四"

    def test_布尔转是否(self):
        assert describe_value("boolean", True) == "是"
        assert describe_value("boolean", False) == "否"

    def test_对象按紧凑JSON兜底(self):
        assert describe_value("json", {"b": 1, "a": 2}) == '{"a": 2, "b": 1}'

    def test_数字与日期原样(self):
        assert describe_value("number", "12.5") == "12.5"
        assert describe_value("date", "2026-09-17") == "2026-09-17"

    def test_空值(self):
        assert describe_value("text", None) == ""
        assert describe_value("text", "") == ""


class TestValueDetail:
    def test_新增值(self):
        assert build_value_detail("text", None, "中力", "create") == "填写内容「中力」"

    def test_修改值(self):
        assert build_value_detail("text", "中力", "XX科技", "update") == "把内容从「中力」改为「XX科技」"

    def test_清空值(self):
        detail = build_value_detail("text", "中力", "", "update")
        assert detail == "把内容从「中力」改为「空」"

    def test_删除操作带出原值(self):
        assert build_value_detail("text", "中力", None, "delete") == "清空了内容（原为「中力」）"

    def test_值为空时显示空(self):
        assert "「空」" in build_value_detail("text", None, None, "create")

    def test_超长值截断(self):
        detail = build_value_detail("text", "", "长" * 200, "create")
        assert "…" in detail
        assert len(detail) < 160


class TestRenameDetail:
    def test_只改名(self):
        assert build_rename_detail("客户信息", "客户名称", False, "text", "text") == \
            "把名称从「客户信息」改为「客户名称」"

    def test_改名并改类型(self):
        detail = build_rename_detail("数量", "数量", True, "text", "number")
        assert detail == "把值类型从「文字输入」改为「数字」"

    def test_改名与改类型同时(self):
        detail = build_rename_detail("数量", "台数", True, "text", "number")
        assert detail == "把名称从「数量」改为「台数」；把值类型从「文字输入」改为「数字」"

    def test_无变化返回空串(self):
        assert build_rename_detail("数量", "数量", False, "text", "text") == ""


class TestStructuralDetails:
    def test_增补节点带父名(self):
        assert build_node_create_detail("月台编号", "硬件") == "在「硬件」下增补节点「月台编号」"
        assert build_node_create_detail("月台编号", None) == "增补节点「月台编号」"

    def test_删除无子节点(self):
        assert build_node_delete_detail("充电区位置", 0) == "删除节点「充电区位置」"

    def test_删除含子树(self):
        assert build_node_delete_detail("车辆", 3) == "删除节点「车辆」及其 3 个子节点"

    def test_移动换父(self):
        assert build_node_move_detail("叉车1", "车辆", "设备") == "把「叉车1」从「车辆」移到「设备」下"

    def test_移动移到最外层(self):
        assert build_node_move_detail("叉车1", "车辆", None) == "把「叉车1」从「车辆」移到最外层"

    def test_移动从最外层挂到某节点(self):
        assert build_node_move_detail("叉车1", None, "车辆") == "把「叉车1」从最外层移到「车辆」下"

    def test_移动同父同级排序(self):
        assert build_node_move_detail("叉车1", "车辆", "车辆", same_parent=True) == \
            "调整了「叉车1」在同级中的顺序"

    def test_导入文案区分来源(self):
        assert build_import_detail("import", 120) == "导入信息树：新增 120 个节点"
        assert build_import_detail("template", 120) == "按预设模板同步信息树：新增 120 个节点"

    def test_模板变更文案(self):
        assert build_sync_detail(2, 5, 1) == "全局模板变更：新增 2 个、更新 5 个、停用 1 个节点"

    def test_一键清空整树级文案(self):
        # 两样都动了：都报出来（删除在前——结构变动比值更醒目）
        assert build_reset_detail(4, 132) == "一键清空（恢复为模板结构）：删除 4 个增补节点，清空 132 项已填内容"
        # 只动了其中一样：另一项不出现（调用方本来也只在至少动了一样时才写这条）
        assert build_reset_detail(4, 0) == "一键清空（恢复为模板结构）：删除 4 个增补节点"
        assert build_reset_detail(0, 7) == "一键清空（恢复为模板结构）：清空 7 项已填内容"


class TestSubtreeNodeIds:
    """一级标签的「修改记录」按子树取记录，子树 id 的算法（纯函数，不连库）。

    行只用到 id / parent_id；调用方传的是按 sort_order 排好的行，
    同级顺序靠入参顺序保持（与 get_tree 组装出的顺序一致）。
    """

    def _row(self, node_id, parent_id):
        return SimpleNamespace(id=node_id, parent_id=parent_id)

    def test_含根节点且按先序(self):
        rows = [
            self._row("r", None),
            self._row("a", "r"),
            self._row("a1", "a"),
            self._row("a2", "a"),
            self._row("b", "r"),
        ]
        assert subtree_node_ids(rows, "r") == ["r", "a", "a1", "a2", "b"]
        # 从中间一层取也一样：自己在前，子孙随后
        assert subtree_node_ids(rows, "a") == ["a", "a1", "a2"]

    def test_兄弟分支与本树之外的根不进来(self):
        rows = [
            self._row("r", None),
            self._row("a", "r"),
            self._row("other", None),
            self._row("other1", "other"),
        ]
        assert subtree_node_ids(rows, "r") == ["r", "a"]

    def test_同级顺序保持入参顺序(self):
        # sort_order 已由调用方排好：入参里 a2 在 a1 前，先序就按这个来
        rows = [self._row("r", None), self._row("a2", "r"), self._row("a1", "r")]
        assert subtree_node_ids(rows, "r") == ["r", "a2", "a1"]

    def test_根节点已不在节点表里时只有它自己(self):
        # 看一个已被删除的节点的历史：rows 里查不到它，与 list_for_node 表现一致
        rows = [self._row("r", None), self._row("a", "r")]
        assert subtree_node_ids(rows, "gone") == ["gone"]

    def test_脏数据成环不死循环(self):
        rows = [self._row("a", "b"), self._row("b", "a")]
        assert subtree_node_ids(rows, "a") == ["a", "b"]

    def test_接受字典行(self):
        rows = [{"id": "r", "parent_id": None}, {"id": "a", "parent_id": "r"}]
        assert subtree_node_ids(rows, "r") == ["r", "a"]


class TestRecordId:
    """记录 id 用时间有序的 UUID：changed_at 只到秒，同秒多条的展示顺序靠 id 兜底。"""

    def test_是合法uuid且随时间递增(self):
        ids = [_new_id() for _ in range(5)]
        for value in ids:
            assert uuid.UUID(value).version == 7
        assert ids == sorted(ids)

    def test_同毫秒也不重复(self):
        assert len({_new_id() for _ in range(200)}) == 200
