// 编辑项目信息 —— 项目信息树编辑页（对照原型 routes/projects.$id_.edit.tsx + components/tree/ProjectInformationTree.tsx）。
//
// **权限（本次改造的核心）**：字段定义与项目值分开，页面上的操作也分两类——
//   管理员（permissions 含 'admin'）：改名 / 增删节点 / 改内容形式 / 长按拖动 / 文件导入 /
//     详情模板；节点定义改的是「全局一份」的模板，改一次全体项目生效。
//   普通用户：**只能填值**（文字、下拉、附件），节点编辑与节点历史照旧保留可看可点，
//     但改不了结构——要多记表外信息就点「增补信息」，在允许增补的节点下加本项目自己的字段。
//   后端按接口口径强制：结构类接口 Depends(get_current_admin_user)，值写入接口只认已存在节点。
//   所以这里除了藏按钮，saveValue 也必须走值写入接口（否则普通用户一保存就 403）。
//
// 每行的「历史」看该节点的操作记录（时间 / 人员 / 变动；子节点被删除时记录在父节点下）。
// 有本机没看过的新记录时历史按钮右上角出小红点：保存成功后立即出，点开该节点历史才消失；
// 该节点所在的一级节点（根节点）同时出点，作为「这个一级标签下有未看过的变动」的汇总。
// 已读水位按「项目 + 登录用户」存本机（localStorage，见 shared/utils/projectInfoTree.ts）。
//
// 数据走后端 /api/admin/info-nodes/*（逐节点 CRUD，数据层见 shared/utils/projectInfoTree.ts）：变更先本地乐观更新，
// 接口失败时提示并整树重读回滚；文件/图片内容先上传资源管理服务（与项目文档同一接口）再写节点值。
// 操作记录由后端在每个写接口里随业务同事务落库（backend .../services/info_node_change_service.py），前端只读。
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { BackTop, Input, Navbar, Popup, Toast } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { useAuthStore } from '@/stores/auth';
import ProjectInfoFileImport from './ProjectInfoFileImport';
import {
  MacChevronDown, MacChevronRight, MacChevronsDownUp, MacChevronsUpDown, MacDownload, MacFileText,
  MacGripVertical, MacHistory, MacImage, MacMoreHorizontal, MacPencil, MacPlus, MacScrollText, MacTrash2, MacUpload,
} from '@/shared/components/macaronIcons';
import {
  computeInfoCompleteness,
  createInfoNode,
  deleteInfoNode,
  formatFileSize,
  loadCollapsedIds,
  loadHistoryLatest,
  loadHistorySeen,
  loadInfoNodeChanges,
  loadInfoNodes,
  isInfoNodeVisible,
  moveInfoNode,
  patchInfoNode,
  PROJECT_INFO_MAX_DEPTH,
  removeInfoNode,
  saveCollapsedIds,
  saveHistorySeen,
  setInfoNodeValue,
  subtreeNodeIds,
  unseenHistoryChain,
  unseenHistoryNodes,
  updateInfoNode,
  visibleInfoNodes,
  type ProjectInfoContentType,
  type ProjectInfoFileValue,
  type ProjectInfoNode,
  type ProjectInfoSelectValue,
} from '@/shared/utils/projectInfoTree';
import type { ApiInfoNodeChange } from '@/api/infoNodes';

type DropMode = 'child' | 'before';
const CONTENT_TYPE_NAMES: Record<ProjectInfoContentType, string> = {
  text: '文字输入',
  select: '下拉选择',
  file: '上传文件',
  image: '上传图片',
};
/** 增补信息可选的内容形式（增补只加末级字段，不做下拉/附件以外的东西） */
const CUSTOM_NODE_TYPES: ProjectInfoContentType[] = ['text', 'select', 'image', 'file'];
/** 操作记录的类型标签（与后端 action 一一对应） */
const HISTORY_ACTION_NAMES: Record<string, string> = {
  create: '新增',
  update: '修改',
  move: '移动',
  delete: '删除',
  import: '导入',
  sync: '模板同步',
};

/** 接口错误 → 提示文案（各写操作共用） */
const errMsg = (err: unknown) => (err instanceof Error && err.message ? err.message : '请稍后重试');

