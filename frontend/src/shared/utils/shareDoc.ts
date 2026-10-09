/**
 * 「问题共享文档」（工单完整问题文档）的正文生成与合并 —— 纯函数，无副作用。
 *
 * 文档分两段，由一条独占一行的 `---` 分隔（用户可见，与设计稿一致）：
 *
 *   ┌ 系统段 ────────────────────────────────┐
 *   │ # 问题共享文档                          │
 *   │ > 项目：xxx                            │
 *   │ ## 项目背景信息                         │
 *   │ ### 车端软件 / #### 软件版本 …          │  ← 按勾选的一级标签实时重算
 *   ├ ------------ 分隔线 ------------ ┤
 *   │ 提单人/接单人自己写的内容                │  ← 重算时原样保留，不会被覆盖
 *   └────────────────────────────────────────┘
 *
 * 全部对齐后端既有契约：节点数据来自 `loadInfoNodes`（GET /info-nodes/projects/{id}），
 * 文档正文仍走 task_spec_doc（PUT /tasks/{id}/spec-doc），后端不需要新接口。
 */
import {
  computeInfoCompleteness,
  countInfoValues,
  hasFieldValue,
  type ProjectInfoNode,
} from './projectInfoTree';

/** 系统段与补充段的分隔线（独占一行；重算时据此切分） */
export const SHARE_DOC_DIVIDER = '---';

/**
 * 补充段的结构化骨架：首次生成文档时预置在分隔线以下（需求 3 的「问题拆分 / 前因后果 / 涉及人员」）。
 * 只写一次——用户填进去的内容属于补充段，之后系统段怎么重算都不覆盖它；
 * 用户删掉也不要紧（不会再被塞回来）。
 */
export const SHARE_DOC_SECTION_TEMPLATE = [
  '## 问题描述',
  '',
  '（现象、发生时间、影响范围）',
  '',
  '## 前因后果',
  '',
  '（触发条件、已做的排查、初步结论）',
  '',
  '## 涉及人员',
  '',
  '（现场联系人 / 相关责任人）',
  '',
].join('\n');

/** 文档正文标题（与设计稿一致） */
export const SHARE_DOC_TITLE = '问题共享文档';

export interface ShareDocBuildOptions {
  /**
   * 是否连空节点一起写入文档（空值标注「（未填写）」/「（未选择）」）。
   * 默认 false —— 需求 1.2「只展示节点内有信息的部分」；
   * 传 true 即为设计稿截图里带占位文案的完整骨架形态。
   */
  includeEmpty?: boolean;
  /** 项目名留空时不输出 `> 项目：` 行 */
  projectName?: string;
}

/** 可填节点为空时的占位文案（仅 includeEmpty=true 时才会出现在正文里） */
const EMPTY_TEXT = '（未填写）';
const EMPTY_SELECT = '（未选择）';

/** 节点值 → 文档里的一行文本 */
export function shareDocValueText(node: ProjectInfoNode): string {
  if (node.content_type === 'select') {
    const selected = (node.value as { selected?: string } | null)?.selected ?? '';
    return selected.trim() || EMPTY_SELECT;
  }
  if (node.content_type === 'file' || node.content_type === 'image') {
    const name = (node.value as { name?: string } | null)?.name ?? '';
    return name.trim() || EMPTY_TEXT;
  }
  const raw = typeof node.value === 'string' ? node.value : '';
  return raw.trim() || EMPTY_TEXT;
}

function heading(level: number, title: string): string {
  return `${'#'.repeat(Math.min(6, Math.max(1, level)))} ${title}`;
}

/** 一级标签（根节点）按 sort_order 升序 */
function rootNodes(nodes: ProjectInfoNode[]): ProjectInfoNode[] {
  return nodes.filter((n) => n.parent_id === null).slice().sort((a, b) => a.sort_order - b.sort_order);
}

