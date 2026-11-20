import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import ProjectActivityCard from '../admin/ProjectActivityCard';
import { fetchProjectActivityApi, type ApiProjectActivityItem } from '@/api/infoNodes';

// 动态卡的数据源是后端聚合接口：mock 出固定条目，验证渲染/空态/失败重试
vi.mock('@/api/infoNodes', () => ({
  fetchProjectActivityApi: vi.fn(),
  fetchInfoTree: vi.fn(),
  createInfoNodeApi: vi.fn(),
  updateInfoNodeApi: vi.fn(),
  moveInfoNodeApi: vi.fn(),
  deleteInfoNodeApi: vi.fn(),
  importInfoTreeApi: vi.fn(),
  fetchInfoNodeMarksApi: vi.fn(),
  toggleInfoNodeMarkApi: vi.fn(),
}));

const item = (partial: Partial<ApiProjectActivityItem> & { node_id: string }): ApiProjectActivityItem => ({
  node_title: '节点',
  root_title: '节点',
  action: 'update',
  detail: '',
  created_at: '2026-09-16 10:00:00',
  ...partial,
});

describe('ProjectActivityCard（项目动态卡）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('展示被关注节点的最新变动：节点名 + 变动内容，不出现时间与人员', async () => {
    vi.mocked(fetchProjectActivityApi).mockResolvedValue([
      item({
        node_id: 'n1', node_title: '客户信息', root_title: '基础信息',
        detail: '把内容从「空」改为「中力」', created_at: '2026-09-16 10:00:00',
      }),
      item({ node_id: 'n2', node_title: '载具类型', root_title: '载具类型', detail: '新建节点「载具类型」' }),
    ]);
    render(<ProjectActivityCard projectId="CODE-1" />);

    expect(fetchProjectActivityApi).toHaveBeenCalledWith('CODE-1');
    expect(await screen.findByText('基础信息 · 客户信息')).toBeTruthy();
    expect(screen.getByText('把内容从「空」改为「中力」')).toBeTruthy();
    // 节点名与根同名时不重复拼前缀
    expect(screen.getByText('载具类型')).toBeTruthy();
    expect(screen.getByText('新建节点「载具类型」')).toBeTruthy();
    // 只展示变动内容：不渲染时间/操作人（接口返回里也没有人员）
    expect(screen.queryByText(/2026-09-16/)).toBeNull();
  });

  it('没有变动时给出空态引导（去信息卡点星标关注）', async () => {
    vi.mocked(fetchProjectActivityApi).mockResolvedValue([]);
    render(<ProjectActivityCard projectId="CODE-1" />);
    expect(await screen.findByText(/暂无变动/)).toBeTruthy();
  });

  it('加载失败显示重试，点击后重新拉取；reloadToken 变化也会重新拉取', async () => {
    vi.mocked(fetchProjectActivityApi).mockRejectedValueOnce(new Error('boom'));
    const { rerender } = render(<ProjectActivityCard projectId="CODE-1" />);
    expect(await screen.findByText('项目动态加载失败')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: '重新加载' }));
    await waitFor(() => expect(fetchProjectActivityApi).toHaveBeenCalledTimes(2));
    expect(await screen.findByText(/暂无变动/)).toBeTruthy();

    // 星标变化后外层翻动 reloadToken → 重新拉取
    rerender(<ProjectActivityCard projectId="CODE-1" reloadToken={1} />);
    await waitFor(() => expect(fetchProjectActivityApi).toHaveBeenCalledTimes(3));
  });
});
