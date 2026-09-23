// 项目信息树（项目详细信息）——前端数据层。
//
// 后端接口（backend/app/modules/admin/api/info_nodes.py）在本次改造后分成两类写操作：
// - **结构**（增删改节点/位置/类型/导入/模板）＝管理员，服务端有 get_current_admin_user 闸门；
//   普通用户要记表外信息走「增补信息」：createCustomInfoNode，任何节点下都能加（层数 ≤ 4）。
// - **值**（填内容）＝任何登录用户，走 setInfoNodeValue，只能写已存在节点的值。
//   普通用户的编辑权限就到此为止——他改不了节点名/类型/位置，也加不了节点。
// - 值由服务端按值类型编码后下发（下拉/布尔 {selected, options}、附件 {name, resource_id, size}），
//   本文件的解码层只做归一化，不再 JSON 解析字符串。
// - 仍存本机的只剩个人界面偏好（标签筛选、折叠状态、卡片折叠、历史已读水位），不属于共享数据。

import {
  createCustomInfoNodeApi,
  createInfoNodeApi,
  deleteInfoNodeApi,
  fetchInfoNodeChangeSummaryApi,
  fetchInfoNodeChangesApi,
  fetchInfoNodeMarksApi,
  fetchInfoTree,
  fetchProjectActivityApi,
  importInfoTreeApi,
  moveInfoNodeApi,
  setInfoNodeValueApi,
  toggleInfoNodeMarkApi,
  updateInfoNodeApi,
  type ApiInfoNode,
  type ApiInfoNodeChange,
  type ApiInfoNodeUpdate,
  type ApiInfoNodeValue,
  type ApiInfoTreeImportNode,
  type ApiProjectActivityItem,
} from '@/api/infoNodes';

export type ProjectInfoContentType = 'text' | 'select' | 'file' | 'image';

/** 下拉选择节点的内容值（选项来自字段定义，服务端合并在 value 里下发） */
export interface ProjectInfoSelectValue {
  selected: string;
  options: string[];
}

/** 文件/图片节点的内容值（resource_id 指向资源管理服务的真实文件） */
export interface ProjectInfoFileValue {
  name: string;
  resource_id?: number;
  size?: number;
}

export interface ProjectInfoNode {
  id: string;
  /** 全局字段为 null：该节点是全体项目共用的字段定义，值才是本项目的 */
  project_id: string | null;
  parent_id: string | null;
  title: string;
  content_type: ProjectInfoContentType;
  /** 结构化值：text → string；select → {selected, options}；file/image → {name, resource_id, size} */
  value: unknown;
  sort_order: number;
  created_at: string;
  /** 后端最后更新时间（字符串时间戳），本地乐观更新时为最近一次成功写入的值 */
  updated_at?: string;
  /** 字段标识（服务端维护，供程序引用，不在界面上展示） */
  node_key?: string;
  /** 内部细分类型 text/number/boolean/date/select/multi_select/person/attachment/json */
  value_type?: string;
  /** 该节点是否允许普通用户「增补信息」 */
  allow_custom?: boolean;
  /** true = 本项目增补的节点（可改名/改类型/删除/拖动），false = 全局字段定义（仅模板可改） */
  is_custom?: boolean;
  /** 下拉选项（字段定义，只读下发；管理员改） */
  options?: string[];
  /** 标题备选项（字段定义，只读下发；管理员改） */
  titleOptions?: string[];
}

/** 信息树最大层级（与设计稿一致：第 4 层不可再新增/下挂） */
export const PROJECT_INFO_MAX_DEPTH = 4;

// —— 预设信息树模板（已下沉到后端） ——
// 唯一来源：project_info_node 里 project_id 为空的行（全局字段定义）。
// 管理员在编辑页的「详情模板」入口维护；普通项目读树时自动带上，前端不再维护副本，
// 也没有「按模板补种本项目」这一步（旧接口 POST /import-template 已移除）。

