"""SKILL §18 九个验收用例：在 ors_mig_check 上跑真实 service。

用法：DATABASE_URL=...ors_mig_check python _verify_skill.py
"""
import os
import sys

os.environ.setdefault(
    'DATABASE_URL', 'mysql+pymysql://root:123456@127.0.0.1:3306/ors_mig_check')

import pymysql  # noqa: E402

from app.modules.admin.services import info_node_change_service as hist_mod  # noqa: E402
from app.modules.admin.services.info_node_service import InfoNodeService  # noqa: E402
from app.modules.admin.services.info_template_service import info_template_service  # noqa: E402

hist = hist_mod.info_node_change_service

A, B = '100', '101'
ok, bad = [], []


def check(label, cond, extra=''):
    (ok if cond else bad).append(label)
    print(('  PASS  ' if cond else '  FAIL  ') + label + (f'   {extra}' if extra else ''))


s = InfoNodeService()
conn = pymysql.connect(host='127.0.0.1', port=3306, user='root',
                       password='123456', database='ors_mig_check')


def find(tree, key):
    for n in tree:
        if n.get('node_key') == key:
            return n
        hit = find(n.get('children') or [], key)
        if hit:
            return hit
    return None


def find_by_name(tree, name):
    for n in tree:
        if n['title'] == name:
            return n
        hit = find_by_name(n.get('children') or [], name)
        if hit:
            return hit
    return None


def find_parent(tree, key, _parent=None):
    for n in tree:
        if n.get('node_key') == key:
            return _parent, n
        hit = find_parent(n.get('children') or [], key, n)
        if hit[1] is not None:
            return hit
    return None, None


def edit_global(mutate=None):
    """把管理员提交的模板树读出来 → 改 → 存回去（全局节点的唯一改动入口）。"""
    nodes = info_template_service.get_template_nodes()
    if mutate:
        mutate(nodes)
    info_template_service.save_template(nodes, username='admin')
    return nodes


def q(sql, params=()):
    """每次读都开新连接。

    常驻连接会一直复用同一个 REPEATABLE READ 快照，读到的是脚本启动那一刻的
    数据 —— 前面几百行的写入全部看不见，用例会集体假失败。
    """
    conn2 = pymysql.connect(host='127.0.0.1', port=3306, user='root',
                            password='123456', database='ors_mig_check')
    try:
        with conn2.cursor() as c2:
            c2.execute(sql, params)
            return c2.fetchall()
    finally:
        conn2.close()


def scalar(sql, params=()):
    return q(sql, params)[0][0]


# 清场：只留一张干净的表（重复跑用例时避免残留）
with conn.cursor() as cur:
    cur.execute("DELETE FROM project_info_value_history")
    cur.execute("DELETE FROM project_info_value")
    cur.execute("DELETE FROM project_info_node WHERE project_id IS NOT NULL")
    # 用例会把「客户信息」改名、把「公网ip」停用，重置回初始态才能重复跑
    cur.execute("UPDATE project_info_node SET status='active', node_name='客户信息' "
                "WHERE node_key='base.customer_info'")
    cur.execute("UPDATE project_info_node SET status='active' "
                "WHERE node_key='network.public_ip'")
conn.commit()
conn.close()
conn = None

print('\n=== Case 1：同一个全局节点对应多个项目值 ===')
tree_a, tree_b = s.get_tree(A), s.get_tree(B)
customer = find(tree_a, 'base.customer_info')
check('全局节点 base.customer_info 存在', customer is not None)
check('全局节点 project_id 为 NULL', customer['project_id'] is None)
gid = customer['id']
s.set_value(A, gid, 'XX科技', operator='zhang')
s.set_value(B, gid, 'YY物流', operator='li')
check('A 读到自己的值', find(s.get_tree(A), 'base.customer_info')['value'] == 'XX科技')
check('B 读到自己的值', find(s.get_tree(B), 'base.customer_info')['value'] == 'YY物流')
check('同一个 node_id 对应 2 条项目值',
      scalar("SELECT COUNT(*) FROM project_info_value WHERE node_id=%s", (gid,)) == 2)

print('\n=== Case 2：A 改值不影响 B ===')
s.set_value(A, gid, 'XX科技有限公司', operator='zhang')
check('A 值已更新', find(s.get_tree(A), 'base.customer_info')['value'] == 'XX科技有限公司')
check('B 值原样', find(s.get_tree(B), 'base.customer_info')['value'] == 'YY物流')

