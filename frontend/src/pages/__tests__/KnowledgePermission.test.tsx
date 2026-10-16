import type { ReactNode } from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { Toast } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import PermissionManage from '../admin/PermissionManage';
import RoleManage from '../admin/RoleManage';

vi.mock('@/api/client', () => ({ createRequest: vi.fn() }));
vi.mock('tdesign-mobile-react', () => ({
  Toast: vi.fn(), Loading: ({ text }: { text: string }) => <div>{text}</div>,
  Dialog: { confirm: vi.fn() },
  Popup: ({ visible, children }: { visible: boolean; children: ReactNode }) => visible ? <div>{children}</div> : null,
}));

const permission = { id: 'perm_frontend_admin_knowledge_show', code: 'frontend:admin:knowledge:show',
  name: '显示知识贡献榜', resource_type: 'frontend', action: 'show', enabled: true };
const role = { id: 'field-engineer', name: '现场工程师', role_type: 'project' };
const request = vi.fn();

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(createRequest).mockReturnValue(request);
  request.mockImplementation(async (endpoint: string, options?: { method?: string }) => {
    if (options?.method) return {};
    if (endpoint === '/permissions/') return [permission];
    if (endpoint === '/roles/') return [role];
    if (endpoint === `/roles/${role.id}/all-permissions`) return [];
    throw new Error(`Unexpected endpoint: ${endpoint}`);
  });
});

describe('知识贡献榜卡片权限配置', () => {
  it('权限管理沿用原权限卡片和开关，能够禁用知识榜显示', async () => {
    render(<PermissionManage />);
    expect(await screen.findByText('显示知识贡献榜')).toBeInTheDocument();
    expect(screen.getByText(permission.code)).toBeInTheDocument();
    const toggle = screen.getByRole('switch');
    expect(toggle.closest('.mac-perm-card')).not.toBeNull();
    expect(toggle).toHaveAttribute('aria-checked', 'true');
    fireEvent.click(toggle);
    await waitFor(() => expect(request).toHaveBeenCalledWith(`/permissions/${permission.id}`,
      { method: 'PUT', body: JSON.stringify({ enabled: false }) }));
    expect(toggle).toHaveAttribute('aria-checked', 'false');
  });

  it('权限开关保存失败时不伪造已关闭状态', async () => {
    render(<PermissionManage />);
    const toggle = await screen.findByRole('switch');
    request.mockRejectedValueOnce(new Error('没有修改权限'));
    fireEvent.click(toggle);
    await waitFor(() => expect(Toast).toHaveBeenCalledWith(expect.objectContaining({ theme: 'error' })));
    expect(toggle).toHaveAttribute('aria-checked', 'true');
  });

  it('可搜索知识贡献榜权限并重新启用', async () => {
    request.mockResolvedValueOnce([{ ...permission, enabled: false }]);
    render(<PermissionManage />);
    await screen.findByText(permission.name);
    fireEvent.change(screen.getByPlaceholderText('搜索权限名称或编码'), { target: { value: '知识贡献榜' } });
    const toggle = screen.getByRole('switch');
    expect(toggle).toHaveAttribute('aria-checked', 'false');
    fireEvent.click(toggle);
    await waitFor(() => expect(request).toHaveBeenCalledWith(`/permissions/${permission.id}`,
      { method: 'PUT', body: JSON.stringify({ enabled: true }) }));
    expect(toggle).toHaveAttribute('aria-checked', 'true');
  });

  it.each([false, true])('角色管理可以%s授权状态为基础勾选或取消卡片权限', async (assigned) => {
    request.mockImplementation(async (endpoint: string, options?: { method?: string }) => {
      if (options?.method) return {};
      if (endpoint === '/permissions/') return [permission];
      if (endpoint === '/roles/') return [role];
      if (endpoint === `/roles/${role.id}/all-permissions`) return assigned ? [permission] : [];
      throw new Error(`Unexpected endpoint: ${endpoint}`);
    });
    render(<RoleManage />);
    fireEvent.click(await screen.findByText(role.name));
    const choice = await screen.findByRole('button', { name: /显示知识贡献榜/ });
    if (assigned) expect(choice).toHaveClass('is-active');
    else expect(choice).not.toHaveClass('is-active');
    fireEvent.click(choice);
    fireEvent.click(screen.getByRole('button', { name: '保存' }));
    await waitFor(() => expect(request).toHaveBeenCalledWith(
      `/roles/${role.id}/permissions${assigned ? '/remove' : ''}`,
      { method: 'POST', body: JSON.stringify({ permission_ids: [permission.id] }) }));
  });
});