const selectedKey = (code: string) => `project-info-tree:selected:${code}`;
const collapsedKey = (code: string) => `project-info-tree:collapsed:${code}`;
const cardCollapsedKey = (code: string) => `project-info-tree:card-collapsed:${code}`;
const historySeenKey = (code: string, user: string) => `project-info-tree:history-seen:${code}:${user}`;

function readJson<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

function writeJson(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // localStorage 不可用（隐私模式等）时静默降级为仅内存态，不阻断交互
  }
}

// —— 后端行 <-> 页面节点 ——

/**
 * 后端值 → 页面结构化值。
 * 服务端已按下发口径编码好了：下拉是 {selected, options}，附件是对象，text 是字符串。
 * 这里只做「形状归一化」——选项过滤掉非字符串、附件缺 name 时留空，
 * 免得调用方（展示卡、完整度统计）到处判空。
 */
function decodeInfoValue(contentType: string, raw: unknown): unknown {
  if (contentType === 'select') {
    const data = (raw && typeof raw === 'object' && !Array.isArray(raw) ? raw : {}) as Partial<ProjectInfoSelectValue>;
    return {
      selected: typeof data.selected === 'string' ? data.selected : '',
      options: Array.isArray(data.options) ? data.options.filter((item): item is string => typeof item === 'string') : [],
    };
  }
  if (contentType === 'file' || contentType === 'image') {
    return raw && typeof raw === 'object' && !Array.isArray(raw) ? (raw as ProjectInfoFileValue) : {};
  }
  if (raw === null || raw === undefined) return '';
  // 数字/布尔等非字符串标量统一成字符串，页面按文本渲染
  return typeof raw === 'string' ? raw : String(raw);
}

/**
 * 页面结构化值 → 提交给后端的值。
 * 服务端按 value_type 解码，所以这里**不再 JSON.stringify 对象**：
 * 直接把结构化值送过去，字符串类节点的值原样丢弃首尾空白。
 */
export function encodeInfoValue(value: unknown): ApiInfoNodeValue {
  if (value === null || value === undefined) return null;
  if (typeof value === 'string') {
    const trimmed = value.trim();
    return trimmed ? trimmed : null;
  }
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return value as ApiInfoNodeValue;
}

function stringList(raw: unknown): string[] {
  return Array.isArray(raw) ? raw.filter((item): item is string => typeof item === 'string') : [];
}

/** 节点定义里的选项清单：服务端下发在顶层（options / titleOptions）。 */
function decodeNodeOptions(raw: ApiInfoNode, key: 'options' | 'titleOptions'): string[] {
  return stringList(raw[key]);
}

function decodeInfoNode(raw: ApiInfoNode, parentId: string | null): ProjectInfoNode {
  const contentType = (raw.content_type || 'text') as ProjectInfoContentType;
  return {
    id: raw.id,
    project_id: raw.project_id ?? null,
    parent_id: parentId,
    title: raw.title,
    content_type: contentType,
    value: decodeInfoValue(contentType, raw.value),
    sort_order: raw.sort_order,
    created_at: raw.created_at,
    updated_at: raw.updated_at,
    node_key: raw.node_key,
    value_type: raw.value_type,
    allow_custom: !!raw.allow_custom,
    is_custom: !!raw.is_custom,
    options: decodeNodeOptions(raw, 'options'),
    titleOptions: decodeNodeOptions(raw, 'titleOptions'),
  };
}

/** 接口返回的递归树 → 扁平节点（父 id 以树的层级为准，避免后端脏 parent_id 影响渲染） */
export function flattenInfoTree(roots: ApiInfoNode[]): ProjectInfoNode[] {
  const flat: ProjectInfoNode[] = [];
  const walk = (items: ApiInfoNode[], parentId: string | null) => {
    items.forEach((item) => {
      flat.push(decodeInfoNode(item, parentId));
      if (item.children?.length) walk(item.children, item.id);
    });
  };
  walk(roots, null);
  return flat;
}

