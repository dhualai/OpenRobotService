/**
 * 工单 API —— /api/tasks/*
 *
 * - 催办：POST /cuiban-notification（现成，TicketDetailPage handleUrge 已用）
 * - 评论：GET/POST /{ticket_id}/comments（现成，评论绑定 ticket_id）
 * - 附件：POST /comments/attachments（temp_id 关联，MinIO 存储，发评论时一并入库）
 * - 创建：POST /（系统任务创建，兜底双工单场景下工单2 直接指定 assigned_to=项目负责人）
 */
import { createRequest, getToken } from '@/api/client';
import API_CONFIG from '@/config/api';
import { readStored } from '@/stores/authStorage';

const request = createRequest(API_CONFIG.TASKS.BASE_URL, '工单服务');

export interface TicketComment {
  id: number;
  ticket_id: number;
  content: string;
  is_public: boolean;
  /** 附件列表（后端存的是 object_path 字符串数组，如 ["bucket/temp/file.png"]） */
  attachments?: unknown[];
  created_by: string;
  created_by_name?: string;
  created_at: string;
  updated_at: string;
}

/** 工单类型（与后端 TicketType 枚举对齐） */
export type TicketType = 'problem' | 'bug' | 'feature' | 'support' | 'other';
/** 工单优先级（与后端 TicketPriority 枚举对齐） */
export type TicketPriority = 'low' | 'medium' | 'high' | 'urgent';

export interface CreateTicketParams {
  title: string;
  description: string;
  ticket_type?: TicketType;
  priority?: TicketPriority;
  project_name?: string;
  project_id?: string;
  assigned_to?: string;       // 接单人 users.id（过渡期后端也认 username）
  deadline_at?: string;       // 最晚解决时间（ISO 字符串，兜底双工单工单2 复用弹窗选择值）
  customer?: string;
  metadata_info?: Record<string, unknown>;
  tags?: string[];
  /** 附件列表：字符串为 object_path；dict 为 {path, object_path, filename} 结构（远程截图等）。
   *  与 tasks.attachments 列约定对齐——详情页读 path，AI 路径去重读 object_path。 */
  attachments?: Array<string | { path?: string; object_path?: string; filename?: string; [k: string]: unknown }>;
  /** 代他人提单：被代理人 users.id（留空=普通自提单）。
   *  后端会二次校验其为在职用户；成功后建立 pending 代提关系并通知对方。 */
  on_behalf_of?: string;
  /** 初始协商节点 ID（留空=后端按 ticket_type 自动取模板第一个） */
  curr_step_id?: number;
  /** 初始协商节点截止时间（ISO 字符串，留空=后端兜底 deadline_at 或 +7 天） */
  curr_step_endtime?: string;
}

export interface CreatedTicket {
  id: number;
  title: string;
  assigned_to?: string | null;
  [k: string]: unknown;
}

/** 一键催办：通知指定用户 */
export const urgeTicket = (ticketId: number | string, assignedTo: string) =>
  request('/cuiban-notification', {
    method: 'POST',
    body: JSON.stringify({ ticket_id: Number(ticketId), notify_type: 1, assigned_to: assignedTo }),
  });

/** 上报：通知指定用户 + 管理员 */
export const reportTicket = (ticketId: number | string, assignedTo: string) =>
  request('/cuiban-notification', {
    method: 'POST',
    body: JSON.stringify({ ticket_id: Number(ticketId), notify_type: 1, assigned_to: assignedTo, to_admin: true }),
  });

/** 撤回：将工单状态置为已取消（Canceled）
 *  后端 PATCH /{task_id}/status 的 status 参数用 Body(..., embed=True) 声明，
 *  因此请求体必须是 JSON 对象 { status: "canceled" }（不能是裸字符串），否则 FastAPI 报 422 Field required。 */
export const cancelTicket = (ticketId: number | string) =>
  request(`/${Number(ticketId)}/status`, {
    method: 'PATCH',
    body: JSON.stringify({ status: 'canceled' }),
  });

