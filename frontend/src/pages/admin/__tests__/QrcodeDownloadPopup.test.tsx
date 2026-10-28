import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';

const api = vi.hoisted(() => ({ downloadQrcodes: vi.fn(), toast: vi.fn() }));
vi.mock('@/api/qrcode', () => ({ downloadQrcodes: api.downloadQrcodes }));
vi.mock('tdesign-mobile-react', () => ({
  Popup: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  Toast: api.toast,
}));

import QrcodeDownloadPopup from '../components/QrcodeDownloadPopup';

const onClose = vi.fn();
const filters = { status: 'published' as const, qrcode_type: 'permanent' as const, keyword: '项目',
  date_start: '2026-10-27', date_end: '2026-10-28', date_field: 'created_at' as const };

function open(selectedIds: number[] = [1, 25]) {
  return render(<QrcodeDownloadPopup selectedIds={selectedIds} pageIds={[1, 2]} filteredTotal={42}
    filters={filters} onClose={onClose} />);
}

describe('二维码批量下载', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    api.downloadQrcodes.mockResolvedValue(new Blob(['zip']));
    vi.stubGlobal('URL', class extends URL {
      static createObjectURL = vi.fn(() => 'blob:zip');
      static revokeObjectURL = vi.fn();
    });
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  });
  afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

  it('默认下载所有跨页勾选记录，触发 ZIP 下载并关闭', async () => {
    open();
    fireEvent.click(screen.getByText('下载 ZIP'));
    await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
    expect(api.downloadQrcodes).toHaveBeenCalledWith({ scope: 'selected', qid_list: [1, 25] });
    expect(URL.createObjectURL).toHaveBeenCalledOnce();
    expect(HTMLAnchorElement.prototype.click).toHaveBeenCalledOnce();
  });

  it('没有勾选时默认下载完整筛选结果，不传分页', async () => {
    open([]);
    expect(screen.getByLabelText('下载范围')).toHaveValue('filtered');
    fireEvent.click(screen.getByText('下载 ZIP'));
    await waitFor(() => expect(api.downloadQrcodes).toHaveBeenCalledWith({ scope: 'filtered', ...filters }));
  });

  it.each([
    ['page', { scope: 'page', qid_list: [1, 2] }],
    ['all', { scope: 'all' }],
  ])('选择 %s 时不混入当前筛选或其他页勾选', async (scope, params) => {
    open();
    fireEvent.change(screen.getByLabelText('下载范围'), { target: { value: scope } });
    fireEvent.click(screen.getByText('下载 ZIP'));
    await waitFor(() => expect(api.downloadQrcodes).toHaveBeenCalledWith(params));
  });

  it('ID 区间校验后按包含两端的范围下载', async () => {
    open();
    fireEvent.change(screen.getByLabelText('下载范围'), { target: { value: 'range' } });
    fireEvent.click(screen.getByText('下载 ZIP'));
    expect(api.downloadQrcodes).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText('起始 ID（包含）'), { target: { value: '20' } });
    fireEvent.change(screen.getByLabelText('结束 ID（包含）'), { target: { value: '10' } });
    fireEvent.click(screen.getByText('下载 ZIP'));
    expect(api.downloadQrcodes).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText('结束 ID（包含）'), { target: { value: '30' } });
    fireEvent.click(screen.getByText('下载 ZIP'));
    await waitFor(() => expect(api.downloadQrcodes).toHaveBeenCalledWith({ scope: 'range', id_start: 20, id_end: 30 }));
  });

  it('打包期间禁用重复提交、范围修改和关闭', async () => {
    api.downloadQrcodes.mockReturnValue(new Promise(() => {}));
    open();
    fireEvent.click(screen.getByText('下载 ZIP'));
    expect(screen.getByText('正在打包，请稍候…')).toBeDisabled();
    expect(screen.getByLabelText('下载范围')).toBeDisabled();
    expect(screen.getByText('取消')).toBeDisabled();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('服务端失败时展示原因并保留弹窗以便重试', async () => {
    api.downloadQrcodes.mockRejectedValue(new Error('单次最多下载 500 条，请缩小下载范围'));
    open();
    fireEvent.click(screen.getByText('下载 ZIP'));
    await waitFor(() => expect(api.toast).toHaveBeenCalledWith({ message: '单次最多下载 500 条，请缩小下载范围' }));
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByText('下载 ZIP')).toBeEnabled();
    expect(URL.createObjectURL).not.toHaveBeenCalled();
  });
});
