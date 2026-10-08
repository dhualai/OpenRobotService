"""项目信息能力：只展开点名的组，口令和地址不进文本。"""

from ai.agents.AiTaskPlatform.capabilities.tools.project_info import (
    catalog_line,
    render_project_info,
    select_groups,
)

_SECRET = "ToDesk-Secret-9f3a"
_SSH_CODE = "SSH-CODE-7781"
_IP = "10.20.30.40"


def _node(node_id, key, name, parent_id=None, value_type="text", node_type="field", sort_order=0):
    return {
        "id": node_id,
        "parent_id": parent_id,
        "node_key": key,
        "node_name": name,
        "node_type": node_type,
        "value_type": value_type,
        "sort_order": sort_order,
        "status": "active",
    }


def _value(node_id, value):
    return {"node_id": node_id, "value_json": value}


def _sample():
    nodes = [
        _node("g1", "hardware", "车型信息", node_type="group"),
        _node("m1", "hardware.vehicle.model_1", "车型1", "g1", node_type="group", sort_order=10),
        _node("v1", "hardware.vehicle.model_1.name", "型号", "m1", sort_order=11),
        _node("v2", "hardware.vehicle.model_1.quantity", "数量", "m1", sort_order=20),
        _node("r1", "network.remote.todesk", "ToDesk", node_type="group"),
        _node("r2", "network.remote.todesk.password", "密码", "r1"),
        _node("r3", "network.remote.ssh.code", "远程码", "r1"),
        _node("r4", "network.public_ip", "公网ip"),
        _node("e1", "environment.peripheral.elevator", "电梯"),
        _node("c1", "custom.dock", "月台编号", "g1"),
        _node("p1", "personnel.phone", "现场电话"),
        _node("off", "hardware.vehicle.model_2", "车型2"),
        _node("cad", "environment.map_layout.cad", "CAD源文件", value_type="attachment"),
    ]
    nodes[11]["status"] = "disabled"
    values = [
        _value("v1", "XQE-6"),
        _value("v2", 12),
        _value("r2", _SECRET),
        _value("r3", _SSH_CODE),
        _value("r4", _IP),
        _value("e1", "通力"),
        _value("c1", "A-03"),
        _value("p1", "13800001111"),
        _value("off", "不应出现"),
        _value("cad", {"filename": "layout.dwg", "object_path": "/secret/layout.dwg"}),
    ]
    return nodes, values


def test_select_groups_from_question():
    assert select_groups("这台车是什么车型") == ["hardware"]
    assert select_groups("你好") == []


def test_catalog_has_no_secret_values():
    nodes, values = _sample()
    text = catalog_line(nodes, values)
    assert "车型与载具" in text
    assert "现场环境" in text
    assert _SECRET not in text
    assert _SSH_CODE not in text
    assert _IP not in text
    assert "13800001111" not in text
    assert "远程方式已配置" in text
    assert "公网地址已填写" in text


def test_named_group_omits_other_groups_and_secrets():
    nodes, values = _sample()
    text = render_project_info(nodes, values, "看下车型和数量")
    assert "XQE-6" in text
    assert "车型1 / 数量: 12" in text
    assert "A-03" in text
    assert "通力" not in text
    assert "不应出现" not in text
    assert _SECRET not in text
    assert _SSH_CODE not in text
    assert _IP not in text
    assert "13800001111" not in text


def test_project_info_request_expands_filled_groups():
    nodes, values = _sample()
    text = render_project_info(nodes, values, "那你给我说一下项目信息吧")
    assert "XQE-6" in text
    assert "通力" in text
    assert "未指明要哪一组" not in text
    assert _SECRET not in text
    assert _IP not in text
    assert "13800001111" not in text


def test_unspecified_query_is_catalog_only():
    nodes, values = _sample()
    text = render_project_info(nodes, values, "帮我看看这个项目")
    assert "未指明要哪一组" in text
    assert "XQE-6" not in text
    assert "通力" not in text
    assert _SECRET not in text


def test_network_group_hides_address_and_password():
    nodes, values = _sample()
    text = render_project_info(nodes, values, "远程和外网怎么连")
    assert "远程方式已配置" in text
    assert "公网地址已填写" in text
    assert _SECRET not in text
    assert _SSH_CODE not in text
    assert _IP not in text


def test_environment_attachment_hides_path():
    nodes, values = _sample()
    text = render_project_info(nodes, values, "现场电梯和地图")
    assert "通力" in text
    assert "已上传附件" in text
    assert "layout.dwg" not in text
    assert "/secret/" not in text
