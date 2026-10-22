import type { ReactNode } from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '@/api/profile';
import { useAuthStore } from '@/stores/auth';
import UserProfile from '../admin/UserProfile';

vi.mock('tdesign-mobile-react', () => ({
  Input: ({ value, placeholder }: { value?: string; placeholder?: string }) => <input readOnly value={value ?? ''} placeholder={placeholder} />,
  Button: ({ children, disabled, onClick }: { children: ReactNode; disabled?: boolean; onClick?: () => void }) => <button disabled={disabled} onClick={onClick}>{children}</button>,
  Dialog: vi.fn(), Toast: vi.fn(), Popup: () => null,
}));
vi.mock('@/shared/components/ClearableInput', () => ({ default: () => <input /> }));
vi.mock('@/shared/components/FilterableSelect', () => ({ default: () => null }));
vi.mock('@/shared/utils/wechatJsSdk', () => ({ setupWechatShare: vi.fn() }));
vi.mock('@/api/profile', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/api/profile')>(),
  getMyProfile: vi.fn(), getProfileOptions: vi.fn(), getMyTimezone: vi.fn(),
  generateUspUsername: vi.fn(), detectTimezone: vi.fn(),
}));

beforeEach(() => {
  vi.clearAllMocks();
  useAuthStore.setState({ isLoggedIn: true, username: 'profile-viewer', name: '个人用户', avatarResourceId: null, permissions: ['user'], isAdmin: false });
  vi.mocked(api.getMyProfile).mockResolvedValue({ id: 'profile-viewer', username: 'profile-viewer',
    name: '个人用户', status: 'active', external_credentials: { usp: { username: 'profile-viewer', password: '-' } } });
  vi.mocked(api.getProfileOptions).mockResolvedValue({ companies: [], departments_by_company: {}, my_pending: { companies: [], departments: [] } });
  vi.mocked(api.generateUspUsername).mockResolvedValue('profile-viewer');
  vi.mocked(api.detectTimezone).mockReturnValue('Asia/Shanghai');
});

describe('个人中心通知时区来源', () => {
  it.each([
    { source: null, label: '未设置（按系统默认 Asia/Shanghai）' },
    { source: '', label: '未设置（按系统默认 Asia/Shanghai）' },
    { source: 'unknown', label: '未设置（按系统默认 Asia/Shanghai）' },
    { source: 'browser', label: '设备自动上报' },
    { source: 'manual', label: '手动设置' },
    { source: 'system', label: '系统默认' },
  ])('来源为$source时显示$label', async ({ source, label }) => {
    vi.mocked(api.getMyTimezone).mockResolvedValue({ timezone: null, locked: false, source,
      updated_at: null, system_default: 'Asia/Shanghai' });
    render(<MemoryRouter><UserProfile /></MemoryRouter>);
    await waitFor(() => expect(screen.getByRole('button', { name: '保存' })).not.toBeDisabled());
    expect(screen.getByText(`来源：${label}`)).toBeInTheDocument();
    expect(api.getMyTimezone).toHaveBeenCalledOnce();
  });

  it('获取时区失败时仍展示未设置提示且不阻止个人信息加载', async () => {
    vi.mocked(api.getMyTimezone).mockRejectedValue(new Error('时区暂不可用'));
    render(<MemoryRouter><UserProfile /></MemoryRouter>);
    await waitFor(() => expect(screen.getByRole('button', { name: '保存' })).not.toBeDisabled());
    expect(screen.getByText('来源：未设置（按系统默认 Asia/Shanghai）')).toBeInTheDocument();
  });
});
