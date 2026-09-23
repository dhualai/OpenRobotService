// 项目信息树节点 API —— 对接 admin 模块 /api/admin/info-nodes/*
// 后端实现：backend/app/modules/admin/api/info_nodes.py（路由前缀 /info-nodes，挂在 /api/admin 下）
// 契约要点（改造后）：
//   - 字段定义与项目值分离：节点（title/content_type）是「定义」，value 是**本项目**填的数据；
//   - 结构类写接口（增删改节点/位置/导入/模板）仅管理员；值写入（PUT /nodes/{id}/value）
//     任何登录用户都能用，且只能写已存在节点——普通用户要记表外信息走「增补信息」；
//   - value 在库里是 JSON，服务端按值类型编码后再下发：下拉/布尔为 {selected, options}，
//     附件为 {name, resource_id, size}，其余为字符串或 null；
//   - 换父/排序走 move；import 为纯增补（只增不改不删，不再清空旧节点）；
//   - 删除节点会连带删除整棵子树，但只允许删本项目的增补节点。
import { ApiError, createRequest } from './client';
import API_CONFIG from '@/config/api';

/** 下拉 / 布尔类节点的值：选项来自字段定义（服务端合并下发），selected 是本项目的选中项 */
export interface ApiInfoValueSelect {
  selected: string;
  options: string[];
}

/** 附件类节点的值 */
export interface ApiInfoValueAttachment {
  name: string;
  resource_id?: string;
  size?: number;
}

export type ApiInfoNodeValue = string | ApiInfoValueSelect | ApiInfoValueAttachment | null;

/** 后端原始节点（value 由服务端按值类型编码；树查询时每节点含 children） */
export interface ApiInfoNode {
  id: string;
  project_id: string | null;
  parent_id: string | null;
  title: string;
  content_type: string;
  value: ApiInfoNodeValue;
  sort_order: number;
  created_at: string;
  updated_at: string;
  children?: ApiInfoNode[];
  /** 字段标识（服务端维护，程序引用用；不在界面上展示） */
  node_key?: string;
  /** root / group / field */
  node_type?: string;
  /** 内部细分类型 text/number/boolean/date/select/multi_select/person/attachment/json */
  value_type?: string;
  /** 该字段在模板里是否必填 */
  required?: boolean;
  /** 节点下是否允许「增补信息」——2026-09-18 起不再是闸门，所有节点都可增补（服务端恒为 true） */
  allow_custom?: boolean;
  /** 下拉类字段的可选项（定义在节点上，全员共用） */
  options?: string[];
  /** 非末级节点的标题备选项（定义在节点上，全员共用） */
  titleOptions?: string[];
  /** true = 本项目增补的节点（非全局字段定义） */
  is_custom?: boolean;
}

/** 增补一个节点（管理员走 /projects/{id}，普通用户走 /projects/{id}/custom-nodes，载荷相同） */
export interface ApiInfoNodeCreate {
  parent_id?: string | null;
  title?: string;
  content_type?: string;
  value_type?: string;
  sort_order?: number;
  /** 外部标识，仅管理员接口认；增补入口服务端强制忽略 */
  node_key?: string;
}

/** 可更新字段（仅管理员，且只对「本项目增补的节点」有效）；parent_id 不在此列，换父走 move */
export interface ApiInfoNodeUpdate {
  title?: string;
  content_type?: string;
  value_type?: string;
  sort_order?: number;
  required?: boolean;
  allow_custom?: boolean;
  /** 下拉选项（字段定义，落节点 config） */
  options?: string[];
  /** 标题备选项（字段定义，落节点 config） */
  titleOptions?: string[];
}

/** import 接口的递归节点结构（children 递归嵌套） */
export interface ApiInfoTreeImportNode {
  title: string;
  content_type?: string;
  value?: ApiInfoNodeValue;
  sort_order?: number;
  children?: ApiInfoTreeImportNode[];
}

// 与其他 api 模块一致：调用时再建 requester，不在模块顶层求值（便于测试 mock @/api/client）
const request = () => createRequest(API_CONFIG.ADMIN.BASE_URL, '信息树服务');

/** 获取项目完整信息树（递归嵌套；空树返回 []） */
export async function fetchInfoTree(projectId: string): Promise<ApiInfoNode[]> {
  const data = await request()<ApiInfoNode[]>(`/info-nodes/projects/${encodeURIComponent(projectId)}`);
  return Array.isArray(data) ? data : [];
}