/**
 * 生成系统段：「项目背景信息」章节（按勾选的一级标签）。
 *
 * - 只写**勾选**的一级标签；未勾选的一律不出现在文档里。
 * - 展开到该标签的整棵子树，标题按层级加井号（一级标签 `###`，往下逐级 +1，最多 6 级）。
 * - 默认只写有信息的节点（含空的纯分组节点整支省略）；`includeEmpty=true` 时写全并标注占位。
 */
export function buildProjectBackgroundMarkdown(
  projectName: string,
  nodes: ProjectInfoNode[],
  selectedRootIds: Iterable<string>,
  options: ShareDocBuildOptions = {},
): string {
  const includeEmpty = options.includeEmpty ?? false;
  const selected = new Set(selectedRootIds);

  const byParent = new Map<string | null, ProjectInfoNode[]>();
  nodes.forEach((node) => {
    const list = byParent.get(node.parent_id) ?? [];
    list.push(node);
    byParent.set(node.parent_id, list);
  });
  // 每个节点名下「有值的字段」条数（含自身值），用来整支裁剪空分支
  const counts = countInfoValues(nodes);

  const lines: string[] = [`# ${SHARE_DOC_TITLE}`, ''];
  const name = (options.projectName ?? projectName ?? '').trim();
  if (name) lines.push(`> 项目：${name}`, '');
  lines.push('## 项目背景信息', '');

  const emit = (node: ProjectInfoNode, depth: number) => {
    const children = byParent.get(node.id) ?? [];
    const hasChildren = children.length > 0;
    // 只有末级节点与自带值类型的非末级节点（下拉/附件）才承载值，纯文本分组不承载
    const fillsValue = !hasChildren || node.content_type !== 'text';
    if (!includeEmpty && (counts.get(node.id) ?? 0) === 0) return;

    lines.push(heading(depth, node.title), '');
    if (fillsValue && (hasFieldValue(node) || includeEmpty)) {
      lines.push(shareDocValueText(node), '');
    }
    children
      .slice()
      .sort((a, b) => a.sort_order - b.sort_order)
      .forEach((child) => emit(child, depth + 1));
  };

  rootNodes(nodes)
    .filter((root) => selected.has(root.id))
    .forEach((root) => emit(root, 3));

  const body = lines.join('\n').trimEnd();
  return `${body}\n`;
}

/**
 * 拆出系统段与补充段。找不到分隔线时整篇都算补充段（用户自己写/上传的文档）。
 * 分隔线取**第一个**独占一行的 `---`，其余 `---` 留在补充段里（用户正文里的分隔线不受影响）。
 */
export function splitShareDoc(doc: string): { system: string; user: string } {
  const text = doc ?? '';
  const lines = text.split('\n');
  for (let i = 0; i < lines.length; i += 1) {
    if (lines[i].trim() === SHARE_DOC_DIVIDER) {
      const head = lines.slice(0, i);
      // 隔开正文与分隔线之间多余的空行，重算时不留空档
      while (head.length && !head[head.length - 1].trim()) head.pop();
      return { system: head.join('\n'), user: lines.slice(i + 1).join('\n') };
    }
  }
  return { system: '', user: text };
}

/**
 * 系统段 + 补充段 → 整篇文档（没有补充内容时不写分隔线，文档就是纯系统段）。
 */
export function composeShareDoc(system: string, user: string): string {
  const head = (system ?? '').trimEnd();
  const body = (user ?? '').replace(/^\n+/, '').trimEnd();
  if (!body) return `${head}\n`;
  // 没有系统段（还没选项目 / 上传的文档）时整篇就是补充段，不补分隔线
  if (!head) return `${body}\n`;
  return `${head}\n\n${SHARE_DOC_DIVIDER}\n\n${body}\n`;
}

/**
 * 用新的系统段重算整篇文档，**补充段原样保留**（这是「随勾选自动更新、以下内容不被覆盖」的落点）。
 */