// —— 节点读写（真实后端 /api/admin/info-nodes/*，逐节点 CRUD） ——

/** 读取某项目的全部信息节点（扁平，按 sort_order 升序）。
 *  返回的是「全局字段定义 ∪ 本项目增补节点」的并集，值取自本项目。 */
export async function loadInfoNodes(projectId: string): Promise<ProjectInfoNode[]> {
  const tree = await fetchInfoTree(projectId);
  return flattenInfoTree(tree).sort((a, b) => a.sort_order - b.sort_order);
}

/**
 * 增补一个节点到本项目（两条路都只动本项目，区别只在门槛与层级）。
 * - `canEditTree=true`（本项目成员或 admin）走 /projects/{id}：任意层级，
 *   含最外层根节点（编辑页的「新标签」）；这是改树结构的接口。
 * - `canEditTree=false` 走 /custom-nodes（增补信息）：必须指定 parentId、
 *   层数 ≤ 4，任何登录用户都能加——普通用户记表外信息的唯一途径。
 */
export async function createInfoNode(
  projectId: string,
  parentId: string | null,
  sortOrder: number,
  title = '未命名节点',
  canEditTree = false,
  contentType: ProjectInfoContentType = 'text',
): Promise<ProjectInfoNode> {
  const payload = {
    parent_id: parentId,
    title,
    content_type: contentType,
    sort_order: sortOrder,
  };
  const raw = canEditTree
    ? await createInfoNodeApi(projectId, payload)
    : await createCustomInfoNodeApi(projectId, payload);
  return decodeInfoNode(raw, parentId);
}

/**
 * 写节点值（任何登录用户）。值 + 历史在后端同一事务里落库。
 * 这是普通用户唯一能改数据的地方——改不了结构。
 */
export async function setInfoNodeValue(
  node: ProjectInfoNode,
  value: unknown,
  projectId?: string,
): Promise<ProjectInfoNode> {
  const ownerProject = projectId ?? node.project_id ?? '';
  const raw = await setInfoNodeValueApi(node.id, ownerProject, encodeInfoValue(value));
  return decodeInfoNode(raw, node.parent_id);
}

/**
 * 更新节点**定义**（仅管理员，且只对「本项目增补的节点」生效）。
 * 全局字段的定义请改详情模板（saveInfoTemplate），否则后端返回 403。
 * 值不在这里改——走 setInfoNodeValue；options/titleOptions 是定义（落节点 config），在这里改。
 */
export async function updateInfoNode(
  node: ProjectInfoNode,
  updates: {
    title?: string;
    content_type?: ProjectInfoContentType;
    sort_order?: number;
    /** 下拉选项（字段定义，落节点 config） */
    options?: string[];
    /** 标题备选项（字段定义，落节点 config） */
    titleOptions?: string[];
  },
): Promise<ProjectInfoNode> {
  const payload: ApiInfoNodeUpdate = {};
  if (updates.title !== undefined) payload.title = updates.title;
  if (updates.content_type !== undefined) payload.content_type = updates.content_type;
  if (updates.sort_order !== undefined) payload.sort_order = updates.sort_order;
  if (updates.options !== undefined) payload.options = updates.options;
  if (updates.titleOptions !== undefined) payload.titleOptions = updates.titleOptions;
  const raw = await updateInfoNodeApi(node.id, payload);
  return decodeInfoNode(raw, node.parent_id);
}

/** 移动节点（仅管理员；换父 + 同级排序） */
export async function moveInfoNode(
  node: ProjectInfoNode,
  parentId: string | null,
  sortOrder: number,
): Promise<ProjectInfoNode> {
  const raw = await moveInfoNodeApi(node.id, parentId, sortOrder);
  return decodeInfoNode(raw, parentId);
}

/** 删除节点及其整棵子树（仅管理员；全局字段定义删不掉，只能改模板） */
export async function deleteInfoNode(nodeId: string): Promise<void> {
  await deleteInfoNodeApi(nodeId);
}

