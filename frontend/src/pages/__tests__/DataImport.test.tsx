// 数据导入页 · 二维码导入（第三 Tab）：微信扫码（QRV1 单码 / QRV2 分片收集）与
// 非微信环境手动粘贴兜底，提交 POST /data/import-qrcode 后展示结果卡。
//
// 用例都从界面出发断言（点 Tab → 选项目 → 扫码/粘贴 → 校验请求体与结果卡），
// 不直接测内部函数；wx.scanQRCode 与后端请求均以 mock 替代。
import type { ReactNode } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';

const mockRequest = vi.fn();

vi.mock('@/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/client')>()),
  createRequest: () => mockRequest,
}));

vi.mock('@/shared/utils/wechatJsSdk', () => ({
  initWechatJsSdk: vi.fn(async () => true),
}));

vi.mock('tdesign-mobile-react', () => ({
  Toast: vi.fn(),
  Loading: () => null,
  Popup: ({ children, visible }: { children?: ReactNode; visible?: boolean }) =>
    visible ? <div data-testid="popup">{children}</div> : null,
}));

vi.mock('react-router-dom', () => ({ useNavigate: () => vi.fn() }));

import DataImport from '../admin/DataImport';
import { useAuthStore } from '@/stores/auth';
import { Toast } from 'tdesign-mobile-react';
import { initWechatJsSdk } from '@/shared/utils/wechatJsSdk';

// 真 auth store：permissions 含 admin 即「能看全部项目」，与线上 hasPermission 同一套判定
useAuthStore.setState({ username: 'admin', permissions: ['admin'] });

const PROJECTS = [{ id: 'P-0001', project_code: 'P-0001', name: '演示项目' }];

// 前端只校验 QRV1| 前缀，不解码内容（解码在后端），样例文本足够驱动流程
const QRV1_PAYLOAD = 'QRV1|H4sIAAAAAAAAA6tWKkktLlFQslIqS8wpTdVRKs5JLEkFAAAAAA==';

const IMPORT_OK = {
  success: true,
  message: '二维码数据导入成功',
  project: 'P-0001',
  indicator: 'GroupEfficiency',
  chunk_count: 1,
  chunks: [{ start_time: '2026-10-20 00:00:00', end_time: '2026-10-20 23:59:59' }],
};

// 捕获 wx.scanQRCode 收到的参数，测试里手动触发 success 回调（等价于真实扫到码）
interface ScanOpts {
  needResult: 1;
  scanType: string[];
  success: (res: { resultStr?: string }) => void;
}
let scanQueue: ScanOpts[] = [];

beforeEach(() => {
  vi.clearAllMocks();
  scanQueue = [];
  // window.wx 上挂 scanQRCode：initWechatJsSdk 被 mock 成恒成功，组件随后取用该 API
  (window as unknown as { wx?: { scanQRCode?: (opts: ScanOpts) => void } }).wx = {
    scanQRCode: (opts: ScanOpts) => {
      scanQueue.push(opts);
    },
  };
  mockRequest.mockImplementation(async (url: string) => {
    if (url === '/projects/' || url === '/projects/me') return PROJECTS;
    if (url === '/data/import-qrcode') return IMPORT_OK;
    throw new Error(`未预期的请求: ${url}`);
  });
});

/** 打开项目选择弹层并选中「演示项目」（等价于用户在 Popup 上点选） */
const pickProject = async () => {
  await waitFor(() => expect(screen.getByText('请选择项目')).toBeTruthy());
  fireEvent.click(screen.getByText('请选择项目'));
  fireEvent.click(screen.getByText('演示项目'));
};

