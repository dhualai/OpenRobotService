import { describe, expect, it } from 'vitest';
import {
  buildTasksListCacheKey,
  readTasksListCache,
  writeTasksListCache,
} from '../tasksListCache';

describe('tasksListCache', () => {
  it('同 key 可读回，异 key 读不到', () => {
    const key = buildTasksListCacheKey({ page: 1, relevance: 'global' });
    writeTasksListCache({
      key,
      tickets: [{ id: 1 }],
      total: 1,
      relatedTickets: [],
    });
    expect(readTasksListCache(key)?.tickets).toEqual([{ id: 1 }]);
    expect(readTasksListCache(buildTasksListCacheKey({ page: 2, relevance: 'global' }))).toBeNull();
  });
});
