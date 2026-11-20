import { describe, it, expect } from 'vitest';
import { buildHistoryMarkdown, historyActionName, type HistoryDocNode } from '../historyMarkdown';
import type { ApiInfoNodeChange } from '@/api/infoNodes';

const node = (
  id: string,
  parent_id: string | null,
  title: string,
  sort_order = 0,
): HistoryDocNode => ({ id, parent_id, title, sort_order });

const NODES: HistoryDocNode[] = [
  node('r', null, '基础信息'),
  node('c1', 'r', '客户信息'),
  node('p1', 'r', '订单信息', 10),
  node('c2', 'p1', 'ERP'),
  node('r2', null, '硬件', 20), // 另一个一级标签：不属于本次这棵子树
];

const record = (partial: Partial<ApiInfoNodeChange> & { id: string }): ApiInfoNodeChange => ({
  node_id: 'r',
  parent_id: null,
  node_title: '基础信息',
  action: 'update',
  operator: null,
  operator_name: null,
  detail: null,
  created_at: '2026-09-14 11:00:00',
  ...partial,
});

const root = NODES[0];

describe('buildHistoryMarkdown（一级标签的子树修改记录）', () => {
  it('按树的先序分组：根自己在前，子孙逐层跟在后面', () => {
    const md = buildHistoryMarkdown(root, [
      record({ id: 'h1', node_id: 'c2', parent_id: 'p1', node_title: 'ERP', detail: '填写内容「SAP ECC」' }),
      record({ id: 'h2', node_id: 'p1', node_title: '订单信息', detail: '把内容从「空」改为「2」' }),
      record({ id: 'h3', node_id: 'r', detail: '把内容从「基础」改为「基础信息」' }),
    ], NODES);

    expect(md).toContain('# 基础信息 · 修改记录');
    // 分组标题用整条路径，顺序 = 先序（父在前、同级按 sort_order）
    expect(md.indexOf('## 基础信息\n')).toBeLessThan(md.indexOf('## 基础信息 / 订单信息\n'));
    expect(md.indexOf('## 基础信息 / 订单信息\n')).toBeLessThan(md.indexOf('## 基础信息 / 订单信息 / ERP'));
    // 没有记录的节点不占一节
    expect(md).not.toContain('## 基础信息 / 客户信息');
    // 不属于这棵子树的一级标签不进来
    expect(md).not.toContain('硬件');
  });

  it('组内保持后端给的顺序（最新在前），不重排', () => {
    const md = buildHistoryMarkdown(root, [
      record({ id: 'h1', node_id: 'p1', node_title: '订单信息', detail: '新的一条', created_at: '2026-09-20 09:00:00' }),
      record({ id: 'h2', node_id: 'p1', node_title: '订单信息', detail: '旧的一条', created_at: '2026-09-01 09:00:00' }),
    ], NODES);

    expect(md.indexOf('新的一条')).toBeLessThan(md.indexOf('旧的一条'));
  });

  it('每行是「时间 · 操作人 · 动作：变动」，动作用中文名', () => {
    const md = buildHistoryMarkdown(root, [
      record({
        id: 'h1', operator: 'zhangsan', operator_name: '张三',
        detail: '把内容从「中力」改为「浙江中力」', created_at: '2026-09-14 11:00:00',
      }),
    ], NODES);

    expect(md).toContain('- **2026-09-14 11:00:00** 张三 · 修改：把内容从「中力」改为「浙江中力」');
    // 表头：条数 / 涉及的节点数 / 时间范围
    expect(md).toContain('共 1 条记录 · 涉及 1 个节点 · 2026-09-14 11:00:00 ~ 2026-09-14 11:00:00');
  });

  it('节点已从树里删掉：单列一组用记录里的名称快照，排在最后', () => {
    const md = buildHistoryMarkdown(root, [
      record({ id: 'h1', node_id: 'gone', parent_id: 'r', node_title: '旧地址', action: 'delete', detail: '删除节点「旧地址」及 2 个子节点', created_at: '2026-09-10 09:00:00' }),
      record({ id: 'h2', node_id: 'r', detail: '改了根节点', created_at: '2026-09-12 09:00:00' }),
    ], NODES);

    expect(md).toContain('## 已删除 · 旧地址');
    expect(md.indexOf('## 基础信息\n')).toBeLessThan(md.indexOf('## 已删除 · 旧地址'));
    expect(md).toContain('删除节点「旧地址」及 2 个子节点');
    expect(md).toContain('涉及 2 个节点');
  });

  it('整树级记录（node_id 为空）归到「整棵信息树」并排在最后', () => {
    const md = buildHistoryMarkdown(root, [
      record({ id: 'h1', node_id: null, node_title: '', action: 'import', detail: '导入信息树：新增 12 个节点' }),
    ], NODES);

    expect(md).toContain('## 整棵信息树');
    expect(md).toContain('导入信息树：新增 12 个节点');
  });

  it('识别不到操作人回退成「未知用户」；没有 detail 时按动作与节点标题兜底', () => {
    const md = buildHistoryMarkdown(root, [
      record({ id: 'h1', action: 'create', detail: null }),
    ], NODES);

    expect(md).toContain('未知用户 · 新增：新增节点「基础信息」');
  });

  it('动态文本里的 Markdown 控制字符被转义，值不会被解析成斜体/代码', () => {
    const md = buildHistoryMarkdown(root, [
      record({ id: 'h1', operator_name: '张三', detail: '把内容从「空」改为「2*3_4`5」' }),
    ], NODES);

    expect(md).toContain('2\\*3\\_4\\`5');
    expect(md).not.toContain('2*3_4`5');
  });

  it('记录数摸到上限时在表头说明「更早的没展示」', () => {
    const records = [record({ id: 'h1' }), record({ id: 'h2' })];
    expect(buildHistoryMarkdown(root, records, NODES, { limit: 2 })).toContain('已达显示上限（最近 2 条）');
    expect(buildHistoryMarkdown(root, records, NODES, { limit: 10 })).not.toContain('已达显示上限');
  });

  it('没有记录时给一句话，不吐半截文档', () => {
    const md = buildHistoryMarkdown(root, [], NODES);
    expect(md).toContain('# 基础信息 · 修改记录');
    expect(md).toContain('暂无编辑记录');
  });

  it('动作中文名覆盖结构类操作（后端会写 node_create 这类值，不能原样露出）', () => {
    // 与值类同词：列表里的兜底文案是「{动作}节点「X」」，只有两字动词接得上
    expect(historyActionName('node_create')).toBe('新增');
    expect(historyActionName('node_rename')).toBe('修改');
    expect(historyActionName('node_move')).toBe('移动');
    expect(historyActionName('unknown_action')).toBe('unknown_action');
  });
});
