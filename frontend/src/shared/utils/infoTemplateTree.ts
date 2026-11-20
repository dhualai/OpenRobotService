// 项目详情模板的本地树操作（纯函数，全部不可变更新）。
//
// 模板编辑页（pages/admin/ProjectInfoTemplate.tsx）在内存里编辑这棵树，
// 点「保存并同步」后整棵树提交给后端（后端校验并同步到所有项目）。
// 节点 id 是模板侧稳定 UUID：后端 project_info_node.template_node_id 以它为同步锚点，
// 所以新增节点也要在这里生成 id，保存后不再变化。
import type { ApiInfoTemplateNode } from '@/api/infoNodes';

/** 模板最大层级（与后端 MAX_TEMPLATE_DEPTH / 前端 PROJECT_INFO_MAX_DEPTH 一致） */
export const INFO_TEMPLATE_MAX_DEPTH = 4;

export const TEMPLATE_CONTENT_TYPES = ['text', 'select', 'file', 'image'] as const;
export type TemplateContentType = (typeof TEMPLATE_CONTENT_TYPES)[number];

export const TEMPLATE_CONTENT_TYPE_NAMES: Record<TemplateContentType, string> = {
  text: '文字输入',
  select: '下拉选择',
  file: '上传文件',
  image: '上传图片',
};