/** 批量导入信息树（仅管理员；纯增补，只增不改不删）；返回新增的节点数 */
export async function importInfoTree(projectId: string, input: unknown): Promise<number> {
  return importInfoTreeApi(projectId, normalizeImportNodes(input));
}

// —— 编辑历史（节点操作记录）：后端全量落库，「已读水位」存本机（每个人各自的未读状态） ——

/** 一级标签的「修改记录」要覆盖整棵子树，条数上限比单节点高（后端上限 500） */
export const SUBTREE_HISTORY_LIMIT = 200;

/** 某节点的编辑历史：自身操作 + 其直接子节点的删除记录（最新在前）。
 *  includeDescendants=true（一级标签）时连整棵子树的记录一起取，见 historyMarkdown。 */
export async function loadInfoNodeChanges(
  projectId: string,
  nodeId: string,
  options: { includeDescendants?: boolean; limit?: number } = {},
): Promise<ApiInfoNodeChange[]> {
  return fetchInfoNodeChangesApi(projectId, nodeId, options);
}

/** 各节点最新记录的 id {节点id: 记录id}（删除记录计入其上级节点） */
export async function loadHistoryLatest(projectId: string): Promise<Record<string, string>> {
  return fetchInfoNodeChangeSummaryApi(projectId);
}

/** 已读水位（该节点看过的最后一条记录 id）按「项目 + 登录用户」存本机：
 *  每个没点开过历史的用户，自己看到小红点 */
export function loadHistorySeen(projectCode: string, username = ''): Record<string, string> {
  return readJson<Record<string, string>>(historySeenKey(projectCode, username), {});
}

export function saveHistorySeen(projectCode: string, seen: Record<string, string>, username = '') {
  writeJson(historySeenKey(projectCode, username), seen);
}

/**
 * 有「新变动」的节点（历史按钮上的小红点）：
 * 该节点最新记录 id 与本机已读水位不一致（或从没点开过）即未读；没有记录的节点不出红点。
 * 只有点开过该节点的历史才会推进水位——包括自己刚保存的改动。
 * 记录 id 是后端时间有序的 UUIDv7：只比相等，同秒内的新记录也不会漏（时间戳只到秒）。
 */
export function unseenHistoryNodes(
  latest: Record<string, string>,
  seen: Record<string, string>,
): Set<string> {
  const result = new Set<string>();
  Object.entries(latest ?? {}).forEach(([nodeId, latestId]) => {
    if (!latestId) return;
    if (seen?.[nodeId] !== latestId) result.add(nodeId);
  });
  return result;
}

/**
 * 未读变动往上归到一级标签：返回「该标签下有未读变动」的根节点 id 集合。
 * 展示页标签池的红点用它——与编辑页行内红点同一套水位（unseenHistoryNodes）：
 * 节点本身或它所属的一级标签下有没看过的记录就带点；点开该节点「历史」后不再贡献，
 * 该一级标签下没有其它未读时红点随之消失。节点已从树里删除（只剩记录）不往上归。
 */
export function unseenHistoryRoots(
  nodes: Array<Pick<ProjectInfoNode, 'id' | 'parent_id'>>,
  unseen: Set<string>,
): Set<string> {
  const parentOf = new Map(nodes.map((node) => [node.id, node.parent_id]));
  const roots = new Set<string>();
  unseen.forEach((nodeId) => {
    if (!parentOf.has(nodeId)) return;
    let current = nodeId;
    for (let depth = 0; depth < PROJECT_INFO_MAX_DEPTH; depth += 1) {
      const parentId = parentOf.get(current);
      if (!parentId) break;
      current = parentId;
    }
    roots.add(current);
  });
  return roots;
}

/**
 * 未读节点 + 它的**每一层上级**（编辑页行内红点用它）：
 * 某个节点有更新时，从它自己一直到一级标签都出小红点；
 * 点开其中任一处的历史（openHistory 按子树推进水位）后，这一串点一起消失。
 * 节点已从树里删除（只剩记录）不出点、也不往上归。
 */