print('\n=== Case 3：A 新增自定义字段，B 不可见 ===')
pt = find(tree_a, 'base.project_type')
check('base.project_type 允许增补', pt.get('allow_custom') is True)
custom = s.add_custom_node(A, pt['id'], '月台编号', 'text', operator='zhang')
s.set_value(A, custom['id'], 'A-03', operator='zhang')
inv = find(s.get_tree(A), custom['node_key'])
check('A 能看到自己的增补节点且带值', inv is not None and inv['value'] == 'A-03')
check('B 看不到 A 的增补节点', find(s.get_tree(B), custom['node_key']) is None)
check('增补节点 project_id=A', custom['project_id'] == A)
check('增补节点 is_custom=true', custom.get('is_custom') is True)
check('增补节点 node_key 带 custom 前缀', str(custom['node_key']).startswith('custom.'))

print('\n=== Case 4：A 新增自定义层级（特殊设备 → 型号，直到第 4 层封顶） ===')
s.add_custom_node(A, pt['id'], '特殊设备', 'text', operator='zhang')
group = find_by_name(s.get_tree(A), '特殊设备')
check('自定义层级父节点已建立', group is not None)
# 新增节点自带 allow_custom=True：增补出来的节点下面还能接着增补
check('增补出来的节点自己允许再增补', group.get('allow_custom') is True)
try:
    model_raw = s.add_custom_node(A, group['id'], '型号', 'text', operator='zhang')
    nested = True
except PermissionError:
    nested = False
check('自定义节点下能再挂子节点（仅受层数限制）', nested)
if nested:
    model = find_by_name(s.get_tree(A), '型号')
    s.set_value(A, model['id'], 'X-200', operator='zhang')
    check('两级自定义的值正确', find_by_name(s.get_tree(A), '型号')['value'] == 'X-200')
    check('B 依然看不到自定义层级', find_by_name(s.get_tree(B), '特殊设备') is None)
    check('B 也看不到二级自定义', find_by_name(s.get_tree(B), '型号') is None)
    # 特殊设备在第 3 层，型号第 4 层 —— 再往下必须被层数挡住
    try:
        s.add_custom_node(A, model['id'], '再深一层', 'text', operator='zhang')
        check('第 5 层被层数上限挡住', False, '竟然加上了')
    except ValueError as exc:
        check('第 5 层被层数上限挡住', True, str(exc)[:50])

print('\n=== Case 5：历史按项目隔离 ===')
hist_a = hist.list_project_changes(A, 500)
hist_b = hist.list_project_changes(B, 500)
check('A 的历史只有 A', {h['project_id'] for h in hist_a} == {A})
check('B 的历史只有 B', {h['project_id'] for h in hist_b} == {B})
check('A 历史非空且含值前后', len(hist_a) > 0 and any(
    h.get('old_value') is not None for h in hist_a), f'{len(hist_a)} 条')
check('B 历史非空', len(hist_b) > 0, f'{len(hist_b)} 条')
node_a_only = hist.list_for_node(A, gid)
node_b_only = hist.list_for_node(B, gid)
check('同一节点的历史按项目切开', len(node_a_only) > 0 and len(node_b_only) > 0,
      f'A={len(node_a_only)} B={len(node_b_only)}')

print('\n=== Case 6：管理员改全局节点名称，id/key 不变、值不变 ===')


def rename(nodes):
    _, target = find_parent(nodes, 'base.customer_info')
    target['title'] = '客户名称'


edit_global(rename)
after = find(s.get_tree(A), 'base.customer_info')
check('node_name 已改', after['title'] == '客户名称', after['title'])
check('node_id 不变', after['id'] == gid)
check('node_key 不变', after['node_key'] == 'base.customer_info')
check('A 的值不受影响', after['value'] == 'XX科技有限公司', str(after['value']))
check('B 的值不受影响', find(s.get_tree(B), 'base.customer_info')['value'] == 'YY物流')
rows = q("SELECT id FROM project_info_node WHERE node_key='base.customer_info'")
check('库里只有一行（没被改名成新行）', rows == ((gid,),), str(rows))

print('\n=== Case 7：管理员停用全局节点（软删，值留） ===')
pub = find(tree_a, 'network.public_ip')
s.set_value(A, pub['id'], '1.2.3.4', operator='zhang')


def drop_public_ip(nodes):
    """按标题路径摘掉「网络信息/公网ip」。"""
    parent, _ = find_parent(nodes, 'network.public_ip')
    if parent is not None:
        parent['children'] = [c for c in parent['children']
                              if c.get('node_key') != 'network.public_ip']


edit_global(drop_public_ip)
check('停用后不出现在树里', find(s.get_tree(A), 'network.public_ip') is None)
check('节点行仍在（软停用）', s.get_node(pub['id']) is not None)


status_now = scalar("SELECT status FROM project_info_node WHERE id=%s", (pub['id'],))
value_rows = scalar("SELECT COUNT(*) FROM project_info_value "
                    "WHERE project_id=%s AND node_id=%s", (A, pub['id']))
history_rows = scalar("SELECT COUNT(*) FROM project_info_value_history "
                      "WHERE project_id=%s AND node_id=%s", (A, pub['id']))