const genId = (): string => {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID();
  return `tpl_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
};

export function newTemplateNode(title = '新节点'): ApiInfoTemplateNode {
  // allow_custom 写 true：所有节点都允许各项目在其下增补信息（2026-09-18 取消开关，
  // 后端 normalize_template_nodes 也会一律归一到 true）
  return { id: genId(), title, content_type: 'text', allow_custom: true, children: [] };
}

export function countTemplateNodes(nodes: ApiInfoTemplateNode[]): number {
  return nodes.reduce((sum, node) => sum + 1 + countTemplateNodes(node.children ?? []), 0);
}

/** 节点子树深度（自身为 1） */
export function subtreeDepth(node: ApiInfoTemplateNode): number {
  const children = node.children ?? [];
  if (!children.length) return 1;
  return 1 + Math.max(...children.map(subtreeDepth));
}

function containsId(nodes: ApiInfoTemplateNode[], id: string): boolean {
  return nodes.some((node) => node.id === id || containsId(node.children ?? [], id));
}

export function findTemplateNode(
  nodes: ApiInfoTemplateNode[],
  id: string,
): { node: ApiInfoTemplateNode; parentId: string | null; index: number } | null {
  for (let index = 0; index < nodes.length; index += 1) {
    const node = nodes[index];
    if (node.id === id) return { node, parentId: null, index };
    const hit = findTemplateNode(node.children ?? [], id);
    if (hit) return { node: hit.node, parentId: hit.parentId ?? node.id, index: hit.index };
  }
  return null;
}

/** 更新节点字段（title / content_type / options）；返回新树 */
export function updateTemplateNode(
  nodes: ApiInfoTemplateNode[],
  id: string,
  patch: Partial<Pick<ApiInfoTemplateNode, 'title' | 'content_type' | 'options'>>,
): ApiInfoTemplateNode[] {
  return nodes.map((node) => {
    if (node.id === id) return { ...node, ...patch };
    const children = node.children ?? [];
    if (!containsId(children, id)) return node;
    return { ...node, children: updateTemplateNode(children, id, patch) };
  });
}

/** 删除节点及其子树；返回新树 */
export function removeTemplateNode(nodes: ApiInfoTemplateNode[], id: string): ApiInfoTemplateNode[] {
  const filtered = nodes.filter((node) => node.id !== id);
  if (filtered.length !== nodes.length) return filtered;
  return nodes.map((node) => {
    const children = node.children ?? [];
    if (!containsId(children, id)) return node;
    return { ...node, children: removeTemplateNode(children, id) };
  });
}

/** 在 parentId（null=根）下追加一个子节点；返回新树 */
export function appendTemplateNode(
  nodes: ApiInfoTemplateNode[],
  parentId: string | null,
  child: ApiInfoTemplateNode,
): ApiInfoTemplateNode[] {
  if (parentId === null) return [...nodes, child];
  return nodes.map((node) => {
    if (node.id === parentId) return { ...node, children: [...(node.children ?? []), child] };
    const children = node.children ?? [];
    if (!containsId(children, parentId)) return node;
    return { ...node, children: appendTemplateNode(children, parentId, child) };
  });
}

/** 同级上移/下移（delta=-1/+1）；到头不动 */
export function moveTemplateSibling(
  nodes: ApiInfoTemplateNode[],
  id: string,
  delta: -1 | 1,
): ApiInfoTemplateNode[] {
  const index = nodes.findIndex((node) => node.id === id);
  if (index >= 0) {
    const target = index + delta;
    if (target < 0 || target >= nodes.length) return nodes;
    const next = [...nodes];
    [next[index], next[target]] = [next[target], next[index]];
    return next;
  }
  return nodes.map((node) => {
    const children = node.children ?? [];
    if (!containsId(children, id)) return node;
    return { ...node, children: moveTemplateSibling(children, id, delta) };
  });
}

/**
 * 「降一级」会归入谁：上一个同级节点的 id；已是同级第一个（降不了）返回 null。
 * 页面用它把落点展开——否则节点降进一个收起的分支里，看上去像"点了没反应"。
 */
export function indentTargetId(nodes: ApiInfoTemplateNode[], id: string): string | null {
  const index = nodes.findIndex((node) => node.id === id);
  if (index >= 0) return index === 0 ? null : nodes[index - 1].id;
  for (const node of nodes) {
    const hit = indentTargetId(node.children ?? [], id);
    if (hit !== null) return hit;
    if (containsId(node.children ?? [], id)) return null; // 找到了，但它是那一层的第一个
  }
  return null;
}

/**
 * 降一级：成为上一个同级节点的最后一个子节点。
 * 已是同级第一个（没有上一个同级）时不动，返回原树。
 */
export function indentTemplateNode(nodes: ApiInfoTemplateNode[], id: string): ApiInfoTemplateNode[] {
  const index = nodes.findIndex((node) => node.id === id);
  if (index >= 0) {
    if (index === 0) return nodes;
    const moving = nodes[index];
    const target = nodes[index - 1];
    const next = [...nodes];
    next.splice(index, 1);
    next[index - 1] = { ...target, children: [...(target.children ?? []), moving] };
    return next;
  }
  return nodes.map((node) => {
    const children = node.children ?? [];
    if (!containsId(children, id)) return node;
    return { ...node, children: indentTemplateNode(children, id) };
  });
}

/**
 * 升一级：成为父节点的下一个同级兄弟（插到父节点之后）。
 * 已是根节点时不动，返回原树。
 */
export function outdentTemplateNode(nodes: ApiInfoTemplateNode[], id: string): ApiInfoTemplateNode[] {
  if (nodes.some((node) => node.id === id)) return nodes; // 根层无父可升
  for (let i = 0; i < nodes.length; i += 1) {
    const node = nodes[i];
    const children = node.children ?? [];
    const childIndex = children.findIndex((child) => child.id === id);
    if (childIndex >= 0) {
      const moving = children[childIndex];
      const next = [...nodes];
      next[i] = { ...node, children: children.filter((child) => child.id !== id) };
      next.splice(i + 1, 0, moving);
      return next;
    }
    if (containsId(children, id)) {
      const next = [...nodes];
      next[i] = { ...node, children: outdentTemplateNode(children, id) };
      return next;
    }
  }
  return nodes;
}

/** 节点在树中的层级（根为 1）；找不到返回 0 */
export function templateNodeDepth(nodes: ApiInfoTemplateNode[], id: string, depth = 1): number {
  for (const node of nodes) {
    if (node.id === id) return depth;
    const found = templateNodeDepth(node.children ?? [], id, depth + 1);
    if (found) return found;
  }
  return 0;
}