export function unseenHistoryChain(
  nodes: Array<Pick<ProjectInfoNode, 'id' | 'parent_id'>>,
  unseen: Set<string>,
): Set<string> {
  const parentOf = new Map(nodes.map((node) => [node.id, node.parent_id]));
  const result = new Set<string>();
  unseen.forEach((nodeId) => {
    if (!parentOf.has(nodeId)) return;
    result.add(nodeId);
    let current: string | null = parentOf.get(nodeId) ?? null;
    for (let depth = 0; current && depth < PROJECT_INFO_MAX_DEPTH; depth += 1) {
      result.add(current);
      current = parentOf.get(current) ?? null;
    }
  });
  return result;
}

/** 某节点及其全部子孙的 id（点开一处历史 = 这棵子树都算看过；只按本地树的 parent_id 关系展开） */
export function subtreeNodeIds(
  nodes: Array<Pick<ProjectInfoNode, 'id' | 'parent_id'>>,
  rootId: string,
): string[] {
  const children = new Map<string | null, string[]>();
  nodes.forEach((node) => {
    const list = children.get(node.parent_id) ?? [];
    list.push(node.id);
    children.set(node.parent_id, list);
  });
  const ids: string[] = [];
  const walk = (nodeId: string) => {
    ids.push(nodeId);
    (children.get(nodeId) ?? []).forEach(walk);
  };
  walk(rootId);
  return ids;
}

// —— 关注（星标）与项目动态：关注按登录人隔离（后端落库，服务端按 token 过滤），动态按本人关注节点聚合 ——

/** 某项目被关注的节点 id（「项目信息管理」卡的星标状态） */
export async function loadInfoNodeMarks(projectId: string): Promise<string[]> {
  return fetchInfoNodeMarksApi(projectId);
}

/** 切换节点关注状态，返回切换后是否被关注。
 *  projectId 可选（展示卡只传 nodeId，后端按已有标注切换）。 */
export async function toggleInfoNodeMark(nodeId: string, projectId?: string): Promise<boolean> {
  return toggleInfoNodeMarkApi(nodeId, projectId);
}

/** 项目动态里的一条：某被关注节点的最新变动（只展示 detail，不带时间与人员） */
export type ProjectActivityItem = ApiProjectActivityItem;

/** 项目动态：每个被关注节点只取最新一条变动（后端聚合，最新在前） */
export async function loadProjectActivity(projectId: string): Promise<ProjectActivityItem[]> {
  return fetchProjectActivityApi(projectId);
}

/** 归一化导入内容：接受节点数组、{nodes:[…]}、{info_nodes:[…]}，
 *  或「标题 → 内容」紧凑映射（backend/app/config/project_templates/tmp.json 的写法）。
 *  统一补序号与缺省字段，并把 options 清单转成 select 节点。
 *  导入是**纯增补**：后端按 node_key 对齐，导入源里多余的 id 不再有意义（新节点 id 由服务端生成）。 */
export function normalizeImportNodes(input: unknown): ApiInfoTreeImportNode[] {
  if (Array.isArray(input)) return normalizeImportLevel(input as Record<string, unknown>[]);
  const container = input as { nodes?: unknown; info_nodes?: unknown } | null;
  if (Array.isArray(container?.nodes)) return normalizeImportLevel(container.nodes as Record<string, unknown>[]);
  const infoNodes = container?.info_nodes;
  if (Array.isArray(infoNodes)) return normalizeImportLevel(infoNodes as Record<string, unknown>[]);
  if (infoNodes && typeof infoNodes === 'object') {
    return normalizeImportLevel(mapFormToLevel(infoNodes as Record<string, unknown>));
  }
  throw new Error('导入内容需要是信息树数组（或含 nodes / info_nodes 字段的对象、标题:内容 映射）');
}

