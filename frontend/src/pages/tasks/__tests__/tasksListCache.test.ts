import { describe, expect, it } from 'vitest';
import {
  buildTasksListCacheKeyFromState,
  readTasksListCache,
  writeTasksListCache,
} from '../tasksListCache';

const base = {
  search: '',
  statusFilter: ['pending'],
  priorityFilter: ['medium'],
  typeFilter: ['problem'],
  relevanceFilter: 'global',
  projectFilter: [] as string[],
  assigneeFilter: '',
  creatorFilter: '',
  createdStart: '',
  createdEnd: '',
  resolvedStart: '',
  resolvedEnd: '',
  closedStart: '',
  closedEnd: '',
  page: 1,
  sortBy: 'priority',
  sortOrder: 'desc',
  view: 'relation',
  user: 'u1',
  projects: ['b', 'a'],
  canViewAll: true,
};

describe('tasksListCache', () => {
  it('同 key 可读回，异 key 读不到；projects 排序不影响 key', () => {
    const key = buildTasksListCacheKeyFromState(base);
    writeTasksListCache({
      key,
      tickets: [{ id: 1 }],
      total: 1,
      relatedTickets: [],
    });
    expect(readTasksListCache(key)?.tickets).toEqual([{ id: 1 }]);
    expect(readTasksListCache(buildTasksListCacheKeyFromState({ ...base, page: 2 }))).toBeNull();
    expect(buildTasksListCacheKeyFromState({ ...base, projects: ['a', 'b'] })).toBe(key);
  });
});
