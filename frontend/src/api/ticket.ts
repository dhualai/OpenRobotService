// 系统任务（工单）API —— 对接 backend POST /api/tasks/（TicketCreate）
// 用途：兜底双工单场景下，工单2（申请单）走系统任务创建接口，直接指定 assigned_to=项目负责人。
// 依赖后端改动：create_ticket 尊重 ticket_data.assigned_to（张文星改，不再硬编码 created_by）。
import { createRequest } from './client';
import API_CONFIG from '@/config/api';

const request = createRequest(API_CONFIG.TASKS.BASE_URL, '工单服务');

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
  assigned_to?: string;       // 接单人 username（后端改后生效）
  customer?: string;
  metadata_info?: Record<string, unknown>;
  tags?: string[];
}

export interface CreatedTicket {
  id: number;
  title: string;
  assigned_to?: string | null;
  [k: string]: unknown;
}

/** 创建系统任务（工单）。返回创建后的工单（含 id）。 */
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
      customer: params.customer ?? null,
      metadata_info: params.metadata_info ?? null,
      tags: params.tags ?? null,
    }),
  });
}