/** 紧凑映射 → 节点数组：""/文字 → 文字节点；[选项…] → 下拉节点；{…} → 子节点 */
function mapFormToLevel(map: Record<string, unknown>): Record<string, unknown>[] {
  return Object.entries(map).map(([title, value], index) => {
    if (Array.isArray(value)) {
      return { title, sort_order: (index + 1) * 10, content_type: 'select', options: value };
    }
    if (value && typeof value === 'object') {
      return { title, sort_order: (index + 1) * 10, children: mapFormToLevel(value as Record<string, unknown>) };
    }
    return { title, sort_order: (index + 1) * 10, value: typeof value === 'string' ? value : '' };
  });
}

function normalizeImportLevel(items: Record<string, unknown>[]): ApiInfoTreeImportNode[] {
  return items.map((item, index) => {
    const value = item.value as { selected?: unknown; options?: unknown } | null | undefined;
    const options = Array.isArray(item.options)
      ? (item.options as unknown[]).filter((option): option is string => typeof option === 'string')
      : Array.isArray(value?.options)
        ? value.options.filter((option): option is string => typeof option === 'string')
        : [];
    const contentType =
      typeof item.content_type === 'string' ? item.content_type : options.length ? 'select' : 'text';
    const rawValue = item.value ?? null;
    const node: ApiInfoTreeImportNode = {
      title: typeof item.title === 'string' && item.title ? item.title : '未命名节点',
      content_type: contentType,
      // 下拉节点的选项属于字段定义：值里带上供后端写进节点 config
      value: options.length
        ? { selected: typeof value?.selected === 'string' ? value.selected : '', options }
        : normalizeImportValue(rawValue),
      sort_order: typeof item.sort_order === 'number' ? item.sort_order : (index + 1) * 10,
    };
    const children = Array.isArray(item.children) ? (item.children as Record<string, unknown>[]) : [];
    if (children.length) node.children = normalizeImportLevel(children);
    return node;
  });
}

/** 导入值：字符串与结构化值都按接口口径原样提交（后端按节点类型解码） */
function normalizeImportValue(value: unknown): ApiInfoNodeValue {
  if (value === null || value === undefined) return null;
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return value as ApiInfoNodeValue;
}

/** 乐观更新：本地替换单个节点的字段（不落盘，调用方随后发 API，失败时回滚） */
export function patchInfoNode(
  nodes: ProjectInfoNode[],
  id: string,
  updates: Partial<Pick<ProjectInfoNode,
    'title' | 'content_type' | 'value' | 'parent_id' | 'sort_order' | 'options' | 'titleOptions'>>,
): ProjectInfoNode[] {
  return nodes.map((node) => (node.id === id ? { ...node, ...updates } : node));
}

/**
 * 一键清空（乐观更新）：把树恢复成模板的样子——本项目增补的节点（导入 / 同步 /
 * 「增补信息」加进来的，is_custom）连子孙一起删掉，剩下的全局字段值清空。
 * 与后端 reset-to-template 同一口径；全局节点与它们的字段定义原样留着。
 * 值：下拉清掉选中项、**保留选项**（选项属于字段定义，清了就没得选了）；
 * 附件置 null（文件本体在资源库里不动，只是不再挂在这个节点上）；其余置空串。
 */
export function resetInfoTreeToTemplate(nodes: ProjectInfoNode[]): ProjectInfoNode[] {
  return nodes
    .filter((node) => !node.is_custom)
    .map((node) => (hasFieldValue(node) ? { ...node, value: emptyValueOf(node) } : node));
}

function emptyValueOf(node: ProjectInfoNode): ProjectInfoSelectValue | ProjectInfoFileValue | string | null {
  if (node.content_type === 'select') {
    const current = node.value as ProjectInfoSelectValue | null;
    return { selected: '', options: current?.options ?? [] };
  }
  if (node.content_type === 'file' || node.content_type === 'image') return null;
  return '';
}

