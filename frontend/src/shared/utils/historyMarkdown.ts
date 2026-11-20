// 信息树「修改记录」的 Markdown 文档（纯函数，便于单测）。
//
// 一级标签（根节点）的「历史」看的是**整棵子树**：把这一级下所有节点（含自己）的变动，
// 按节点分组、组内最新在前，拼成一份 md，由 react-markdown 渲染（真解析，故动态文本要转义）。
// 分组顺序 = 树的先序（父在前、同级按 sort_order），与编辑页里看到的行顺序一致；
// 记录里的节点已经不在树里（被删掉了）时单列一组，用写入时的 node_name 快照当标题——
// 历史表本来就是靠这份快照显示「当时这个节点叫什么」的。
//
// 后端 records 已按 changed_at desc / id desc 排好，这里**不再重排**：
// 组内顺序就是接口给的顺序（同秒内的先后由时间有序的 id 兜底，前端重排反而会打乱）。
import type { ApiInfoNodeChange } from '@/api/infoNodes';

/** 文档里用到的节点信息（ProjectInfoNode 的子集，单测直接造对象即可） */
export interface HistoryDocNode {
  id: string;
  parent_id: string | null;
  title: string;
  sort_order: number;
}

/** 操作类型的中文名（与后端 info_node_change_service 的 ACTION_* 一一对应）。
 *  结构类（node_*）与值类用同一个词：记录行里是「动作：具体变动」，变动文案本身
 *  已写明是「增补节点」还是「填写内容」；而列表里的兜底文案是「{动作}节点「X」」，
 *  只有「新增 / 修改 / 移动 / 删除」这类两字动词能接得上（早前后端写 node_create
 *  这儿没有对应项，界面上直接露出过英文 action）。 */
export const HISTORY_ACTION_NAMES: Record<string, string> = {
  create: '新增',
  node_create: '新增',
  update: '修改',
  node_rename: '修改',
  delete: '删除',
  move: '移动',
  node_move: '移动',
  import: '导入',
  sync: '模板同步',
};

export function historyActionName(action: string): string {
  return HISTORY_ACTION_NAMES[action] ?? action;
}

/** 树的层数上限（后端 PROJECT_INFO_MAX_DEPTH 同值）：防脏数据成环时路径拼不出来 */
const MAX_PATH_DEPTH = 8;

/** 动态文本（节点名 / 值 / 人名）里的 Markdown 控制字符转义：
 *  值里带 * _ ` [ ] 会让整行变成斜体 / 代码 / 链接，交付内容里这些都是常见字符 */
function esc(text: string): string {
  return String(text ?? '').replace(/[\\`*_[\]<>]/g, (char) => `\\${char}`);
}

/** 「基础信息 / 车辆 / 车型1」：从根一路拼到该节点；上级查不到就停在那里 */
function pathOf(nodeId: string, byId: Map<string, HistoryDocNode>, rootId: string): string {
  const parts: string[] = [];
  let current: string | null = nodeId;
  const seen = new Set<string>();
  while (current && parts.length < MAX_PATH_DEPTH && !seen.has(current)) {
    seen.add(current);
    const node: HistoryDocNode | undefined = byId.get(current);
    if (!node) break;
    parts.unshift(node.title);
    if (current === rootId) break;
    current = node.parent_id;
  }
  return parts.join(' / ');
}

/** 子树节点 id（含根），按先序 —— 与后端 list_for_subtree 的分组口径一致 */
function subtreeIds(root: HistoryDocNode, nodes: HistoryDocNode[]): string[] {
  const children = new Map<string | null, string[]>();
  nodes.forEach((node) => {
    const siblings = children.get(node.parent_id) ?? [];
    siblings.push(node.id);
    children.set(node.parent_id, siblings);
  });
  const ordered: string[] = [];
  const walk = (nodeId: string) => {
    ordered.push(nodeId);
    (children.get(nodeId) ?? []).forEach(walk);
  };
  walk(root.id);
  return ordered;
}

/**
 * 记录 + 树 → Markdown 文档。
 *
 * - root 是本次查看的节点（一级标签）：分组顺序以它开头，标题用它做文档标题；
 * - nodes 传当前项目的全部节点（扁平），用来拼「上级 / 本级」的路径标题；
 * - limit 传入接口用的条数上限：记录数摸到上限时在表头写明「已达显示上限」，
 *   不假装这就是全部。
 */
export function buildHistoryMarkdown(
  root: HistoryDocNode,
  records: ApiInfoNodeChange[],
  nodes: HistoryDocNode[],
  options: { limit?: number } = {},
): string {
  const title = `# ${esc(root.title)} · 修改记录`;
  if (!records.length) return `${title}\n\n暂无编辑记录\n`;

  const byId = new Map(nodes.map((node) => [node.id, node]));
  // 子树先序 → 分组排序用的序号；不在序里的（删掉的节点、整树级记录）排到最后
  const rank = new Map(subtreeIds(root, nodes).map((nodeId, index) => [nodeId, index]));

  const groups = new Map<string, { heading: string; lines: string[] }>();
  const outsideSubtree: string[] = [];
  records.forEach((record) => {
    const key = record.node_id ?? '';
    let group = groups.get(key);
    if (!group) {
      if (rank.has(key)) {
        group = { heading: pathOf(key, byId, root.id), lines: [] };
      } else if (key) {
        // 节点已不在树里：用记录里写入时的名称快照，并标明是被删掉的节点
        group = { heading: `已删除 · ${record.node_title || '未知节点'}`, lines: [] };
        outsideSubtree.push(key);
      } else {
        group = { heading: '整棵信息树', lines: [] };
        outsideSubtree.push(key);
      }
      groups.set(key, group);
    }
    const who = record.operator_name || record.operator || '未知用户';
    const action = historyActionName(record.action);
    const what = record.detail || `${action}节点「${record.node_title}」`;
    group.lines.push(`- **${esc(record.created_at)}** ${esc(who)} · ${esc(action)}：${esc(what)}`);
  });

  const times = records.map((record) => record.created_at).filter(Boolean).sort();
  const range = times.length ? `${times[0]} ~ ${times[times.length - 1]}` : '';
  const summary = [
    `共 ${records.length} 条记录`,
    `涉及 ${groups.size} 个节点`,
    range,
    options.limit && records.length >= options.limit ? `已达显示上限（最近 ${options.limit} 条）` : '',
  ].filter(Boolean).join(' · ');

  const keys = [...groups.keys()].filter((key) => rank.has(key))
    .sort((a, b) => (rank.get(a) ?? 0) - (rank.get(b) ?? 0))
    .concat(outsideSubtree);
  const body = keys
    .map((key) => `## ${esc(groups.get(key)!.heading)}\n\n${groups.get(key)!.lines.join('\n')}`)
    .join('\n\n');

  return `${title}\n\n${summary}\n\n${body}\n`;
}