export default function ProjectInfoEdit() {
  const { id = '' } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const username = useAuthStore((s) => s.username);
  // 管理员判据与后端 get_current_admin_user 一致：结构类接口只认管理员，
  // 普通用户这边只保留「填值 + 看历史 + 增补信息」。
  const isAdmin = useAuthStore((s) => Array.isArray(s.permissions) && s.permissions.includes('admin'));
  const request = useMemo(() => createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin'), []);

  const [nodes, setNodes] = useState<ProjectInfoNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [fileImportOpen, setFileImportOpen] = useState(false);
  const [collapsedIds, setCollapsedIds] = useState<Set<string>>(() => loadCollapsedIds(id));
  const [editingId, setEditingId] = useState<string | null>(null);
  const [menuNode, setMenuNode] = useState<ProjectInfoNode | null>(null);
  const [historyNode, setHistoryNode] = useState<ProjectInfoNode | null>(null);
  const [historyChanges, setHistoryChanges] = useState<ApiInfoNodeChange[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState(false);
  // 历史按钮上的小红点：有本机没看过的新记录（已读水位存本机，见 projectInfoTree）
  const [unseenHistoryIds, setUnseenHistoryIds] = useState<Set<string>>(new Set());
  const [deleteNode, setDeleteNode] = useState<ProjectInfoNode | null>(null);
  const [selectNode, setSelectNode] = useState<ProjectInfoNode | null>(null);
  const [titleOptionsNode, setTitleOptionsNode] = useState<ProjectInfoNode | null>(null);
  const [draftOption, setDraftOption] = useState('');
  const [draftTitleOptions, setDraftTitleOptions] = useState('');
  const [draggingId, setDraggingId] = useState<string | null>(null);
  const [dropTarget, setDropTarget] = useState<{ id: string; mode: DropMode } | null>(null);
  const [uploadingNodeId, setUploadingNodeId] = useState<string | null>(null);
  const [projectName, setProjectName] = useState('');
  const holdTimer = useRef<number | null>(null);
  /** 正在查看历史的节点 id：请求期间用户切到别的节点时，丢弃过期响应 */
  const historyRequestRef = useRef('');

  // 项目名称仅用于页头副标题（真实数据；失败静默降级为项目编号）
  useEffect(() => {
    if (!id) return;
    request<{ name?: string }>(`/projects/${id}`)
      .then((data) => setProjectName(data.name || ''))
      .catch(() => setProjectName(''));
  }, [id, request]);

  // 信息树真实数据：打开页面读取；保存失败需回滚时整树重读
  const reload = useCallback(async () => {
    if (!id) return;
    setLoading(true);
    setLoadError(false);
    try {
      setNodes(await loadInfoNodes(id));
    } catch {
      setLoadError(true);
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => { void reload(); }, [reload]);

  useEffect(() => { saveCollapsedIds(id, collapsedIds); }, [id, collapsedIds]);

  const byParent = useMemo(() => {
    const map = new Map<string | null, ProjectInfoNode[]>();
    nodes.forEach((node) => {
      const siblings = map.get(node.parent_id) ?? [];
      siblings.push(node);
      map.set(node.parent_id, siblings);
    });
    map.forEach((items) => items.sort((a, b) => a.sort_order - b.sort_order));
    return map;
  }, [nodes]);

  const roots = byParent.get(null) ?? [];
  // 完整度只统计当前看得见的字段（区域联动隐藏的字段不该计入「缺 N」）
  const completeness = useMemo(() => computeInfoCompleteness(visibleInfoNodes(nodes)), [nodes]);

  // 编辑历史（后端每个节点操作都有记录）：拉「各节点最新记录时间」对比本机已读水位，
  // 算哪些节点的历史按钮要出小红点。水位只在「点开历史」时推进——包括自己刚保存的改动，
  // 没点开过就一直带红点（谁没看过谁自己看到）。最新记录 id 存一份在 ref 里，
  // 点开历史时要按它把整棵子树一起标记已读。
  const latestHistoryRef = useRef<Record<string, string>>({});
  const syncHistoryMeta = useCallback(async () => {
    if (!id) return;
    try {
      const latest = await loadHistoryLatest(id);
      latestHistoryRef.current = latest;
      setUnseenHistoryIds(unseenHistoryNodes(latest, loadHistorySeen(id, username)));
    } catch {
      // 红点只是辅助提示：拉取失败静默，不打扰主流程
    }
  }, [id, username]);

  useEffect(() => { void syncHistoryMeta(); }, [syncHistoryMeta]);

  // 写入后端：先本地乐观更新（界面即时反馈），成功后提示；失败时提示原因并整树重读回滚。
  // 保存成功即重算历史元信息，本次改动对应的节点立刻带上小红点（还没点开过）。
  const applyMutation = async (
    optimistic: ProjectInfoNode[],
    action: () => Promise<unknown>,
    successMsg: string,
  ) => {
    setNodes(optimistic);
    try {
      await action();
      Toast({ message: successMsg, theme: 'success' });
      void syncHistoryMeta();
    } catch (err) {
      Toast({ message: `保存失败：${errMsg(err)}`, theme: 'error' });
      void reload();
    }
  };

  // 打开某节点的编辑历史。点开即已读，而且**整棵子树一起读**：
  // 红点是「这一片有你没看过的变动」，从更新的节点一路点到一级标签，看到的都是同一片变动，
  // 所以点开链上任一处的历史，这一串小红点就该一起消失。
  const openHistory = async (node: ProjectInfoNode) => {
    setHistoryNode(node);
    setHistoryChanges([]);
    setHistoryError(false);
    setHistoryLoading(true);
    historyRequestRef.current = node.id;
    try {
      const records = await loadInfoNodeChanges(id, node.id);
      if (historyRequestRef.current !== node.id) return; // 期间切到了别的节点，丢弃本次结果
      setHistoryChanges(records);
      // 水位推进：子树里每个节点都记成它的最新记录 id（summary 里那份；缺了就用本节点的记录兜底）
      const subtree = subtreeNodeIds(nodes, node.id);
      const seen = loadHistorySeen(id, username);
      subtree.forEach((nodeId) => {
        const latestId = latestHistoryRef.current[nodeId];
        if (latestId && seen[nodeId] !== latestId) seen[nodeId] = latestId;
      });
      const ownLatest = records.reduce((acc, record) => (record.id > acc ? record.id : acc), '');
      if (ownLatest && seen[node.id] !== ownLatest) seen[node.id] = ownLatest;
      saveHistorySeen(id, seen, username);
      const subtreeSet = new Set(subtree);
      setUnseenHistoryIds((prev) => {
        const next = new Set([...prev].filter((nodeId) => !subtreeSet.has(nodeId)));
        return next.size === prev.size ? prev : next;
      });
    } catch {
      if (historyRequestRef.current === node.id) setHistoryError(true);
    } finally {
      if (historyRequestRef.current === node.id) setHistoryLoading(false);
    }
  };

  const depthOf = (node: ProjectInfoNode): number => {
    let depth = 1;
    let parent = nodes.find((item) => item.id === node.parent_id);
    while (parent && depth < PROJECT_INFO_MAX_DEPTH + 1) {
      depth += 1;
      parent = nodes.find((item) => item.id === parent?.parent_id);
    }
    return depth;
  };

  const descendantsOf = (nodeId: string): Set<string> => {
    const result = new Set<string>();
    const walk = (parentId: string) => (byParent.get(parentId) ?? []).forEach((child) => {
      result.add(child.id);
      walk(child.id);
    });
    walk(nodeId);
    return result;
  };

  /** 新增节点：管理员加的是（全局）字段定义，普通用户走「增补信息」加本项目自己的字段 */
  const addNode = async (parent: ProjectInfoNode | null, custom = false) => {
    if (parent && depthOf(parent) >= PROJECT_INFO_MAX_DEPTH) {
      Toast({ message: '信息维度过深，建议拆分或合并', theme: 'warning' });
      return;
    }
    if (custom && !parent) {
      Toast({ message: '增补信息需要选择挂在哪个节点下', theme: 'warning' });
      return;
    }
    const siblings = byParent.get(parent?.id ?? null) ?? [];
    try {
      // 节点 id 由后端生成（增补节点要按 node_key 保证同项目内唯一），落库后直接追加
      const node = await createInfoNode(id, parent?.id ?? null, siblings.length * 10, '未命名节点', !custom);
      setNodes((prev) => [...prev, node]);
      if (parent) {
        setCollapsedIds((prev) => { const next = new Set(prev); next.delete(parent.id); return next; });
      }
      if (!custom) setEditingId(node.id);
      void syncHistoryMeta(); // 新增记录落在新节点上：立即重算，历史按钮带红点
    } catch (err) {
      Toast({ message: `新增失败：${errMsg(err)}`, theme: 'error' });
    }
  };

  const renameNode = (node: ProjectInfoNode, title: string) =>
    void applyMutation(patchInfoNode(nodes, node.id, { title }), () => updateInfoNode(node, { title }), '名称已保存');
  // 填值：走值写入接口（任何登录用户），不再借用改节点定义的接口
  const saveValue = (node: ProjectInfoNode, value: unknown) =>
    void applyMutation(patchInfoNode(nodes, node.id, { value }), () => setInfoNodeValue(node, value, id), '内容已保存');

  const changeContentType = (node: ProjectInfoNode, type: ProjectInfoContentType) => {
    const value = type === 'select' ? { selected: '', options: [] } : type === 'text' ? '' : {};
    void applyMutation(
      patchInfoNode(nodes, node.id, { content_type: type, value }),
      // 内容形式属于字段定义：先改定义，再把旧值清掉（同一个节点的值走值写入接口）
      async () => {
        await updateInfoNode(node, { content_type: type });
        await setInfoNodeValue({ ...node, content_type: type }, value, id);
      },
      '内容形式已切换',
    );
    setMenuNode(null);
  };

  const confirmDelete = () => {
    if (!deleteNode) return;
    void applyMutation(
      removeInfoNode(nodes, deleteNode.id),
      () => deleteInfoNode(deleteNode.id),
      '节点及其子节点已删除',
    );
    setDeleteNode(null);
  };

  const uploadNodeFile = async (node: ProjectInfoNode, file: File) => {
    setUploadingNodeId(node.id);
    try {
      const form = new FormData();
      form.append('file', file);
      form.append('owner_id', username || 'admin');
      form.append('resource_type', 'document');
      form.append('category', '项目信息');
      form.append('description', `项目 ${id} 信息节点「${node.title}」`);
      const resource = await request<{ id: number; resource_name: string }>('/resource-manager/resources/', {
        method: 'POST',
        body: form,
      });
      const value = { name: file.name, resource_id: resource.id, size: file.size };
      await setInfoNodeValue(node, value, id);
      setNodes((prev) => patchInfoNode(prev, node.id, { value }));
      Toast({ message: '文件已上传并保存', theme: 'success' });
      void syncHistoryMeta();
    } catch (err) {
      Toast({ message: `上传失败：${errMsg(err)}`, theme: 'error' });
    } finally {
      setUploadingNodeId(null);
    }
  };

  const removeNodeFile = (node: ProjectInfoNode) => {
    void applyMutation(patchInfoNode(nodes, node.id, { value: {} }), () => setInfoNodeValue(node, {}, id), '已移除文件引用');
  };

  // —— 长按拖动调整从属（原生 Pointer 事件，不引入依赖；与设计稿一致 400ms 长按） ——

  const clearHold = () => {
    if (holdTimer.current != null) { window.clearTimeout(holdTimer.current); holdTimer.current = null; }
  };

  const startHold = (nodeId: string) => {
    clearHold();
    holdTimer.current = window.setTimeout(() => {
      setDraggingId(nodeId);
      navigator.vibrate?.(35);
    }, 400);
  };

  const pointerTarget = (clientX: number, clientY: number): { target: ProjectInfoNode; mode: DropMode } | null => {
    const element = document.elementFromPoint(clientX, clientY)?.closest<HTMLElement>('[data-info-node]');
    if (!element) return null;
    const target = nodes.find((node) => node.id === element.dataset.infoNode);
    if (!target) return null;
    const rect = element.getBoundingClientRect();
    return { target, mode: clientY < rect.top + rect.height * 0.27 ? 'before' : 'child' };
  };

  const dragMove = (event: React.PointerEvent) => {
    if (!draggingId) return;
    const hit = pointerTarget(event.clientX, event.clientY);
    if (hit && hit.target.id !== draggingId) setDropTarget({ id: hit.target.id, mode: hit.mode });
  };

  const drop = (event: React.PointerEvent) => {
    clearHold();
    if (!draggingId) return;
    const hit = pointerTarget(event.clientX, event.clientY);
    if (hit) moveNode(draggingId, hit.target, hit.mode);
    setDraggingId(null);
    setDropTarget(null);
  };

  const moveNode = (draggedId: string, target: ProjectInfoNode, mode: DropMode) => {
    if (draggedId === target.id || descendantsOf(draggedId).has(target.id)) return;
    const dragged = nodes.find((node) => node.id === draggedId);
    if (!dragged) return;
    const parentId = mode === 'child' ? target.id : target.parent_id;
    const targetDepth = mode === 'child' ? depthOf(target) + 1 : depthOf(target);
    const subtreeDepth = Math.max(0, ...[...descendantsOf(dragged.id)].map((descendantId) => {
      const item = nodes.find((node) => node.id === descendantId);
      return item ? depthOf(item) - depthOf(dragged) : 0;
    }));
    if (targetDepth + subtreeDepth > PROJECT_INFO_MAX_DEPTH) {
      Toast({ message: '信息维度过深，建议拆分或合并', theme: 'warning' });
      return;
    }
    const targetSiblings = byParent.get(parentId) ?? [];
    const sortOrder = mode === 'child' ? targetSiblings.length : target.sort_order;
    void applyMutation(
      patchInfoNode(nodes, dragged.id, { parent_id: parentId, sort_order: sortOrder }),
      () => moveInfoNode(dragged, parentId, sortOrder),
      '从属关系已调整',
    );
  };

  const expandAll = () => setCollapsedIds(new Set());
  const collapseAll = () => setCollapsedIds(new Set(
    nodes.filter((node) => (byParent.get(node.id) ?? []).length > 0).map((node) => node.id),
  ));

  // —— 下拉选项管理（选项属于**字段定义**，因此只有管理员能改；改的是增补节点自己的 config） ——

  const selectValue = (selectNode?.value ?? {}) as Partial<ProjectInfoSelectValue>;
  const saveSelectValue = (nextNode: ProjectInfoNode | null, value: Partial<ProjectInfoSelectValue>) => {
    if (!nextNode) return;
    const merged = { selected: value.selected ?? '', options: value.options ?? [] };
    void applyMutation(
      patchInfoNode(nodes, nextNode.id, { value: merged }),
      // options 落到节点定义（config），selected 落到本项目值：后端按定义层校验后写入
      () => updateInfoNode(nextNode, { options: merged.options }),
      '选项已保存',
    );
    setSelectNode((prev) => (prev && prev.id === nextNode.id ? { ...prev, value: merged } : prev));
  };

  const openTitleOptions = (node: ProjectInfoNode) => {
    setDraftTitleOptions((node.titleOptions ?? []).join('，'));
    setTitleOptionsNode(node);
    setMenuNode(null);
  };

  const saveTitleOptions = (options: string[]) => {
    if (!titleOptionsNode) return;
    void applyMutation(
      patchInfoNode(nodes, titleOptionsNode.id, { options }),
      () => updateInfoNode(titleOptionsNode, { titleOptions: options }),
      options.length ? '标题备选项已保存' : '已改回手动输入标题',
    );
    setTitleOptionsNode(null);
  };

  /** 增补信息：普通用户不碰模板，只在自己项目下挂一个新字段（后端记成 project_id 非空的自定义节点） */
  const [customParent, setCustomParent] = useState<ProjectInfoNode | null>(null);
  const [customTitle, setCustomTitle] = useState('');
  const [customType, setCustomType] = useState<ProjectInfoContentType>('text');

  const openCustomInfo = (parent: ProjectInfoNode) => {
    setCustomParent(parent);
    setCustomTitle('');
    setCustomType('text');
  };

  const confirmCustomInfo = async () => {
    const parent = customParent;
    const title = customTitle.trim();
    if (!parent) return;
    if (!title) {
      Toast({ message: '请填写信息名称', theme: 'warning' });
      return;
    }
    const siblings = byParent.get(parent.id) ?? [];
    setCustomParent(null);
    try {
      // 第二个参数 false → 走「增补」接口：节点挂在当前项目下，不动全局模板
      const node = await createInfoNode(id, parent.id, siblings.length * 10, title, false);
      const created = node.content_type === customType
        ? node
        : await updateInfoNode(node, { content_type: customType });
      setNodes((prev) => [...prev, created]);
      setCollapsedIds((prev) => { const next = new Set(prev); next.delete(parent.id); return next; });
      void syncHistoryMeta();
      Toast({ message: `已在「${parent.title}」下增补「${title}」`, theme: 'success' });
    } catch (err) {
      Toast({ message: `增补失败：${errMsg(err)}`, theme: 'error' });
    }
  };

  // 小红点要显示在哪些行上：有未读记录的节点本身 + 它的**每一层上级**（一直冒到一级标签）。
  // 判定与消失都走同一套水位：没点开过就带点；点开链上任一处的历史时，
  // openHistory 会把那棵子树整体标记已读，这一串点随之一起消失。
  // 展示页标签池只到一级标签，仍用 unseenHistoryRoots（ProjectInfoCard）。
  const historyDotIds = useMemo(
    () => unseenHistoryChain(nodes, unseenHistoryIds),
    [nodes, unseenHistoryIds],
  );

  const rowProps = {
    byParent, collapsedIds, editingId, draggingId, dropTarget, uploadingNodeId,
    historyDotIds, isAdmin,
    onToggle: (nodeId: string) => setCollapsedIds((current) => {
      const next = new Set(current);
      if (next.has(nodeId)) next.delete(nodeId); else next.add(nodeId);
      return next;
    }),
    onEdit: setEditingId,
    onAdd: addNode,
    onCustomInfo: openCustomInfo,
    onRename: renameNode,
    onMenu: setMenuNode,
    onHistory: openHistory,
    onSaveValue: saveValue,
    onUpload: uploadNodeFile,
    onRemoveFile: removeNodeFile,
    onOpenSelectEditor: (node: ProjectInfoNode) => { setDraftOption(''); setSelectNode(node); },
    onHoldStart: startHold,
    onHoldEnd: clearHold,
    onDragMove: dragMove,
    onDrop: drop,
  };

  return (
    <div>
      <Navbar title="编辑项目信息" leftArrow onLeftClick={() => navigate(-1)} fixed />
      <div style={{ padding: 16, paddingTop: 64 }}>
        <section className="mac-card mac-card--pad">
          <div className="mac-info__head">
            <div className="mac-info__title-wrap">
              <h3 className="mac-info__title">信息节点</h3>
              <p className="mac-info__subtitle">
                {projectName || `项目 ${id}`} · 长按节点可拖动调整从属
              </p>
            </div>
            <div className="mac-info__actions">
              <button type="button" className="mac-btn mac-btn--ghost mac-info__iconbtn" onClick={expandAll} title="全部展开" aria-label="全部展开"><MacChevronsUpDown size={15} /></button>
              <button type="button" className="mac-btn mac-btn--ghost mac-info__iconbtn" onClick={collapseAll} title="全部折叠" aria-label="全部折叠"><MacChevronsDownUp size={15} /></button>
              {isAdmin && (
                <button
                  type="button"
                  className="mac-btn mac-btn--outline mac-info__act"
                  title="编辑项目详情模板（保存后同步到所有项目的节点）"
                  onClick={() => navigate('/admin/project-info-template')}
                >
                  <MacScrollText size={13} />详情模板
                </button>
              )}
              <button
                type="button"
                className="mac-btn mac-btn--outline mac-info__act"
                onClick={() => setFileImportOpen(true)}
              >
                <MacUpload size={13} />文件导入
              </button>
              {isAdmin && (
                <button type="button" className="mac-btn mac-btn--primary mac-info__act" onClick={() => void addNode(null)}>
                  <MacPlus size={13} />新标签
                </button>
              )}
            </div>
          </div>

          {loading ? (
            <div className="mac-info__state">正在加载信息节点…</div>
          ) : loadError ? (
            <div className="mac-info__state">
              信息节点加载失败
              <div className="mac-info__state-sub">请检查网络后重试</div>
              <button type="button" className="mac-btn mac-btn--outline" style={{ marginTop: 12 }} onClick={() => void reload()}>
                重新加载
              </button>
            </div>
          ) : roots.length === 0 ? (
            <div className="mac-info__state">
              {isAdmin ? '还没有信息节点，点击右上角「新标签」创建' : '信息模板还没有配置节点，请联系管理员'}
              <div className="mac-info__state-sub">
                {isAdmin ? '保存后对所有项目生效' : '管理员配置好模板后，这里就能填写项目信息'}
              </div>
            </div>
          ) : (
            <div className="mac-info__tree">
              {roots.map((root) => (
                <InfoRow key={root.id} {...rowProps} node={root} depth={1} missingCount={completeness.get(root.id)?.empty} />
              ))}
            </div>
          )}
        </section>
      </div>

      {/* 节点操作菜单：内容形式 / 标题备选项 / 删除；改名走行内的铅笔按钮。
          内容形式对**除一级标签外**的节点都开放——「只有末级才能改类型」的限制已取消，
          下拉车型这种「自带值又带子节点」的节点同样要能改。
          菜单只对本项目增补节点弹出（全局字段只读，改动走「详情模板」）。 */}
      <Popup visible={!!menuNode} onClose={() => setMenuNode(null)} placement="bottom" showOverlay>
        <div className="mac-sheet">
          <h4 className="mac-sheet__title">节点操作{menuNode ? ` · ${menuNode.title}` : ''}</h4>
          {menuNode && (menuNode.parent_id
            ? (
              <>
                {(Object.keys(CONTENT_TYPE_NAMES) as ProjectInfoContentType[]).map((type) => (
                  <button key={type} type="button" className="mac-choice" onClick={() => menuNode && changeContentType(menuNode, type)}>
                    <span className="mac-choice__label">
                      {CONTENT_TYPE_NAMES[type]}{menuNode.content_type === type ? ' · 当前' : ''}
                    </span>
                  </button>
                ))}
                {/* 非末级还可以把标题也做成下拉（标题备选项）；一级标签以外的分组节点都可能用到 */}
                {(byParent.get(menuNode.id) ?? []).length > 0 && (
                  <button type="button" className="mac-choice" onClick={() => menuNode && openTitleOptions(menuNode)}>
                    <span className="mac-choice__label">标题改为下拉选择</span>
                  </button>
                )}
              </>
            )
            : (
              <p className="mac-info__state-sub" style={{ padding: '10px 0' }}>
                一级标签只作分组，不单独填值
              </p>
            ))}
          <button type="button" className="mac-choice" onClick={() => { setDeleteNode(menuNode); setMenuNode(null); }}>
            <span className="mac-choice__label mac-info__danger-text"><MacTrash2 size={14} />删除节点</span>
          </button>
        </div>
      </Popup>

      {/* 删除确认（删除会连带整棵子树，且对所有协作者生效） */}
      <Popup visible={!!deleteNode} onClose={() => setDeleteNode(null)} placement="bottom" showOverlay>
        <div className="mac-sheet">
          <h4 className="mac-sheet__title">删除节点</h4>
          <p className="mac-info__confirm">删除「{deleteNode?.title}」及其所有子节点？删除后所有协作者都将不再看到这些节点。</p>
          <div className="mac-info__confirm-actions">
            <button type="button" className="mac-btn mac-btn--outline" onClick={() => setDeleteNode(null)}>取消</button>
            <button type="button" className="mac-btn mac-info__danger" onClick={confirmDelete}>删除</button>
          </div>
        </div>
      </Popup>

      {/* 增补信息：普通用户在某个允许增补的节点下挂一个新字段（只影响本项目，不动全局模板） */}
      <Popup visible={!!customParent} onClose={() => setCustomParent(null)} placement="bottom" showOverlay>
        <div className="mac-sheet">
          <h4 className="mac-sheet__title">增补信息{customParent ? ` · ${customParent.title}` : ''}</h4>
          <Input value={customTitle} onChange={(v: string | number) => setCustomTitle(String(v))} placeholder="信息名称，例如「临时调试口令」" />
          <p className="mac-info__state-sub" style={{ padding: '10px 0 4px' }}>内容形式</p>
          <div className="mac-info__actions">
            {CUSTOM_NODE_TYPES.map((type) => (
              <button
                key={type}
                type="button"
                className={`mac-btn ${customType === type ? 'mac-btn--primary' : 'mac-btn--outline'} mac-info__act`}
                onClick={() => setCustomType(type)}
              >
                {CONTENT_TYPE_NAMES[type]}
              </button>
            ))}
          </div>
          <div className="mac-info__confirm-actions" style={{ marginTop: 16 }}>
            <button type="button" className="mac-btn mac-btn--outline" onClick={() => setCustomParent(null)}>取消</button>
            <button type="button" className="mac-btn mac-btn--primary" onClick={() => void confirmCustomInfo()}>增补</button>
          </div>
        </div>
      </Popup>

      {/* 文件导入：AI 识别弹层（上传文档 → 三组预览勾选确认 → 逐节点落库） */}
      <ProjectInfoFileImport
        visible={fileImportOpen}
        onClose={() => setFileImportOpen(false)}
        projectId={id}
        nodes={nodes}
        onApplied={() => { void reload(); void syncHistoryMeta(); }}
      />

      {/* 编辑历史：后端真实操作记录（时间/人员/节点/具体变动）；打开即标记已读（小红点消失） */}
      <Popup visible={!!historyNode} onClose={() => setHistoryNode(null)} placement="bottom" showOverlay>
        <div className="mac-sheet">
          <h4 className="mac-sheet__title">编辑历史{historyNode ? ` · ${historyNode.title}` : ''}</h4>
          {historyLoading ? (
            <div className="mac-info__state">正在加载编辑历史…</div>
          ) : historyError ? (
            <div className="mac-info__state">
              编辑历史加载失败
              <div className="mac-info__state-sub">请检查网络后重试</div>
              <button
                type="button"
                className="mac-btn mac-btn--outline"
                style={{ marginTop: 12 }}
                onClick={() => historyNode && void openHistory(historyNode)}
              >
                重新加载
              </button>
            </div>
          ) : historyChanges.length === 0 ? (
            <div className="mac-info__state">
              暂无编辑记录
              <div className="mac-info__state-sub">该节点的新增、修改、移动会记录在这里；子节点被删除时，删除记录显示在本节点下</div>
            </div>
          ) : (
            <ul className="mac-history">
              {historyChanges.map((record) => (
                <li key={record.id} className="mac-history__item">
                  <div className="mac-history__head">
                    <span className="mac-history__who">{record.operator_name || record.operator || '未知用户'}</span>
                    <span className="mac-history__action">{HISTORY_ACTION_NAMES[record.action] ?? record.action}</span>
                    <span className="mac-history__when">{record.created_at}</span>
                  </div>
                  <p className="mac-history__what">{record.detail || `${HISTORY_ACTION_NAMES[record.action] ?? '操作'}节点「${record.node_title}」`}</p>
                </li>
              ))}
            </ul>
          )}
        </div>
      </Popup>

      {/* 下拉选项管理：选项由用户自行增删 */}
      <Popup visible={!!selectNode} onClose={() => setSelectNode(null)} placement="bottom" showOverlay>
        <div className="mac-sheet">
          <h4 className="mac-sheet__title">下拉选项{selectNode ? ` · ${selectNode.title}` : ''}</h4>
          {(selectValue.options ?? []).length === 0 && (
            <p className="mac-info__state-sub" style={{ padding: '10px 0' }}>还没有选项，先添加一个</p>
          )}
          {(selectValue.options ?? []).map((option) => (
            <div key={option} className="mac-opt-row">
              <span className="mac-opt-row__label">{option}</span>
              <button
                type="button"
                className="mac-info__iconbtn"
                aria-label={`删除选项 ${option}`}
                onClick={() => selectNode && saveSelectValue(selectNode, { selected: selectValue.selected, options: (selectValue.options ?? []).filter((item) => item !== option) })}
              >
                <MacTrash2 size={14} />
              </button>
            </div>
          ))}
          <div className="mac-opt-add">
            <Input value={draftOption} onChange={(v: string | number) => setDraftOption(String(v))} placeholder="输入新选项" />
            <button
              type="button"
              className="mac-btn mac-btn--primary"
              onClick={() => {
                const option = draftOption.trim();
                if (!option || !selectNode) return;
                saveSelectValue(selectNode, { selected: selectValue.selected, options: [...(selectValue.options ?? []), option] });
                setDraftOption('');
              }}
            >
              添加
            </button>
          </div>
          <div className="mac-sheet__actions">
            <button type="button" className="mac-btn mac-btn--primary mac-btn--block" onClick={() => setSelectNode(null)}>完成</button>
          </div>
        </div>
      </Popup>

      {/* 标题备选项（非末级节点标题可在候选中选择；留空改回手动输入） */}
      <Popup visible={!!titleOptionsNode} onClose={() => setTitleOptionsNode(null)} placement="bottom" showOverlay>
        <div className="mac-sheet">
          <h4 className="mac-sheet__title">标题备选项{titleOptionsNode ? ` · ${titleOptionsNode.title}` : ''}</h4>
          <Input value={draftTitleOptions} onChange={(v: string | number) => setDraftTitleOptions(String(v))} placeholder="用逗号分隔，留空则改回手动输入" />
          <div className="mac-info__confirm-actions" style={{ marginTop: 16 }}>
            <button type="button" className="mac-btn mac-btn--outline" onClick={() => saveTitleOptions([])}>清除</button>
            <button type="button" className="mac-btn mac-btn--primary" onClick={() => saveTitleOptions(draftTitleOptions.split(/[,，]/).map((item) => item.trim()).filter(Boolean))}>确定</button>
          </div>
        </div>
      </Popup>

      {/* 一键回到顶部：滚动超过 200px 时出现在右下角（滚动容器是 MainLayout 的 .tabbar-shell__content） */}
      <BackTop
        container={() => document.querySelector('.tabbar-shell__content') as HTMLElement}
        visibilityHeight={200}
        theme="round"
        style={{ bottom: 'calc(56px + env(safe-area-inset-bottom) + 12px)' }}
      />
    </div>
  );
}

// —— 树行（递归） ——

interface InfoRowProps {
  node: ProjectInfoNode;
  depth: number;
  missingCount?: number | undefined;
  byParent: Map<string | null, ProjectInfoNode[]>;
  collapsedIds: Set<string>;
  editingId: string | null;
  draggingId: string | null;
  dropTarget: { id: string; mode: DropMode } | null;
  uploadingNodeId: string | null;
  /** 历史按钮右上角要出小红点的节点 id 集合（自身有未读记录，或下辖子树里有） */
  historyDotIds: Set<string>;
  /** 结构类操作（改名/增删/改类型/拖动）只对管理员开放；普通用户只有填值、历史、增补信息 */
  isAdmin: boolean;
  onToggle: (id: string) => void;
  onEdit: (id: string | null) => void;
  onAdd: (parent: ProjectInfoNode, custom?: boolean) => void;
  onCustomInfo: (parent: ProjectInfoNode) => void;
  onRename: (node: ProjectInfoNode, title: string) => void;
  onMenu: (node: ProjectInfoNode) => void;
  onHistory: (node: ProjectInfoNode) => void;
  onSaveValue: (node: ProjectInfoNode, value: unknown) => void;
  onUpload: (node: ProjectInfoNode, file: File) => void;
  onRemoveFile: (node: ProjectInfoNode) => void;
  onOpenSelectEditor: (node: ProjectInfoNode) => void;
  onHoldStart: (id: string) => void;
  onHoldEnd: () => void;
  onDragMove: (event: React.PointerEvent) => void;
  onDrop: (event: React.PointerEvent) => void;
}

function InfoRow(props: InfoRowProps) {
  const { node, depth } = props;
  const allChildren = props.byParent.get(node.id) ?? [];
  // 区域联动字段按所选区域显隐（节点仍在，只是不渲染）；是否存在子节点按完整列表判断
  const children = allChildren.filter((child) => isInfoNodeVisible(child, allChildren));
  const isLeaf = allChildren.length === 0;
  // 有没有自己的值：末级节点都有；非末级节点只有下拉/附件这类才有（纯文本分组没有）。
  // 车型1 是「有值又有子节点」的典型——下拉选中的型号和下面的数量都要能编辑。
  const showsValue = isLeaf || node.content_type !== 'text';
  const level = Math.min(depth, PROJECT_INFO_MAX_DEPTH);
  const isCollapsed = props.collapsedIds.has(node.id);
  const activeDrop = props.dropTarget?.id === node.id;
  const titleOptions = node.titleOptions ?? [];
  const hasUnseenHistory = props.historyDotIds.has(node.id);
  const classNames = [
    'mac-info-row',
    `mac-info-row--d${level}`,
    props.draggingId === node.id ? 'is-dragging' : '',
    activeDrop && props.dropTarget?.mode === 'child' ? 'is-drop-child' : '',
    activeDrop && props.dropTarget?.mode === 'before' ? 'is-drop-before' : '',
  ].filter(Boolean).join(' ');

  return (
    <div className={depth > 1 ? 'mac-info-subtree' : undefined}>
      <div data-info-node={node.id} className={classNames}>
        <div className="mac-info-row__main">
          {props.isAdmin && node.is_custom && (
            <span
              className="mac-info-row__grip"
              aria-label="长按拖动调整从属"
              onPointerDown={(event) => { event.currentTarget.setPointerCapture(event.pointerId); props.onHoldStart(node.id); }}
              onPointerMove={props.onDragMove}
              onPointerUp={props.onDrop}
              onPointerCancel={props.onHoldEnd}
            >
              <MacGripVertical size={14} />
            </span>
          )}
          <button
            type="button"
            className="mac-info-row__toggle"
            disabled={isLeaf}
            onClick={() => props.onToggle(node.id)}
            aria-label={isCollapsed ? '展开' : '收起'}
          >
            {children.length ? (isCollapsed ? <MacChevronRight size={15} /> : <MacChevronDown size={15} />) : <span className="mac-info-row__toggle-ghost" />}
          </button>
          {(props.isAdmin && props.editingId === node.id) ? (
            <input
              className="mac-info-row__input"
              autoFocus
              defaultValue={node.title}
              onBlur={(event) => {
                const title = event.target.value.trim();
                if (title && title !== node.title) props.onRename(node, title);
                props.onEdit(null);
              }}
              onKeyDown={(event) => { if (event.key === 'Enter') event.currentTarget.blur(); }}
            />
          ) : (props.isAdmin && !isLeaf && titleOptions.length) ? (
            <select
              className="mac-info-row__select"
              value={titleOptions.includes(node.title) ? node.title : ''}
              aria-label={`${node.title}标题`}
              onChange={(event) => event.target.value && props.onRename(node, event.target.value)}
            >
              <option value="" disabled>{node.title}</option>
              {titleOptions.map((option) => <option key={option} value={option}>{option}</option>)}
            </select>
          ) : (
            <span className="mac-info-row__title">{node.title}</span>
          )}
          {level === 1 && (props.missingCount ?? 0) > 0 && (
            <span className="mac-info-row__missing" title={`${props.missingCount} 项信息未填写`}>缺 {props.missingCount}</span>
          )}
          <div className="mac-info-row__ops">
            {props.isAdmin ? (
              <>
                {/* 全局字段定义改不动（后端 403，只能走「详情模板」），所以结构按钮只对本项目增补的节点出 */}
                {node.is_custom && (
                  <>
                    <button type="button" className="mac-info-row__op" onClick={() => props.onEdit(node.id)} aria-label={`编辑${node.title}`} title="编辑节点"><MacPencil size={15} /></button>
                    <button type="button" className="mac-info-row__op" onClick={() => props.onMenu(node)} aria-label="更多操作"><MacMoreHorizontal size={15} /></button>
                  </>
                )}
                <button type="button" className="mac-info-row__op" onClick={() => props.onAdd(node)} aria-label={`在${node.title}下新增`}><MacPlus size={15} /></button>
                <button
                  type="button"
                  className="mac-info-row__op"
                  onClick={() => props.onHistory(node)}
                  aria-label={`查看${node.title}的编辑历史`}
                  title={hasUnseenHistory ? '有新的编辑记录' : '编辑历史'}
                >
                  <MacHistory size={15} />
                  {hasUnseenHistory && <span className="mac-info-row__op-dot" aria-hidden="true" />}
                </button>
              </>
            ) : (
              <>
                {/* 普通用户：只留「增补信息」（任何节点下都能加，只要还没到第 4 层）+ 编辑历史，
                    结构操作一律不给。层数是唯一的边界（2026-09-18 起 allow_custom 不再是闸门） */}
                {depth < PROJECT_INFO_MAX_DEPTH && (
                  <button
                    type="button"
                    className="mac-info-row__op"
                    onClick={() => props.onCustomInfo(node)}
                    aria-label={`在${node.title}下增补信息`}
                    title="增补信息"
                  >
                    <MacPlus size={15} />
                  </button>
                )}
                <button
                  type="button"
                  className="mac-info-row__op"
                  onClick={() => props.onHistory(node)}
                  aria-label={`查看${node.title}的编辑历史`}
                  title={hasUnseenHistory ? '有新的编辑记录' : '编辑历史'}
                >
                  <MacHistory size={15} />
                  {hasUnseenHistory && <span className="mac-info-row__op-dot" aria-hidden="true" />}
                </button>
              </>
            )}
          </div>
        </div>
        {showsValue && <NodeContent {...props} />}
      </div>
      {!isCollapsed && children.map((child) => (
        <InfoRow key={child.id} {...props} node={child} depth={depth + 1} missingCount={undefined} />
      ))}
    </div>
  );
}

// —— 节点内容编辑（四种内容形式）：末级节点 + 自带值类型的非末级节点（下拉车型）都走这里 ——

function NodeContent(props: InfoRowProps) {
  const { node } = props;
  if (node.content_type === 'select') {
    const data = (node.value ?? {}) as Partial<ProjectInfoSelectValue>;
    return (
      <div className="mac-info-node__select">
        <select
          value={data.selected ?? ''}
          aria-label={`${node.title}内容`}
          onChange={(event) => props.onSaveValue(node, { selected: event.target.value, options: data.options ?? [] })}
        >
          <option value="">请选择</option>
          {(data.options ?? []).map((option) => <option key={option} value={option}>{option}</option>)}
        </select>
        {/* 选项属于字段定义：全局字段的选项在「详情模板」里改，这里只放本项目增补字段的 */}
        {props.isAdmin && node.is_custom && (
          <button type="button" className="mac-btn mac-btn--outline mac-info-node__manage" onClick={() => props.onOpenSelectEditor(node)}>
            管理
          </button>
        )}      </div>
    );
  }
  if (node.content_type === 'file' || node.content_type === 'image') {
    return <FileContent node={node} uploading={props.uploadingNodeId === node.id} onUpload={props.onUpload} onRemove={props.onRemoveFile} />;
  }
  return (
    <textarea
      className="mac-info-node__text"
      aria-label={`${node.title}内容`}
      defaultValue={typeof node.value === 'string' ? node.value : ''}
      placeholder="填写内容"
      rows={1}
      onBlur={(event) => { if (event.target.value !== (typeof node.value === 'string' ? node.value : '')) props.onSaveValue(node, event.target.value); }}
    />
  );
}

function FileContent({ node, uploading, onUpload, onRemove }: {
  node: ProjectInfoNode;
  uploading: boolean;
  onUpload: (node: ProjectInfoNode, file: File) => void;
  onRemove: (node: ProjectInfoNode) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [imageBroken, setImageBroken] = useState(false);
  const file = (node.value ?? {}) as Partial<ProjectInfoFileValue>;
  const hasFile = !!file.name && file.resource_id != null;
  const downloadUrl = hasFile ? `${API_CONFIG.ADMIN.BASE_URL}/resource-manager/resources/${file.resource_id}/download` : '';
  return (
    <div className="mac-info-node__file">
      <input
        ref={inputRef}
        type="file"
        hidden
        accept={node.content_type === 'image' ? 'image/*' : undefined}
        onChange={(event) => {
          const selected = event.target.files?.[0];
          if (selected) onUpload(node, selected);
          event.target.value = '';
        }}
      />
      {hasFile ? (
        <div className="mac-info-node__filebox">
          {/* 图片节点带缩略图（真实资源服务地址；加载失败降级为普通附件行） */}
          {node.content_type === 'image' && !imageBroken && (
            <a href={downloadUrl} target="_blank" rel="noreferrer" className="mac-info-node__thumblink">
              <img className="mac-doc__thumb" src={downloadUrl} alt={file.name} onError={() => setImageBroken(true)} />
            </a>
          )}
          <div className="mac-info-node__filerow">
            {node.content_type === 'image' ? <MacImage size={15} /> : <MacFileText size={15} />}
            <span className="mac-info-node__filename">{file.name}</span>
            {typeof file.size === 'number' && <span className="mac-info-node__filesize">{formatFileSize(file.size)}</span>}
            <a
              className="mac-doc__dl"
              href={downloadUrl}
              download={file.name}
              aria-label="下载"
            >
              <MacDownload size={15} />
            </a>
            <button type="button" className="mac-info-row__op mac-info__danger-text" onClick={() => onRemove(node)} aria-label="移除文件"><MacTrash2 size={14} /></button>
          </div>
        </div>
      ) : (
        <button type="button" className="mac-btn mac-btn--outline mac-info-node__choose" disabled={uploading} onClick={() => inputRef.current?.click()}>
          {node.content_type === 'image' ? <MacImage size={13} /> : <MacFileText size={13} />}
          {uploading ? '上传中…' : `选择${node.content_type === 'image' ? '图片' : '文件'}`}
        </button>
      )}
    </div>
  );
}