/** 删除节点及其全部后代节点 */
export function removeInfoNode(nodes: ProjectInfoNode[], id: string): ProjectInfoNode[] {
  const doomed = new Set<string>([id]);
  let grew = true;
  while (grew) {
    grew = false;
    nodes.forEach((node) => {
      if (node.parent_id && doomed.has(node.parent_id) && !doomed.has(node.id)) {
        doomed.add(node.id);
        grew = true;
      }
    });
  }
  return nodes.filter((node) => !doomed.has(node.id));
}

// —— 标签筛选 / 折叠偏好（个人偏好，存本机；与设计稿一致） ——

export function loadSelectedTags(projectCode: string): Set<string> {
  return new Set(readJson<string[]>(selectedKey(projectCode), []));
}

export function saveSelectedTags(projectCode: string, ids: Set<string>) {
  writeJson(selectedKey(projectCode), [...ids]);
}

export function loadCollapsedIds(projectCode: string): Set<string> {
  return new Set(readJson<string[]>(collapsedKey(projectCode), []));
}

export function saveCollapsedIds(projectCode: string, ids: Set<string>) {
  writeJson(collapsedKey(projectCode), [...ids]);
}

export function loadCardCollapsed(projectCode: string): boolean {
  return localStorage.getItem(cardCollapsedKey(projectCode)) === '1';
}

export function saveCardCollapsed(projectCode: string, collapsed: boolean) {
  try {
    localStorage.setItem(cardCollapsedKey(projectCode), collapsed ? '1' : '0');
  } catch {
    /* ignore */
  }
}

