import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import type { QrcodeItem, QrcodeListResult } from '@/api/qrcode';

const api = vi.hoisted(() => ({
  fetchQrcodes: vi.fn(), fetchAllQrcodes: vi.fn(), fetchQrcodeStats: vi.fn(),
  createQrcode: vi.fn(), batchCreateQrcodes: vi.fn(), generateQrcodeTicket: vi.fn(),
  batchGenerateTickets: vi.fn(), updateQrcode: vi.fn(), qrcodeTransition: vi.fn(), deleteQrcode: vi.fn(),
  toast: vi.fn(),
}));
vi.mock('@/api/qrcode', () => ({ ...api, QRCODE_STATUS_LABELS: {
  init: { label: '初始化' }, entering: { label: '录入中' }, published: { label: '已发布' }, deprecated: { label: '已弃用' },
} }));
vi.mock('tdesign-mobile-react', () => ({
  Navbar: ({ title }: { title: string }) => <div>{title}</div>,
  Loading: ({ text }: { text: string }) => <div>{text}</div>,
  Popup: ({ visible, children }: { visible: boolean; children: ReactNode }) => visible ? <div>{children}</div> : null,
  Toast: api.toast,
}));
vi.mock('../components/QrcodeDownloadPopup', () => ({
  default: ({ filters }: { filters: object }) => <div data-testid="download-filters">{JSON.stringify(filters)}</div>,
}));

import QrcodeManage from '../QrcodeManage';

const rows: QrcodeItem[] = Array.from({ length: 42 }, (_, index) => ({
  id: index + 1, scene_str: String(index + 1), name: `二维码${index + 1}`,
  status: 'published', type: 'permanent', ticket: `ticket-${index + 1}`,
}));

function openPage() {
  return render(<MemoryRouter><QrcodeManage /></MemoryRouter>);
}

async function loaded() {
  await screen.findByLabelText('选择二维码 1');
}