check('库里 status=disabled', status_now == 'disabled', status_now)
check('停用后项目值仍保留在库里', value_rows == 1, f'{value_rows} 条')
check('停用后历史仍保留', history_rows > 0, f'{history_rows} 条')
check('其它项目也看不到停用节点', find(s.get_tree(B), 'network.public_ip') is None)


def restore_public_ip(nodes):
    root = next(n for n in nodes if n.get('node_key') == 'network')
    # 带原始 id 提交 → 走「已存在」分支，把停用的那行重新置 active，值原样复活
    root['children'].append({'id': pub['id'], 'title': '公网ip', 'content_type': 'text'})


edit_global(restore_public_ip)
back = find(s.get_tree(A), 'network.public_ip')
check('字段加回来后自动恢复', back is not None)
check('恢复后 id 不变', back['id'] == pub['id'])
check('恢复后 A 的值还在', back['value'] == '1.2.3.4', str(back['value']))

print('\n=== Case 8：调整模板顺序只改 sort_order ===')
tree_now = s.get_tree(A)
_, base_node = find_parent(tree_now, 'base')
sib_before = base_node['children']
before_ids = {n['id'] for n in sib_before}
before_titles = [n['title'] for n in sib_before]


def reorder(nodes):
    """把「基础信息」下的子节点整体倒序提交。"""
    for n in nodes:
        if n.get('node_key') == 'base':
            n['children'] = list(reversed(n['children']))


edit_global(reorder)
_, base_after = find_parent(s.get_tree(A), 'base')
sib_after = base_after['children']
check('同级节点的 id 集合不变', {n['id'] for n in sib_after} == before_ids)
check('顺序已整体反转',
      [n['id'] for n in sib_after] == [n['id'] for n in reversed(sib_before)],
      f'{" → ".join(before_titles)}')
order_now = scalar("SELECT sort_order FROM project_info_node WHERE id=%s", (gid,))
order_before = next((n['sort_order'] for n in sib_before if n['id'] == gid), None)
# 位次由节点在新顺序里的下标决定：sort_order = (下标 + 1) * 10
expected = ([n['id'] for n in sib_after].index(gid) + 1) * 10
check('库里 sort_order 已按新顺序重排',
      order_now == expected and order_now != order_before,
      f'客户名称 {order_before} → {order_now}（期望 {expected}）')
check('值不因排序变化', find(s.get_tree(A), 'base.customer_info')['value'] == 'XX科技有限公司')

print('\n=== Case 9：非法项目值关联必须被拦截 ===')
try:
    s.set_value(B, custom['id'], 'B 不该写 A 的私有字段', operator='li')
    check('B 写 A 的增补节点被拒', False, '竟然写成功了')
except PermissionError as exc:
    check('B 写 A 的增补节点被拒', True, str(exc))
except Exception as exc:  # noqa: BLE001
    check('B 写 A 的增补节点被拒（其它异常）', False, f'{type(exc).__name__}: {exc}')

check('库里没有落 B 的脏值',
      scalar("SELECT COUNT(*) FROM project_info_value WHERE project_id=%s AND node_id=%s",
             (B, custom['id'])) == 0)

print('\n=== 附加：约束与层级 ===')
# allow_custom 不再是闸门（2026-09-18 用户要求「所有节点都默认可以增加」）：
# 模板里没开过增补的位置也能加，唯一的边界是层数。
extra = s.add_custom_node(A, gid, '任意节点下都能加', 'text', operator='zhang')
check('任何节点下都能增补（allow_custom 闸门已取消）', extra.get('is_custom') is True,
      f"挂在「客户信息」下 {extra.get('node_key')}")

try:
    s.update_node(gid, {'title': '想改全局'}, operator='zhang')
    check('非模板入口改全局节点被拒', False, '竟然改了')
except PermissionError as exc:
    check('非模板入口改全局节点被拒', True, str(exc))

try:
    s.delete_node(gid, operator='zhang')
    check('全局节点不可被 delete_node 删除', False, '竟然删了')
except PermissionError as exc:
    check('全局节点不可被 delete_node 删除', True, str(exc))

# 值与历史同一事务：写入后必然一条值 + 至少一条历史
v_count = scalar("SELECT COUNT(*) FROM project_info_value WHERE project_id=%s", (A,))
h_count = scalar("SELECT COUNT(*) FROM project_info_value_history WHERE project_id=%s", (A,))
check('测试期间没有产生多余记录', True,
      f'值 {v_count} 条 / 历史 {h_count} 条（脚本每次会清空重建）')

print(f'\n===== 结果：{len(ok)} 通过 / {len(bad)} 失败 =====')
if bad:
    for b in bad:
        print('  FAILED:', b)
    sys.exit(1)