/** 附件大小展示（B / KB / MB） */
export function formatFileSize(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

// —— 区域细分字段联动（项目区域/地点 下的三个候选字段按所选区域显隐） ——

/** 「大陆(China Mainland)」选项：选中它才显示省份/地区 */
export const REGION_MAINLAND = '大陆(China Mainland)';
/** 大陆下的细分字段 */
const MAINLAND_DETAIL_TITLES = ['省份', '地区'];
/** 其它区域下的细分字段 */
const OVERSEAS_DETAIL_TITLE = '具体国家';

/** 同级的「区域」下拉：按选项里是否含大陆判定，避免改标题后联动失效 */
function regionDriver(siblings: ProjectInfoNode[]): ProjectInfoNode | undefined {
  return siblings.find((item) => {
    if (item.content_type !== 'select') return false;
    const options = (item.value as Partial<ProjectInfoSelectValue> | null)?.options;
    return Array.isArray(options) && options.includes(REGION_MAINLAND);
  });
}

/**
 * 区域细分字段当前是否显示（节点本身仍在数据里，只是不渲染——切回大陆时原值还在）：
 * - 未选择区域 → 省份/地区/具体国家都不显示；
 * - 选中大陆 → 只显示省份/地区；
 * - 选中其它区域 → 只显示具体国家；
 * - 与区域无关的节点（含用户自建字段）一律显示。
 */
export function isInfoNodeVisible(node: ProjectInfoNode, siblings: ProjectInfoNode[]): boolean {
  const driver = regionDriver(siblings);
  if (!driver || driver.id === node.id) return true;
  const selected = (driver.value as Partial<ProjectInfoSelectValue> | null)?.selected ?? '';
  if (MAINLAND_DETAIL_TITLES.includes(node.title)) return selected === REGION_MAINLAND;
  if (node.title === OVERSEAS_DETAIL_TITLE) return !!selected && selected !== REGION_MAINLAND;
  return true;
}

/** 过滤掉当前不该显示的节点（完整度统计等按「看得见的字段」算） */
export function visibleInfoNodes(nodes: ProjectInfoNode[]): ProjectInfoNode[] {
  const byParent = new Map<string | null, ProjectInfoNode[]>();
  nodes.forEach((node) => {
    const list = byParent.get(node.parent_id) ?? [];
    list.push(node);
    byParent.set(node.parent_id, list);
  });
  return nodes.filter((node) => isInfoNodeVisible(node, byParent.get(node.parent_id) ?? []));
}

// —— 信息完整度（统计每个一级标签下「可填节点」的填写情况） ——
// 可填 = 末级字段 + 自带值类型的非末级节点（下拉车型 + 数量的组合）；纯文本分组不算可填。

export interface TagCompleteness {
  total: number;
  empty: number;
  incomplete: boolean;
}

function isEmptyNodeValue(node: ProjectInfoNode): boolean {
  if (node.content_type === 'select') {
    return !(node.value as ProjectInfoSelectValue | null)?.selected;
  }
  if (node.content_type === 'file' || node.content_type === 'image') {
    return !(node.value as ProjectInfoFileValue | null)?.name;
  }
  return !(typeof node.value === 'string' && node.value.trim());
}

/** 字段是否有值：下拉看选中项，附件看文件名，其余按去空白后的文本判断 */
export function hasFieldValue(node: ProjectInfoNode): boolean {
  return !isEmptyNodeValue(node);
}

/**
 * 节点自己带不带值。末级节点一律带（它就是让人填的字段）；非末级节点只有下拉/附件
 * 这类才带——纯文本的非末级节点是分组，它没有自己的值。
 * 「车型1」是典型的两者兼具：下拉选中型号，下面还挂着「数量」子节点。
 */
function nodeFillsValue(node: ProjectInfoNode, hasChildren: boolean): boolean {
  return !hasChildren || node.content_type !== 'text';
}

/**
 * 每个节点名下「有值的字段」个数 {节点id: 条数}——**含节点自己的值**。
 * 展示页只用它来裁剪：条数为 0 的分支整棵不渲染(空分组只剩标签名的空壳)，
 * 一级标签条数为 0 时提示「信息不足请补充」。一次遍历算出全部节点，避免逐节点重复递归。
 */
export function countInfoValues(nodes: ProjectInfoNode[]): Map<string, number> {
  const byParent = new Map<string | null, ProjectInfoNode[]>();
  nodes.forEach((node) => {
    const list = byParent.get(node.parent_id) ?? [];
    list.push(node);
    byParent.set(node.parent_id, list);
  });

  const counts = new Map<string, number>();
  const walk = (node: ProjectInfoNode): number => {
    const children = byParent.get(node.id) ?? [];
    // 自己的值也算一条：车型1 选好型号、数量还没填时，整条分支不该被判成「没值」而裁掉
    const self = nodeFillsValue(node, children.length > 0) && !isEmptyNodeValue(node) ? 1 : 0;
    const total = self + children.reduce((sum, child) => sum + walk(child), 0);
    counts.set(node.id, total);
    return total;
  };

  (byParent.get(null) ?? []).forEach(walk);
  return counts;
}

/** 只要一级标签下存在值为空的可填节点即视为信息不全（卡片标签上显示「!」角标） */
export function computeInfoCompleteness(nodes: ProjectInfoNode[]): Map<string, TagCompleteness> {
  const byParent = new Map<string | null, ProjectInfoNode[]>();
  nodes.forEach((node) => {
    const list = byParent.get(node.parent_id) ?? [];
    list.push(node);
    byParent.set(node.parent_id, list);
  });

  const result = new Map<string, TagCompleteness>();
  const walk = (node: ProjectInfoNode, acc: { total: number; empty: number }) => {
    const children = byParent.get(node.id) ?? [];
    if (nodeFillsValue(node, children.length > 0)) {
      acc.total += 1;
      if (isEmptyNodeValue(node)) acc.empty += 1;
    }
    children.forEach((child) => walk(child, acc));
  };

  (byParent.get(null) ?? []).forEach((root) => {
    const acc = { total: 0, empty: 0 };
    walk(root, acc);
    result.set(root.id, { total: acc.total, empty: acc.empty, incomplete: acc.total > 0 && acc.empty > 0 });
  });

  return result;
}