/** 重新派单：强制工单回到待派单状态并触发 AI 智能派单重新推荐处理人。
 *  preferredAssignee 为用户倾向的派单人（users.id，必填）；remark 为可选备注。 */
export const reDispatchTicket = (
  ticketId: number | string,
  preferredAssignee: string,
  remark?: string,
) =>
  request(`/${Number(ticketId)}/re-dispatch`, {
    method: 'POST',
    body: JSON.stringify({ preferred_assignee: preferredAssignee, remark: remark || null }),
  });

// ── 代他人提单（代理提单）：见 docs/PRODUCT/代他人提单（代理提单）功能设计方案.md ──

/** 代理关系状态（与后端 3 态状态机对齐；无接手 / 无 revoke） */
export type ProxyRelationStatus = 'pending' | 'acknowledged' | 'declined';

/** 代提选人候选项：group 由后端显式下发，前端**不靠顺序猜** */
export interface OnBehalfCandidate {
  id: string;
  username: string;
  name?: string | null;
  group: 'project' | 'all';
}

/** 代理关系（详情页横幅 / 列表胶囊用） */
export interface ProxyRelation {
  id: number;
  task_id: number;
  relation_status: ProxyRelationStatus;
  source?: string | null;
  remark?: string | null;
  /** 当前登录用户视角，后端下发，前端不自行拼身份判定 */
  is_agent: boolean;
  is_principal: boolean;
  /** 当前登录用户是否为本单接单人（接单人视角：可见「谁代谁提单」） */
  is_assignee: boolean;
  agent_name?: string | null;
  principal_name?: string | null;
  notified_at?: string | null;
  acked_at?: string | null;
  declined_at?: string | null;
  created_at?: string | null;
}

/** 代提选人候选：同项目在前、其他在职在后（分组标记由后端返回） */
export const getOnBehalfCandidates = (params?: { project_id?: string; keyword?: string }) => {
  const qs = new URLSearchParams();
  if (params?.project_id) qs.set('project_id', params.project_id);
  if (params?.keyword) qs.set('keyword', params.keyword);
  const suffix = qs.toString() ? `?${qs.toString()}` : '';
  return request(`/on-behalf-candidates${suffix}`, { method: 'GET' }) as Promise<OnBehalfCandidate[]>;
};

/** 「待我跟进」角标计数（被代理人、pending 且工单未终结） */
export const getMyFollowupsCount = () =>
  request('/my-followups/count', { method: 'GET' }) as Promise<{ count: number }>;

/** 查询工单的代理关系（非参与人后端返回 403 或空数组） */
export const getProxyRelations = (ticketId: number | string) =>
  request(`/${Number(ticketId)}/proxy-relations`, { method: 'GET' }) as Promise<ProxyRelation[]>;

/** 被代理人确认跟进（pending → acknowledged，获协办权） */
export const ackProxyRelation = (ticketId: number | string, relationId: number) =>
  request(`/${Number(ticketId)}/proxy-relations/${relationId}/ack`, {
    method: 'POST',
    body: JSON.stringify({}),
  }) as Promise<ProxyRelation>;

/** 被代理人拒绝（与我无关，需填原因；工单不中断，代理人兜底推进） */
export const declineProxyRelation = (
  ticketId: number | string,
  relationId: number,
  remark: string,
) =>
  request(`/${Number(ticketId)}/proxy-relations/${relationId}/decline`, {
    method: 'POST',
    body: JSON.stringify({ remark }),
  }) as Promise<ProxyRelation>;

// ── 二次派单感知增强（M2）：详情 redispatch 子对象类型 + 读取 ──
export interface RedispatchCandidate {
  rank: number;
  engineer_id: string;
  name: string;
  department?: string | null;
  job_level?: number | null;
  modules?: string[] | null;
  duty?: string | null;
  // 画像缺失英文字段（department/job_level/responsibility_modules），供前端权威判定"待补充画像"
  missing?: string[] | null;
  scores?: { llm?: number; semantic?: number; history?: number; total?: number } | null;
  tags?: string[] | null;
}

