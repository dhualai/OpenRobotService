import { beforeEach, describe, expect, it } from 'vitest';
import {
  TASKS_LIST_SCROLL_KEY,
  clearTasksListScroll,
  isTaskDetailPath,
  isTasksListPath,
  readTasksListScroll,
  saveTasksListScroll,
  tasksScrollAction,
} from '../tasksListScroll';

describe('tasksListScroll paths', () => {
  it('识别列表与详情路径', () => {
    expect(isTasksListPath('/tasks')).toBe(true);
    expect(isTasksListPath('/tasks/')).toBe(true);
    expect(isTasksListPath('/tasks/12')).toBe(false);
    expect(isTaskDetailPath('/tasks/12')).toBe(true);
    expect(isTaskDetailPath('/tasks/12/operations')).toBe(false);
    expect(isTaskDetailPath('/tasks')).toBe(false);
  });

  it('列表→详情保存，详情→列表恢复，其它清零', () => {
    expect(tasksScrollAction('/tasks', '/tasks/99')).toBe('save');
    expect(tasksScrollAction('/tasks/99', '/tasks')).toBe('restore');
    expect(tasksScrollAction('/tasks', '/call')).toBe('reset');
    expect(tasksScrollAction('/call', '/tasks')).toBe('reset');
  });

  it('同路径首屏与详情子页往返不清掉已存滚动', () => {
    expect(tasksScrollAction('/tasks/99', '/tasks/99')).toBe('keep');
    expect(tasksScrollAction('/tasks/99', '/tasks/99/operations')).toBe('keep');
    expect(tasksScrollAction('/tasks/99/operations', '/tasks/99')).toBe('keep');
    expect(tasksScrollAction('/tasks/99/operations', '/tasks')).toBe('restore');
  });
});

describe('tasksListScroll storage', () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  it('读写四舍五入非负滚动值', () => {
    saveTasksListScroll(320.6);
    expect(sessionStorage.getItem(TASKS_LIST_SCROLL_KEY)).toBe('321');
    expect(readTasksListScroll()).toBe(321);
  });

  it('清空后读回 null', () => {
    saveTasksListScroll(10);
    clearTasksListScroll();
    expect(readTasksListScroll()).toBeNull();
  });

  it('非法值视为 null', () => {
    sessionStorage.setItem(TASKS_LIST_SCROLL_KEY, 'nope');
    expect(readTasksListScroll()).toBeNull();
  });
});