describe('二维码列表操作', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    api.fetchQrcodes.mockImplementation(async ({ skip = 0, limit = 20 }) => ({ total: rows.length, items: rows.slice(skip, skip + limit) }));
    api.fetchAllQrcodes.mockResolvedValue({ total: rows.length, items: rows });
    api.fetchQrcodeStats.mockResolvedValue({ total: rows.length, status: {}, type: {}, permanent_quota_remaining: 99958 });
  });

  it('一键全选与取消当前页，部分勾选时全选会补齐', async () => {
    openPage();
    await loaded();
    fireEvent.click(screen.getByText('一键全选本页'));
    expect(screen.getAllByRole('checkbox')).toHaveLength(20);
    screen.getAllByRole('checkbox').forEach((checkbox) => expect(checkbox).toBeChecked());
    fireEvent.click(screen.getByLabelText('选择二维码 1'));
    expect(screen.getByText('显示 20 / 42 条 · 已选 19 条')).toBeVisible();
    fireEvent.click(screen.getByText('一键全选本页'));
    expect(screen.getByLabelText('选择二维码 1')).toBeChecked();
    fireEvent.click(screen.getByText('取消本页全选'));
    screen.getAllByRole('checkbox').forEach((checkbox) => expect(checkbox).not.toBeChecked());
  });

  it('本页全选和取消保留其他页已选，清空已选清除跨页勾选', async () => {
    openPage();
    await loaded();
    fireEvent.click(screen.getByText('一键全选本页'));
    fireEvent.click(screen.getByText('下一页'));
    await screen.findByLabelText('选择二维码 21');
    fireEvent.click(screen.getByText('一键全选本页'));
    expect(screen.getByText('显示 20 / 42 条 · 已选 40 条')).toBeVisible();
    fireEvent.click(screen.getByText('取消本页全选'));
    expect(screen.getByText('显示 20 / 42 条 · 已选 20 条')).toBeVisible();
    fireEvent.click(screen.getByText('清空已选'));
    expect(screen.getByText('显示 20 / 42 条 · 已选 0 条')).toBeVisible();
  });

  it('一页显示全部筛选记录、全选全部并恢复分页', async () => {
    openPage();
    await loaded();
    fireEvent.click(screen.getByText('一页显示全部（42 条）'));
    await screen.findByLabelText('选择二维码 42');
    expect(screen.getAllByRole('checkbox')).toHaveLength(42);
    expect(screen.queryByText('下一页')).not.toBeInTheDocument();
    fireEvent.click(screen.getByText('一键全选全部'));
    expect(screen.getByText('显示 42 / 42 条 · 已选 42 条')).toBeVisible();
    fireEvent.click(screen.getByText('取消全选'));
    expect(screen.getByText('显示 42 / 42 条 · 已选 0 条')).toBeVisible();
    fireEvent.click(screen.getByText('恢复分页（每页 20 条）'));
    await waitFor(() => expect(screen.getAllByRole('checkbox')).toHaveLength(20));
    expect(screen.getByText('下一页')).toBeVisible();
    expect(api.fetchQrcodes).toHaveBeenLastCalledWith(expect.objectContaining({ skip: 0, limit: 20 }));
  });

  it('应用日期筛选重置分页，下载沿用日期，清除后恢复', async () => {
    openPage();
    await loaded();
    fireEvent.click(screen.getByText('下一页'));
    await screen.findByLabelText('选择二维码 21');
    fireEvent.click(screen.getByText('日期筛选'));
    fireEvent.change(screen.getByLabelText('日期类型（北京时间）'), { target: { value: 'ticket_created_at' } });
    fireEvent.change(screen.getByLabelText('开始日期（包含）'), { target: { value: '2026-10-27' } });
    fireEvent.change(screen.getByLabelText('结束日期（包含）'), { target: { value: '2026-10-28' } });
    fireEvent.click(screen.getByText('按日期筛选'));
    await loaded();
    expect(api.fetchQrcodes).toHaveBeenLastCalledWith(expect.objectContaining({
      skip: 0, date_field: 'ticket_created_at', date_start: '2026-10-27', date_end: '2026-10-28',
    }));
    fireEvent.click(screen.getByText('批量下载'));
    expect(JSON.parse(screen.getByTestId('download-filters').textContent!)).toEqual({
      date_field: 'ticket_created_at', date_start: '2026-10-27', date_end: '2026-10-28',
    });
    fireEvent.click(screen.getByText('清除日期'));
    await waitFor(() => expect(screen.getByText('日期筛选')).toBeVisible());
    expect(api.fetchQrcodes).toHaveBeenLastCalledWith({ status: undefined, qrcode_type: undefined, keyword: undefined, skip: 0, limit: 20 });
  });

  it('日期倒置不给后端发请求，支持单边日期与全部显示模式', async () => {
    openPage();
    await loaded();
    fireEvent.click(screen.getByText('一页显示全部（42 条）'));
    await screen.findByLabelText('选择二维码 42');
    fireEvent.click(screen.getByText('日期筛选'));
    fireEvent.change(screen.getByLabelText('开始日期（包含）'), { target: { value: '2026-10-28' } });
    fireEvent.change(screen.getByLabelText('结束日期（包含）'), { target: { value: '2026-10-27' } });
    fireEvent.click(screen.getByText('按日期筛选'));
    expect(api.toast).toHaveBeenCalledWith({ message: '开始日期不能晚于结束日期' });
    expect(api.fetchAllQrcodes).toHaveBeenCalledOnce();
    fireEvent.change(screen.getByLabelText('结束日期（包含）'), { target: { value: '' } });
    fireEvent.click(screen.getByText('按日期筛选'));
    await waitFor(() => expect(api.fetchAllQrcodes).toHaveBeenCalledTimes(2));
    expect(api.fetchAllQrcodes).toHaveBeenLastCalledWith(expect.objectContaining({ date_start: '2026-10-28', date_end: undefined }), expect.any(Function));
  });

  it('快速切换筛选时旧请求不能覆盖新结果', async () => {
    let resolveOld!: (result: QrcodeListResult) => void;
    api.fetchQrcodes.mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }))
      .mockResolvedValueOnce({ total: 1, items: [rows[1]] });
    openPage();
    fireEvent.change(screen.getByPlaceholderText('搜索名称 / ID'), { target: { value: '二维码2' } });
    await screen.findByLabelText('选择二维码 2');
    await act(async () => resolveOld({ total: 42, items: rows.slice(0, 20) }));
    expect(screen.queryByLabelText('选择二维码 1')).not.toBeInTheDocument();
    expect(screen.getAllByRole('checkbox')).toHaveLength(1);
  });

  it('加载失败不保留旧筛选数据，禁用空列表全选', async () => {
    openPage();
    await loaded();
    api.fetchAllQrcodes.mockRejectedValue(new Error('全部加载失败'));
    fireEvent.click(screen.getByText('一页显示全部（42 条）'));
    await screen.findByText('暂无二维码记录');
    expect(screen.getByText('一键全选全部')).toBeDisabled();
    expect(api.toast).toHaveBeenCalledWith({ message: '全部加载失败' });
  });
});