export interface RedispatchProfile {
  dept?: string | null;
  job_level?: number | null;
  modules?: string[] | null;
  duty?: string | null;
  missing?: string[] | null;
  specified_name?: string | null;
}

export interface RedispatchResult {
  assigned_id?: string;
  assigned_name?: string;
  preferred_id?: string | null;
  preferred_name?: string | null;
  confidence?: number | null;
  decision_type?: string | null;
  reasoning?: string | null;
  profile?: RedispatchProfile | null;
  matched_pref?: boolean | null;
  name_collision?: boolean | null;
  pinyin_match?: boolean | null;
  tip_detail?: string | null;
}

export interface TicketRedispatch {
  dispatch_round?: number;
  candidates?: RedispatchCandidate[] | null;
  result?: RedispatchResult | null;
}

/** 读取工单详情的 redispatch 子对象（R2 候选快照 + R3 结果信息），无记录返回 null */
export const fetchRedispatch = (ticketId: number | string) =>
  request<{ redispatch?: TicketRedispatch | null }>(`/${Number(ticketId)}`).then((r) => {
    return r?.redispatch ?? null;
  });

/** 评论列表（按工单绑定） */
export const listComments = (ticketId: number | string) =>
  request<TicketComment[]>(`/${Number(ticketId)}/comments`);

/** 发评论；attachments 为附件上传时用的 temp_id 数组，后端 add_comment 会换成真实路径入库 */
export const addComment = (
  ticketId: number | string,
  content: string,
  attachments: string[] = [],
  isPublic = true,
) =>
  request<TicketComment>(`/${Number(ticketId)}/comments`, {
    method: 'POST',
    body: JSON.stringify({ content, is_public: isPublic, attachments }),
  });

/** 上传评论附件（FormData；temp_id 用于随后发评论时关联）。鉴权带 Bearer token
 *  返回 MinIO 上的真实 object_path（如 "helpdesk/temp/xxx.png"），
 *  前端建单时可直接透传给 createTicket 的 attachments 字段，无需依赖后端进程内存 temp_id 映射。 */