/** 创建节点（仅管理员，只在本项目范围内增补，不动全局模板） */
export async function createInfoNodeApi(projectId: string, payload: ApiInfoNodeCreate): Promise<ApiInfoNode> {
  return request()<ApiInfoNode>(`/info-nodes/projects/${encodeURIComponent(projectId)}`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

/** 增补信息（任何登录用户）：必须在允许增补的父节点下，只能加在本项目 */
export async function createCustomInfoNodeApi(projectId: string, payload: ApiInfoNodeCreate): Promise<ApiInfoNode> {
  return request()<ApiInfoNode>(`/info-nodes/projects/${encodeURIComponent(projectId)}/custom-nodes`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

/** 写入节点值（任何登录用户，值的归属项目由 project_id 指定） */
export async function setInfoNodeValueApi(
  nodeId: string,
  projectId: string,
  value: ApiInfoNodeValue,
): Promise<ApiInfoNode> {
  return request()<ApiInfoNode>(
    `/info-nodes/nodes/${encodeURIComponent(nodeId)}/value?project_id=${encodeURIComponent(projectId)}`,
    { method: 'PUT', body: JSON.stringify({ value }) },
  );
}

/** 更新节点定义（仅管理员；只对「本项目增补的节点」生效，全局字段请改详情模板） */
export async function updateInfoNodeApi(nodeId: string, updates: ApiInfoNodeUpdate): Promise<ApiInfoNode> {
  return request()<ApiInfoNode>(`/info-nodes/nodes/${encodeURIComponent(nodeId)}`, {
    method: 'PUT',
    body: JSON.stringify(updates),
  });
}

/** 移动节点（仅管理员，换父 + 排序位置）；newParentId 为 null 表示移到根 */
export async function moveInfoNodeApi(nodeId: string, newParentId: string | null, newSortOrder: number): Promise<ApiInfoNode> {
  return request()<ApiInfoNode>(`/info-nodes/nodes/${encodeURIComponent(nodeId)}/move`, {
    method: 'PATCH',
    body: JSON.stringify({ new_parent_id: newParentId, new_sort_order: newSortOrder }),
  });
}

/** 删除节点及其整棵子树（仅管理员；404 = 节点不存在，403 = 全局字段不可删） */
export async function deleteInfoNodeApi(nodeId: string): Promise<void> {
  await request()<{ detail?: string }>(`/info-nodes/nodes/${encodeURIComponent(nodeId)}`, { method: 'DELETE' });
}

/** 批量导入信息树（仅管理员；纯增补，只增不改不删）；返回新增节点数 */
export async function importInfoTreeApi(projectId: string, nodes: ApiInfoTreeImportNode[]): Promise<number> {
  const data = await request()<{ imported?: number }>(`/info-nodes/projects/${encodeURIComponent(projectId)}/import`, {
    method: 'POST',
    body: JSON.stringify({ nodes }),
  });
  return data?.imported ?? 0;
}

/** 一键清空（恢复为模板结构）的结果：清掉的内容数 + 删掉的增补节点数 */
export interface ApiResetInfoTreeResult {
  /** 留下来的全局字段（模板）上被清掉的内容数 */
  cleared: number;
  /** 删掉的本项目增补节点数（导入 / 同步 / 「增补信息」加进来的，含子孙） */
  nodesRemoved: number;
}

/** 一键清空：删掉本项目增补的节点、清掉全部已填值，恢复成模板的样子，返回两个计数。
 *  后端逐条记入编辑历史（delete + change_reason=一键清空），门槛与结构类接口相同（项目成员）。
 */
export async function resetProjectInfoTreeApi(projectId: string): Promise<ApiResetInfoTreeResult> {
  const data = await request()<{ cleared?: number; nodes_removed?: number }>(
    `/info-nodes/projects/${encodeURIComponent(projectId)}/reset-to-template`,
    { method: 'POST' },
  );
  return { cleared: data?.cleared ?? 0, nodesRemoved: data?.nodes_removed ?? 0 };
}

/** 按后端模板重建信息树 —— 已废弃。
 *  新结构下全局字段定义是所有项目共用的一份（project_info_node 里 project_id 为空的行），
 *  每个项目读树时自动带上，不存在「本项目缺字段需要补种」的情况，后端也已移除该接口。
 *  管理员改字段定义请走 /info-nodes/template（见下方 saveInfoTemplateApi）。
 */

// —— 编辑历史（节点操作记录）：后端每个节点操作都会落库，见 info_node_change_service ——

/** 一条节点操作记录（时间 / 人员 / 节点 / 具体变动） */
export interface ApiInfoNodeChange {
  id: string;
  node_id: string | null;
  parent_id: string | null;
  /** 操作时的节点标题快照（节点改名/删除后仍能看清当时是谁） */
  node_title: string;
  /** create / update / move / delete / import / sync */
  action: string;
  operator: string | null;
  /** 操作人显示名（识别不到用户时为 null） */
  operator_name: string | null;
  /** 具体变动的人话描述（服务端拼好，直接展示） */
  detail: string | null;
  created_at: string;
}

/** 某节点的编辑历史：自身操作 + 其直接子节点的删除记录（最新在前）。
 *  includeDescendants=true 时范围放大到整棵子树（一级标签的「修改记录」用，
 *  子节点被删的记录也在里面）；两种口径都由后端算好归属。 */
export async function fetchInfoNodeChangesApi(
  projectId: string,
  nodeId: string,
  options: { limit?: number; includeDescendants?: boolean } = {},
): Promise<ApiInfoNodeChange[]> {
  const query = new URLSearchParams({ node_id: nodeId, limit: String(options.limit ?? 100) });
  if (options.includeDescendants) query.set('include_descendants', 'true');
  const data = await request()<{ changes?: ApiInfoNodeChange[] }>(
    `/info-nodes/projects/${encodeURIComponent(projectId)}/changes?${query.toString()}`,
  );
  return Array.isArray(data?.changes) ? data.changes : [];
}

/** 各节点最新记录的 id {节点id: 记录id}，供「历史」红点判断新变动（与已读水位比相等） */
export async function fetchInfoNodeChangeSummaryApi(projectId: string): Promise<Record<string, string>> {
  const data = await request()<{ latest?: Record<string, string> }>(
    `/info-nodes/projects/${encodeURIComponent(projectId)}/changes/summary`,
  );
  const latest = data?.latest;
  return latest && typeof latest === 'object' ? latest : {};
}

// —— 关注（标注）与项目动态：星标 = 关注该节点，动态里展示其最新一条变动 ——

/** 获取当前登录人在某项目关注的节点 id 列表（关注按人隔离：自己关注的自己才看得到） */
export async function fetchInfoNodeMarksApi(projectId: string): Promise<string[]> {
  const data = await request()<{ node_ids?: string[] }>(
    `/info-nodes/projects/${encodeURIComponent(projectId)}/marks`,
  );
  return Array.isArray(data?.node_ids) ? data.node_ids : [];
}

/** 切换节点关注状态，返回切换后是否被关注（404 = 节点不存在）。
 *  projectId 必须传：全局节点各项目共用同一 node_id，后端推不出是哪个项目里关注的，
 *  不传的话新关注记不下来（只能取消已有的）。只有本项目增补节点能省略。 */
export async function toggleInfoNodeMarkApi(nodeId: string, projectId?: string): Promise<boolean> {
  const suffix = projectId ? `?project_id=${encodeURIComponent(projectId)}` : '';
  const data = await request()<{ marked?: boolean }>(
    `/info-nodes/nodes/${encodeURIComponent(nodeId)}/mark${suffix}`,
    { method: 'POST' },
  );
  return !!data?.marked;
}

/** 项目动态里的一条：某被关注节点的最新变动（只展示 detail，不带时间与人员） */
export interface ApiProjectActivityItem {
  node_id: string;
  /** 节点当前标题（改动改名后以最新为准） */
  node_title: string;
  /** 所在根节点标题，与 node_title 相同时前端不重复展示 */
  root_title: string;
  /** create / update / move / delete 等（前端暂不展示，留作后续按类型分组） */
  action: string;
  /** 变动内容的人话描述（服务端拼好，直接展示） */
  detail: string;
  /** 变动时间（仅排序/排查用，按需求不在动态里展示） */
  created_at: string;
}

/** 项目动态：每个被关注节点只返回最新一条变动，整体最新在前 */
export async function fetchProjectActivityApi(projectId: string): Promise<ApiProjectActivityItem[]> {
  const data = await request()<{ activity?: ApiProjectActivityItem[] }>(
    `/info-nodes/projects/${encodeURIComponent(projectId)}/activity`,
  );
  return Array.isArray(data?.activity) ? data.activity : [];
}

// —— 文件导入（AI 识别）：上传文档 → 后端调大模型识别 → 三类预览（不落库，确认后走上面的 CRUD） ——

/** 匹配到现有节点的识别条目（将填写 / 将覆盖共用） */
export interface ApiParseMatchedItem {
  node_id: string;
  /** 节点完整路径（如「基础信息 / 客户信息」），用于预览展示 */
  path: string;
  title: string;
  content_type: string;
  /** 节点当前内容（text 原值 / select 的 selected；空串=将填写） */
  current: string;
  /** 识别出的新内容（select 已对齐到可选项） */
  value: string;
}

/** 未匹配到节点的识别条目（确认后作为新节点创建） */
export interface ApiParseNewItem {
  title: string;
  value: string;
  /** 车型条目自带的数量（如「6 台」）：新建车型节点时补一个「数量」子节点填进去 */
  quantity?: string | null;
  /** 建议归属节点（后端已解析并校验层级）；null=前端用「导入信息」兜底 */
  suggested_parent_id: string | null;
  suggested_parent_path: string | null;
  /** 「为什么没匹配上」的说明（台账同步会带：如同名节点是下拉、可选项里没有这个值） */
  note?: string | null;
}

/** POST /info-nodes/projects/{id}/parse-file 返回 */
export interface ApiImportParseResult {
  file_name: string;
  /** 实际使用的模型（服务端 settings.LLM_MODEL_NAME，与摇人同一配置） */
  model: string;
  text_length: number;
  /** 正文超过服务端上限被截断时为 true */
  truncated: boolean;
  /** 大模型识别出的条目总数（含被去重的） */
  extracted: number;
  /** 当前系统内的项目名称 */
  project_name: string;
  /** 文件中识别到的项目名称；文件里没写则为 null */
  file_project_name: string | null;
  /** true = 两者确实不一致（后端判定），前端应提醒用户可能导错了文件 */
  name_mismatch: boolean;
  fill: ApiParseMatchedItem[];
  overwrite: ApiParseMatchedItem[];
  unmatched: ApiParseNewItem[];
}

/** 上传支持的文件（正文抽取与大模型识别都在后端完成），返回三类预览；本接口不写库 */
export async function parseImportFileApi(projectId: string, file: File): Promise<ApiImportParseResult> {
  const form = new FormData();
  form.append('file', file);
  const data = await request()<ApiImportParseResult>(
    `/info-nodes/projects/${encodeURIComponent(projectId)}/parse-file`,
    // 大模型识别耗时可能超过默认 30s，单独放宽超时（后端 LLM 调用上限 120s）
    { method: 'POST', body: form, timeout: 180000 },
  );
  return {
    file_name: data?.file_name ?? file.name,
    model: data?.model ?? '',
    text_length: data?.text_length ?? 0,
    truncated: !!data?.truncated,
    extracted: data?.extracted ?? 0,
    project_name: data?.project_name ?? '',
    file_project_name: typeof data?.file_project_name === 'string' && data.file_project_name
      ? data.file_project_name
      : null,
    name_mismatch: !!data?.name_mismatch,
    fill: Array.isArray(data?.fill) ? data.fill : [],
    overwrite: Array.isArray(data?.overwrite) ? data.overwrite : [],
    unmatched: Array.isArray(data?.unmatched) ? data.unmatched : [],
  };
}

// —— 企业微信台账同步：后端读本地台账镜像比对 → 三类预览（不落库，确认后走上面的 CRUD） ——

/** GET /info-nodes/projects/{id}/ledger-sync 返回（三组结构与文件识别完全一致） */
export interface ApiLedgerSyncResult {
  project_id: string;
  /** 当前系统内的项目名称 */
  project_name: string;
  project_code: string;
  /** 台账「更新时间」列（本项目在台账里没这一列时为 null） */
  ledger_updated_at: string | null;
  /** 参与比对的字段数（本项目有值的台账列，不含「项目名称」这个定位列） */
  field_count: number;
  /** 台账镜像的列总数，用于说明「台账还有多少列本项目没值」 */
  mirror_field_total: number;
  fill: ApiParseMatchedItem[];
  overwrite: ApiParseMatchedItem[];
  unmatched: ApiParseNewItem[];
}

/** 拉取台账同步预览（只读不写）：项目不存在时 404，项目还没有信息节点时 400 */
export async function fetchLedgerSyncPreviewApi(projectId: string): Promise<ApiLedgerSyncResult> {
  let data: ApiLedgerSyncResult;
  try {
    data = await request()<ApiLedgerSyncResult>(
      `/info-nodes/projects/${encodeURIComponent(projectId)}/ledger-sync`,
    );
  } catch (err) {
    // 这条路由的 404 本该只出现在「项目不存在」且带中文原因；FastAPI 默认的 "Not Found"
    // 只可能是**后端没有这条路由**——后端还在跑旧代码（改了路由没重启后端），
    // 或前端比后端先发版。直接把 "Not Found" 摆给用户没人看得懂，换成能照着做的提示。
    if (err instanceof ApiError && err.statusCode === 404 && /^not\s*found$/i.test(err.message.trim())) {
      throw new Error('后端没有「台账同步」接口（404）——后端可能还在跑旧代码，请重启后端后再试');
    }
    throw err;
  }
  return {
    project_id: data?.project_id ?? projectId,
    project_name: data?.project_name ?? '',
    project_code: data?.project_code ?? '',
    ledger_updated_at: typeof data?.ledger_updated_at === 'string' && data.ledger_updated_at
      ? data.ledger_updated_at
      : null,
    field_count: data?.field_count ?? 0,
    mirror_field_total: data?.mirror_field_total ?? 0,
    fill: Array.isArray(data?.fill) ? data.fill : [],
    overwrite: Array.isArray(data?.overwrite) ? data.overwrite : [],
    unmatched: Array.isArray(data?.unmatched) ? data.unmatched : [],
  };
}

// —— 项目详情模板（仅管理员）：编辑模板 → 保存并同步到所有项目的节点 ——

/** 模板节点（递归树；id 是节点身份的稳定 UUID，改名不换 id，历史与关注不断线） */
export interface ApiInfoTemplateNode {
  id: string;
  title: string;
  content_type: string;
  /** 仅 select 节点：可选项 */
  options?: string[];
  sort_order?: number;
  /** 该字段是否必填（当前仅透传保存，前端暂不强制校验） */
  required?: boolean;
  /** 恒为 true：所有节点都允许各项目在其下「增补信息」（2026-09-18 取消逐节点开关） */
  allow_custom?: boolean;
  children?: ApiInfoTemplateNode[];
}

export interface ApiInfoTemplate {
  id: string;
  name: string;
  nodes: ApiInfoTemplateNode[];
  updated_at: string | null;
  /** 最近编辑人（首次补种为 system） */
  updated_by: string | null;
  /** 会受同步影响的项目数（未删除项目总数） */
  project_count: number;
  /** db=已入库；yaml=首次访问由默认模板补种 */
  source: string;
}

/** dry-run 预览 / 真实同步共用的统计与明细 */
export interface ApiInfoTemplateSyncResult {
  dry_run: boolean;
  /** 未删除项目总数 */
  projects: number;
  /** 实际会（或已）变更的项目数 */
  changed_projects: number;
  added: number;
  updated: number;
  deleted: number;
  /** 有变更项目的明细（后端最多给 20 条） */
  details?: Array<{ project_id: string; project_name: string; added: number; updated: number; deleted: number }>;
  /** 同步失败的项目（单个项目失败不拖垮整体） */
  failed_projects?: Array<{ project_id: string; error: string }>;
  updated_at?: string;
  updated_by?: string;
}

/** 获取项目详情模板（仅管理员；后端首次访问会用默认模板补种入库） */
export async function fetchInfoTemplateApi(): Promise<ApiInfoTemplate> {
  const data = await request()<ApiInfoTemplate>('/info-nodes/template');
  return {
    id: data?.id ?? 'default',
    name: data?.name ?? '项目详情模板',
    nodes: Array.isArray(data?.nodes) ? data.nodes : [],
    updated_at: data?.updated_at ?? null,
    updated_by: data?.updated_by ?? null,
    project_count: data?.project_count ?? 0,
    source: data?.source ?? 'db',
  };
}

/** 保存模板并同步到所有项目；dryRun=true 只预览影响不写库（校验失败后端返回 400） */
export async function saveInfoTemplateApi(
  nodes: ApiInfoTemplateNode[],
  dryRun: boolean,
): Promise<ApiInfoTemplateSyncResult> {
  const data = await request()<ApiInfoTemplateSyncResult>('/info-nodes/template', {
    method: 'POST',
    body: JSON.stringify({ nodes, dry_run: dryRun }),
  });
  return {
    dry_run: !!data?.dry_run,
    projects: data?.projects ?? 0,
    changed_projects: data?.changed_projects ?? 0,
    added: data?.added ?? 0,
    updated: data?.updated ?? 0,
    deleted: data?.deleted ?? 0,
    details: Array.isArray(data?.details) ? data.details : [],
    failed_projects: Array.isArray(data?.failed_projects) ? data.failed_projects : [],
    updated_at: data?.updated_at,
    updated_by: data?.updated_by,
  };
}