export function mergeShareDoc(doc: string, system: string): string {
  return composeShareDoc(system, splitShareDoc(doc ?? '').user);
}

/**
 * 整篇替换补充段（AI 生成 / 整段粘贴）：系统段（项目背景信息）原样保留，
 * 分隔线以下整体换成 markdown。
 *
 * 会丢掉用户已写的补充内容 —— 调用方必须先让用户确认（见 AiProblemDocGenerator）。
 */
export function replaceUserSection(doc: string, user: string): string {
  return composeShareDoc(splitShareDoc(doc ?? '').system, user);
}

/** 勾选的一级标签里「缺省过半」的那些（提单页提示条用，顺序同树） */
export function missingSelectedTags(
  nodes: ProjectInfoNode[],
  selectedRootIds: Iterable<string>,
): ProjectInfoNode[] {
  const completeness = computeInfoCompleteness(nodes);
  const selected = new Set(selectedRootIds);
  return rootNodes(nodes).filter(
    (root) => selected.has(root.id) && (completeness.get(root.id)?.mostlyEmpty ?? false),
  );
}

/** 待补充的具体节点（发给他人补信息时，工单里要列出到底补哪几条） */
export interface MissingInfoNode {
  id: string;
  /** 「车端软件 / 控制器品牌」 */
  path: string;
  /** 所属一级标签标题 */
  rootTitle: string;
}

/**
 * 收集勾选标签下**可填但为空**的节点（顺序按树先序，便于人照着一条条填）。
 * 与 computeInfoCompleteness 同一判据：末级节点 + 自带值类型的非末级节点才算可填。
 */
export function collectMissingInfoNodes(
  nodes: ProjectInfoNode[],
  selectedRootIds: Iterable<string>,
): MissingInfoNode[] {
  const selected = new Set(selectedRootIds);
  const byParent = new Map<string | null, ProjectInfoNode[]>();
  nodes.forEach((node) => {
    const list = byParent.get(node.parent_id) ?? [];
    list.push(node);
    byParent.set(node.parent_id, list);
  });
  byParent.forEach((items) => items.sort((a, b) => a.sort_order - b.sort_order));

  const result: MissingInfoNode[] = [];
  const walk = (node: ProjectInfoNode, path: string) => {
    const children = byParent.get(node.id) ?? [];
    const fillsValue = children.length === 0 || node.content_type !== 'text';
    const here = `${path} / ${node.title}`;
    if (fillsValue && !hasFieldValue(node)) {
      result.push({ id: node.id, path: here.replace(/^\s*\/\s*/, ''), rootTitle: '' });
    }
    children.forEach((child) => walk(child, here));
  };

  rootNodes(nodes)
    .filter((root) => selected.has(root.id))
    .forEach((root) => {
      const before = result.length;
      const children = byParent.get(root.id) ?? [];
      children.forEach((child) => walk(child, root.title));
      for (let i = before; i < result.length; i += 1) result[i].rootTitle = root.title;
    });
  return result;
}

// —— 提单弹窗里的标签勾选：按项目存本机（与项目信息卡的「筛选」状态各自独立） ——

const SHARE_DOC_TAGS_KEY = 'share-doc:selected-tags';

/** 读本机记住的勾选标签（项目没记过 → 空集合，由调用方决定默认全选） */
export function loadShareDocTags(projectId: string): Set<string> {
  try {
    const raw = localStorage.getItem(`${SHARE_DOC_TAGS_KEY}:${projectId}`);
    const list = raw ? (JSON.parse(raw) as unknown) : [];
    return new Set(Array.isArray(list) ? list.filter((id): id is string => typeof id === 'string') : []);
  } catch {
    return new Set();
  }
}

export function saveShareDocTags(projectId: string, ids: Iterable<string>) {
  try {
    localStorage.setItem(`${SHARE_DOC_TAGS_KEY}:${projectId}`, JSON.stringify([...ids]));
  } catch {
    // 隐私模式等场景静默降级为仅内存态
  }
}