export const uploadCommentAttachment = async (file: File, tempId: string): Promise<string> => {
  const formData = new FormData();
  formData.append('file', file);
  formData.append('temp_id', tempId);
  // 优先取 client.ts 内存 token，其次环境命名空间 localStorage；确保与 createRequest 使用同一个 token
  const token = getToken() || readStored('AUTH_TOKEN') || '';
  const res = await fetch(`${API_CONFIG.TASKS.BASE_URL}/comments/attachments`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}` },
    body: formData,
  });
  if (!res.ok) throw new Error(`附件上传失败: ${res.status}`);
  const data = (await res.json()) as { object_path?: string };
  return data.object_path || '';
};

/** 创建系统任务（工单）。返回创建后的工单（含 id）。
 *  用途：兜底双工单场景下，工单2（申请单）走系统任务创建接口，直接指定 assigned_to=项目负责人。
 *  依赖后端改动：create_ticket 尊重 ticket_data.assigned_to（不再硬编码 created_by）。 */
export async function createTicket(params: CreateTicketParams): Promise<CreatedTicket> {
  return request<CreatedTicket>('/', {
    method: 'POST',
    body: JSON.stringify({
      title: params.title,
      description: params.description,
      ticket_type: params.ticket_type ?? 'support',
      priority: params.priority ?? 'medium',
      project_name: params.project_name ?? '',
      project_id: params.project_id ?? '',
      assigned_to: params.assigned_to ?? null,
      deadline_at: params.deadline_at ?? null,
      customer: params.customer ?? null,
      metadata_info: params.metadata_info ?? null,
      tags: params.tags ?? null,
      attachments: params.attachments ?? null,
      curr_step_id: params.curr_step_id ?? null,
      curr_step_endtime: params.curr_step_endtime ?? null,
    }),
  });
}

/** 工单操作日志类型（与后端 OperationType 枚举对齐） */
export type OperationType =
  | 'create'
  | 'status_change'
  | 'assign'
  | 'escalate'
  | 'return'
  | 'reassign'
  | 'update'
  | 'comment'
  | 'view'
  | 'ai_diagnose'
  | 'ai_assign';

export interface OperationLog {
  id: number;
  task_id: number;
  operation_type: OperationType;
  operator: string;
  operator_name?: string | null;
  to_status?: string | null;
  detail?: Record<string, unknown> | null;
  description?: string | null;
  created_at: string;
  ended_at?: string | null;
  duration_seconds?: number | null;
}

/** 获取工单操作日志列表（按时间倒序） */
export const getOperationLogs = (taskId: number | string) =>
  request<OperationLog[]>(`/${Number(taskId)}/operation-logs`, { skipCache: true });

/** 将秒数格式化为人类可读的停留时长，如 "5 分 30 秒" / "45 秒" / "1 小时 5 分" */
export const formatDuration = (seconds: number | null | undefined): string => {
  if (!seconds || seconds <= 0) return '';
  const s = Math.floor(seconds);
  if (s < 60) return `${s} 秒`;
  const h = Math.floor((s / 3600));
  const m = Math.floor((s % 3600) / 60);
  const rest = s % 60;
  if (h > 0) return rest > 0 ? `${h} 小时 ${m} 分` : `${h} 小时 ${m} 分`;
  return rest > 0 ? `${m} 分 ${rest} 秒` : `${m} 分钟`;
};

// ── 工单关联（task_relations）类型与 API ──

/** 关系类型（与后端 RelationType 枚举对齐） */
export type RelationType = 'predecessor' | 'duplicate' | 'subtask';

/** 关联工单概要（target/source 侧） */
export interface RelationBrief {
  id: number;
  title: string;
  status: string;
  created_by_name?: string | null;
  assigned_to_name?: string | null;
}

/** 工单关联响应 */
export interface TaskRelation {
  id: number;
  source_task_id: number;
  target_task_id: number;
  relation_type: RelationType;
  created_by?: string | null;
  created_at: string;
  target?: RelationBrief | null;
  source?: RelationBrief | null;
}

/** 阻塞工单信息（状态变更 422 错误返回） */
export interface BlockedTaskInfo {
  task_id: number;
  title: string;
  status: string;
  reason: 'predecessor' | 'subtask';
}

/** 422 阻塞错误体 */
export interface BlockedErrorDetail {
  code: 'blocked_by_related_tasks';
  message: string;
  blocked: BlockedTaskInfo[];
}

/** 获取工单所有关联（双向） */
export const listRelations = (taskId: number | string) =>
  request<TaskRelation[]>(`/${Number(taskId)}/relations`);

/** 创建工单关联 */
export const createRelation = (
  taskId: number | string,
  targetTaskId: number,
  relationType: RelationType,
) =>
  request<TaskRelation>(`/${Number(taskId)}/relations`, {
    method: 'POST',
    body: JSON.stringify({ target_task_id: targetTaskId, relation_type: relationType }),
  });

/** 删除工单关联 */
export const deleteRelation = (taskId: number | string, relationId: number) =>
  request(`/${Number(taskId)}/relations/${relationId}`, { method: 'DELETE' });

// ── 关系树（树形渲染用） ──

/** 关系树节点 */
export interface RelationTreeNode {
  id: number;
  title: string;
  status: string;
  created_by_name?: string | null;
  assigned_to_name?: string | null;
}

/** 关系树边 */
export interface RelationTreeEdge {
  source: number;
  target: number;
  relation_type: RelationType;
}

/** 关系树响应 */
export interface RelationTreeResponse {
  root_id: number;       // 渲染根节点（subtask 树最顶层父工单）
  current_id: number;     // 用户实际打开的工单
  nodes: RelationTreeNode[];
  edges: RelationTreeEdge[];
}

/** 获取工单完整关系树 */
export const getRelationTree = (taskId: number | string, maxDepth = 8) =>
  request<RelationTreeResponse>(`/${Number(taskId)}/relations/tree?max_depth=${maxDepth}`);
