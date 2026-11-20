import { beforeEach, describe, expect, it } from 'vitest';

const KEY = 'ors:tasks-list-view';

describe('tasks view preference storage contract', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('未存过时视为关系列表；写入平铺后可读回', () => {
    expect(localStorage.getItem(KEY)).toBeNull();
    localStorage.setItem(KEY, 'flat');
    expect(localStorage.getItem(KEY)).toBe('flat');
    localStorage.setItem(KEY, 'relation');
    expect(localStorage.getItem(KEY)).toBe('relation');
  });
});