/** 点「扫描二维码」并让微信扫码回调返回 text（等价于真实扫到一张码） */
const scanAndReturn = async (text: string) => {
  const btn = screen.getByRole('button', { name: '扫描二维码' });
  // 上一次扫描的 finally 可能还没解除按钮 disabled，等可用再点，避免点击被吞
  await waitFor(() => expect((btn as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(btn);
  const before = scanQueue.length;
  await waitFor(() => expect(scanQueue.length).toBe(before + 1));
  await act(async () => {
    scanQueue[scanQueue.length - 1].success({ resultStr: text });
  });
};

/** 取提交给 /data/import-qrcode 的请求体 */
const importedBody = () => {
  const call = mockRequest.mock.calls.find(([url]) => url === '/data/import-qrcode');
  return call ? JSON.parse((call[1] as { body: string }).body) : null;
};

describe('数据导入页 · 二维码导入', () => {
  it('三个导入 Tab 并列，二维码 Tab 内有扫码入口与手动粘贴兜底', async () => {
    render(<DataImport />);
    // 等项目列表异步加载完成（useEffect 里的 GET /projects/），避免 act 警告
    await waitFor(() => expect(screen.getByText('请选择项目')).toBeTruthy());
    expect(screen.getByRole('button', { name: '文件导入' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'JSON导入' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '二维码导入' }));
    expect(screen.getByRole('button', { name: '扫描二维码' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '无法扫码？手动粘贴二维码内容' })).toBeTruthy();
  });

  it('未选择项目时点扫码只提示，不调起微信扫码', async () => {
    render(<DataImport />);
    fireEvent.click(screen.getByRole('button', { name: '二维码导入' }));
    fireEvent.click(screen.getByRole('button', { name: '扫描二维码' }));

    await waitFor(() => expect(vi.mocked(Toast)).toHaveBeenCalled());
    expect(vi.mocked(Toast).mock.calls[0][0]).toMatchObject({
      message: '请先选择项目',
      theme: 'warning',
    });
    expect(initWechatJsSdk).not.toHaveBeenCalled();
  });

  it('微信扫码得到 QRV1 单码：直接提交导入，成功后展示结果卡', async () => {
    render(<DataImport />);
    await pickProject();
    fireEvent.click(screen.getByRole('button', { name: '二维码导入' }));

    await scanAndReturn(QRV1_PAYLOAD);

    await waitFor(() => expect(importedBody()).toBeTruthy());
    expect(importedBody()).toMatchObject({
      project: 'P-0001',
      payload: QRV1_PAYLOAD,
      fragments: null,
    });

    await waitFor(() => expect(screen.getByText(/二维码数据导入成功/)).toBeTruthy());
    // 结果卡信息行：指标标签与导入条目数（文件名称/大小无值时不展示）
    expect(screen.getByText('指标标签')).toBeTruthy();
    expect(screen.getByText('GroupEfficiency')).toBeTruthy();
    expect(screen.getByText('导入条目数')).toBeTruthy();
    expect(screen.getByText('1')).toBeTruthy();
  });

  it('QRV2 分片逐片扫码：显示收集进度，集齐后自动提交全部片段', async () => {
    render(<DataImport />);
    await pickProject();
    fireEvent.click(screen.getByRole('button', { name: '二维码导入' }));

    // 第 1 片：显示收集进度，未集齐不发请求
    await scanAndReturn('QRV2|1/2|QRV1|AAAA');
    await waitFor(() => expect(screen.getByText(/已收集 1\/2 片/)).toBeTruthy());
    expect(mockRequest.mock.calls.some(([url]) => url === '/data/import-qrcode')).toBe(false);

    // 第 2 片：集齐自动提交（按片号排序的 fragments）
    await scanAndReturn('QRV2|2/2|BBBB');
    await waitFor(() => expect(importedBody()).toBeTruthy());
    const body = importedBody();
    expect(body.payload).toBeNull();
    expect(body.fragments).toEqual(['QRV2|1/2|QRV1|AAAA', 'QRV2|2/2|BBBB']);
  });

  it('非微信环境兜底：手动粘贴单码导入', async () => {
    render(<DataImport />);
    await pickProject();
    fireEvent.click(screen.getByRole('button', { name: '二维码导入' }));

    fireEvent.click(screen.getByRole('button', { name: '无法扫码？手动粘贴二维码内容' }));
    fireEvent.change(screen.getByPlaceholderText(/粘贴扫码得到的文本/), {
      target: { value: QRV1_PAYLOAD },
    });
    fireEvent.click(screen.getByRole('button', { name: '导入粘贴内容' }));

    await waitFor(() => expect(importedBody()).toBeTruthy());
    expect(importedBody()).toMatchObject({
      project: 'P-0001',
      payload: QRV1_PAYLOAD,
      fragments: null,
    });
  });

  it('后端解码失败时展示错误卡，不谎报成功', async () => {
    mockRequest.mockImplementation(async (url: string) => {
      if (url === '/projects/' || url === '/projects/me') return PROJECTS;
      if (url === '/data/import-qrcode') {
        return { success: false, message: '二维码数据解压失败' };
      }
      throw new Error(`未预期的请求: ${url}`);
    });
    render(<DataImport />);
    await pickProject();
    fireEvent.click(screen.getByRole('button', { name: '二维码导入' }));

    await scanAndReturn(QRV1_PAYLOAD);

    await waitFor(() => expect(screen.getByText('二维码数据解压失败')).toBeTruthy());
    expect(screen.queryByText(/二维码数据导入成功/)).toBeNull();
  });
});
