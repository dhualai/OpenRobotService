// 责任双树 API —— 对接 /api/admin/resp-trees|resp-nodes|resp-links
// 后端：backend/app/modules/admin/api/resp_tree.py；DB 为唯一真相源，写带 expected_revision → 409。
import { ApiError, createRequest } from './client';
import API_CONFIG from '@/config/api';

const request = createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin');

function unwrap<T>(raw: unknown): T {
  if (raw && typeof raw === 'object' && 'data' in raw) {
    return (raw as { data: T }).data;
  }
  return raw as T;
}

export class RespTreeConflictError extends Error {
  statusCode = 409;
  constructor(message = '树已被他人修改') {
    super(message);
    this.name = 'RespTreeConflictError';
  }
}

function rethrow(err: unknown): never {
  if (err instanceof ApiError && err.statusCode === 409) {
    throw new RespTreeConflictError(err.message || '树已被他人修改');
  }
  throw err;
}

export type RespTreeKind = 'org' | 'skill';
export type RespNodeKind = 'umbrella' | 'module' | 'leaf' | 'org' | 'func';
export type RespLinkRole = 'leader' | 'owner' | 'specialist' | 'member';

export interface RespTreeMeta {
  id: number;
  kind: RespTreeKind;
  name: string;
  status: string;
  revision: number;
}

export interface RespNodeDto {
  id: number;
  tree_id: number;
  parent_id: number | null;
  name: string;
  description: string;
  keywords: string[];
  anchor: string;
  owners: string[];
  linked_node_id: number | null;
  node_kind: RespNodeKind | string;
  org_kind: string | null;
  vehicle_models: string;
  sort_order: number;
}

export interface RespLinkDto {
  user_id: string;
  node_id: number;
  link_type: string;
  role: RespLinkRole | string;
  source: string;
}

export interface RespTreePayload {
  tree: RespTreeMeta;
  nodes: RespNodeDto[];
  links: RespLinkDto[];
}

export async function seedRespTrees(): Promise<{ trees: RespTreeMeta[] }> {
  try {
    return unwrap(await request('/resp-trees/seed-defaults', { method: 'POST', body: '{}' }));
  } catch (e) {
    rethrow(e);
  }
}

export async function listRespTrees(kind?: RespTreeKind): Promise<RespTreeMeta[]> {
  try {
    const q = kind ? `?kind=${kind}` : '';
    return unwrap(await request(`/resp-trees${q}`, { skipCache: true }));
  } catch (e) {
    rethrow(e);
  }
}

export async function getRespTreeNodes(treeId: number): Promise<RespTreePayload> {
  try {
    return unwrap(await request(`/resp-trees/${treeId}/nodes`, { skipCache: true }));
  } catch (e) {
    rethrow(e);
  }
}

export interface CreateRespNodeBody {
  tree_id: number;
  name: string;
  parent_id?: number | null;
  description?: string;
  node_kind?: string;
  org_kind?: string | null;
  vehicle_models?: string;
  linked_node_id?: number | null;
  sort_order?: number;
  expected_revision?: number;
}

export async function createRespNode(
  body: CreateRespNodeBody,
): Promise<{ node: RespNodeDto; revision: number }> {
  try {
    return unwrap(await request('/resp-nodes', {
      method: 'POST',
      body: JSON.stringify(body),
    }));
  } catch (e) {
    rethrow(e);
  }
}

export async function patchRespNode(
  nodeId: number,
  body: Record<string, unknown> & { expected_revision?: number },
): Promise<{ node: RespNodeDto; revision: number }> {
  try {
    return unwrap(await request(`/resp-nodes/${nodeId}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    }));
  } catch (e) {
    rethrow(e);
  }
}

export async function deleteRespNode(
  nodeId: number,
  expectedRevision?: number,
): Promise<{ deleted: number[]; revision: number }> {
  try {
    const q = expectedRevision == null ? '' : `?expected_revision=${expectedRevision}`;
    return unwrap(await request(`/resp-nodes/${nodeId}${q}`, { method: 'DELETE' }));
  } catch (e) {
    rethrow(e);
  }
}

export async function putRespLink(body: {
  user_id: string;
  node_id: number;
  role?: string;
  link_type?: string;
  source?: string;
  expected_revision?: number;
}): Promise<{ link: RespLinkDto; revision: number }> {
  try {
    return unwrap(await request('/resp-links', {
      method: 'PUT',
      body: JSON.stringify(body),
    }));
  } catch (e) {
    rethrow(e);
  }
}

export async function deleteRespLink(
  nodeId: number,
  userId: string,
  expectedRevision?: number,
): Promise<{ revision: number }> {
  try {
    const params = new URLSearchParams({
      node_id: String(nodeId),
      user_id: userId,
    });
    if (expectedRevision != null) params.set('expected_revision', String(expectedRevision));
    return unwrap(await request(`/resp-links?${params}`, { method: 'DELETE' }));
  } catch (e) {
    rethrow(e);
  }
}

/** 试做页 JSON（domain/org/skill）整份灌库；force=true 清空后重灌 */
export async function importTrialSnapshot(body: {
  nodes: unknown[];
  mounts: unknown[];
  force?: boolean;
}): Promise<{
  skill_tree_id: number;
  org_tree_id: number;
  skill_revision: number;
  org_revision: number;
  domains: number;
  skills: number;
  orgs: number;
  mounts: number;
}> {
  try {
    return unwrap(await request('/resp-trees/import-trial', {
      method: 'POST',
      body: JSON.stringify(body),
    }));
  } catch (e) {
    rethrow(e);
  }
}
