// 项目列表 API —— 对接 AI 服务 GET /api/ai/wecom/projects（企业微信 Smartsheet 拉取的全量项目）
// 该接口是工单绑定项目的权威数据源（与转工单二次确认绑定项目一致）。
// 返回结构：{ code, data: { total, records: [{ record_id, values: {项目编号, 项目名称, 项目生命周期, 是否承接, ...} }] } }
// 字段映射（与 backend 的 wecom adapter 一致）：项目编号→project_code、项目名称→name、是否承接=“是”才可作为绑定项。
import { createRequest } from './client';
import API_CONFIG from '@/config/api';

export interface ProjectItem {
  id: string;
  project_code: string; // 项目业务编码（作为工单 project_id 绑定）
  name: string;
  status?: string;
}

interface WecomProjectRecord {
  record_id: string;
  values: Record<string, unknown>;
}

interface WecomProjectsResponse {
  code: number;
  message?: string;
  data?: { total: number; records: WecomProjectRecord[] };
}

// 把企业微信拍扁记录映射为 ProjectItem；非“承接”项目返回 null（不参与工单绑定）
function toProjectItem(r: WecomProjectRecord): ProjectItem | null {
  const v = r.values || {};
  if (v['是否承接'] !== '是') return null;
  const project_code = String(v['项目编号'] ?? r.record_id);
  const name = String(v['项目名称'] ?? '');
  const status = v['项目生命周期'] ? String(v['项目生命周期']) : undefined;
  return { id: r.record_id, project_code, name, status };
}

// 拉取全部可绑定项目（已过滤“是否承接=是”），模糊搜索由 ProjectSelect 客户端完成
export async function getProjects(): Promise<ProjectItem[]> {
  const request = createRequest(API_CONFIG.AI.BASE_URL, '项目服务');
  const resp = await request<WecomProjectsResponse>('/wecom/projects');
  if (resp.code !== 0) {
    throw new Error(resp.message || '获取项目列表失败');
  }
  const records = resp.data?.records || [];
  return records
    .map(toProjectItem)
    .filter((p): p is ProjectItem => p !== null);
}
