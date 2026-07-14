// 列表数据归一化：后端列表接口可能返回裸数组，也可能返回分页/包装对象。
// 统一从这里取数组，避免组件里 `xxx.map is not a function`。
const LIST_KEYS = ['items', 'data', 'list', 'results', 'records'] as const;

export function normalizeList<T = unknown>(data: unknown): T[] {
  if (Array.isArray(data)) return data as T[];
  if (data && typeof data === 'object') {
    const obj = data as Record<string, unknown>;
    for (const key of LIST_KEYS) {
      if (Array.isArray(obj[key])) return obj[key] as T[];
    }
  }
  return [];
}
