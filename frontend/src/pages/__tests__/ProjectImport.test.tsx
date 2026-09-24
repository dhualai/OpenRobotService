// 项目管理「项目导入」区的一键导入按钮：可见性（仅管理员/超级管理员）、
// 二次确认、结果提示文案、失败提示。
//
// 判据与后端 get_current_admin_user 对齐：permissions 含 'admin'（与项目工单卡
// 「配置阻滞权重」同一套），所以这里 mock 的 auth store 只提供 permissions。
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { act, render, screen, fireEvent, waitFor } from '@testing-library/react';
import { Toast } from 'tdesign-mobile-react';
import ProjectImport from '../admin/ProjectImport';
import { importAllProjectsLedgerApi, type ApiImportAllResult } from '@/api/infoNodes';

vi.mock('@/api/infoNodes', () => ({
  importAllProjectsLedgerApi: vi.fn(),
}));

const authState = vi.hoisted(() => ({ permissions: ['admin'] as string[] }));
vi.mock('@/stores/auth', () => ({
  useAuthStore: (selector: (s: { permissions: string[] }) => unknown) =>
    selector({ permissions: authState.permissions }),
}));

// Dialog.confirm 在真机上是弹层；测试里把 onConfirm 抓出来手动触发（与用户点「开始导入」等价）
const dialogState = vi.hoisted(() => ({ confirm: vi.fn() }));
vi.mock('tdesign-mobile-react', () => ({
  Toast: vi.fn(),
  Dialog: { confirm: dialogState.confirm },
}));

vi.mock('react-router-dom', () => ({ useNavigate: () => vi.fn() }));

const RESULT: ApiImportAllResult = {
  project_total: 297,
  project_written: 295,
  project_no_change: 2,
  project_skipped: 0,
  project_failed: 0,
  filled: 1787,
  overwritten: 0,
  unmatched: 2119,
  failures: [],
  duration_ms: 11800,
};

const clickImport = () => fireEvent.click(screen.getByRole('button', { name: '一键导入所有项目节点内容' }));

/** 点确认弹层的「开始导入」（等价于用户在弹层上点确认） */
const confirmImport = async () => {
  const options = dialogState.confirm.mock.calls[0][0] as { onConfirm: () => void };
  await act(async () => { options.onConfirm(); });
};

describe('项目管理 · 一键导入所有项目节点内容', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    authState.permissions = ['admin'];
  });

  it('管理员可见按钮；普通用户看不到（后端闸门同判据，这里不给点了就报错的入口）', () => {
    const { unmount } = render(<ProjectImport />);
    expect(screen.getByRole('button', { name: '一键导入所有项目节点内容' })).toBeTruthy();
    unmount();

    authState.permissions = ['frontend:admin:project-detail:show'];
    render(<ProjectImport />);
    expect(screen.queryByRole('button', { name: '一键导入所有项目节点内容' })).toBeNull();
  });

  it('点击先二次确认（说明会覆盖、不建不删节点），确认后才发请求，成功给汇总提示', async () => {
    vi.mocked(importAllProjectsLedgerApi).mockResolvedValue(RESULT);
    render(<ProjectImport />);

    clickImport();
    // 还没确认前不发请求——写的是全体项目的数据，误触代价大
    expect(importAllProjectsLedgerApi).not.toHaveBeenCalled();
    expect(dialogState.confirm).toHaveBeenCalledTimes(1);
    const options = dialogState.confirm.mock.calls[0][0] as {
      title: string; content: string; confirmBtn?: string; onConfirm: () => void;
    };
    expect(options.title).toBe('一键导入所有项目节点内容');
    expect(options.content).toContain('覆盖');
    expect(options.content).toContain('不会新建或删除节点');
    expect(options.confirmBtn).toBe('开始导入');

    await act(async () => { options.onConfirm(); });
    await waitFor(() => expect(importAllProjectsLedgerApi).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(vi.mocked(Toast)).toHaveBeenCalled());
    expect(vi.mocked(Toast).mock.calls[0][0]).toMatchObject({
      message: '已处理 297 个项目，新填 1787 项',
      theme: 'success',
    });
  });

  it('全部一致时不说「新填 0 项」，改说没有需要写入的内容', async () => {
    vi.mocked(importAllProjectsLedgerApi).mockResolvedValue({
      ...RESULT, project_written: 0, filled: 0, overwritten: 0, unmatched: 2119,
    });
    render(<ProjectImport />);

    clickImport();
    await confirmImport();

    await waitFor(() => expect(vi.mocked(Toast)).toHaveBeenCalled());
    expect((vi.mocked(Toast).mock.calls[0]![0] as { message: string }).message).toContain('没有需要写入的内容');
  });

  it('有项目失败时补一条提示（带首个失败项目与原因）', async () => {
    vi.mocked(importAllProjectsLedgerApi).mockResolvedValue({
      ...RESULT, project_failed: 1, project_skipped: 3,
      failures: [{ project_id: '9', project_name: '样例项目', reason: '基础信息 / 销售：写入被拒绝' }],
    });
    render(<ProjectImport />);

    clickImport();
    await confirmImport();

    await waitFor(() => expect(vi.mocked(Toast)).toHaveBeenCalledTimes(2));
    const messages = vi.mocked(Toast).mock.calls.map((call) => (call[0] as { message: string }).message);
    expect(messages[0]).toContain('3 个项目还没有信息节点，已跳过');
    expect(messages[1]).toContain('1 个项目没导成功');
    expect(messages[1]).toContain('样例项目');
    expect(messages[1]).toContain('写入被拒绝');
  });

  it('请求失败（如不是管理员、后端超时）给错误提示，不谎报成功', async () => {
    vi.mocked(importAllProjectsLedgerApi).mockRejectedValue(new Error('权限不足'));
    render(<ProjectImport />);

    clickImport();
    await confirmImport();

    await waitFor(() => expect(vi.mocked(Toast)).toHaveBeenCalled());
    expect(vi.mocked(Toast).mock.calls[0][0]).toMatchObject({
      message: '导入失败：权限不足',
      theme: 'error',
    });
  });
});
