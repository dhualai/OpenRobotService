// 车辆信息确认弹窗：三行「标签 + 值」渲染、缺值占位、双按钮回调、确认中防重复提交。
//
// tdesign 用轻量替身——仓库既有约定（见 ProxyRelationBanner.test.tsx 等十余处）：
// 测试不渲染真实 Dialog/Popup，避免常驻 DOM 与弹层动画干扰断言。替身忠实复刻真实 Dialog 的回调语义：
//   - visible=false 时不渲染子树（与 tdesign Popup 一致）
//   - 点「取消」只回调 onCancel（真实 Dialog 会同时回调 onCancel 与 onClose，本组件刻意只接前者）
//   - 点「确认」只回调 onConfirm
//   - disabled 的按钮不派发点击（与真实浏览器对 disabled 控件的行为一致）
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import type { ReactNode } from 'react';

interface BtnProps {
  content?: ReactNode;
  disabled?: boolean;
  loading?: boolean;
}

vi.mock('tdesign-mobile-react', () => ({
  Dialog: ({
    visible,
    title,
    cancelBtn,
    confirmBtn,
    onCancel,
    onConfirm,
    children,
  }: {
    visible?: boolean;
    title?: ReactNode;
    cancelBtn?: BtnProps | null;
    confirmBtn?: BtnProps | null;
    onCancel?: () => void;
    onConfirm?: () => void;
    children?: ReactNode;
  }) => {
    if (!visible) return null;
    return (
      <div data-testid="vehicle-confirm-dialog">
        <div>{title}</div>
        <div>{children}</div>
        <button disabled={cancelBtn?.disabled} onClick={() => { if (!cancelBtn?.disabled) onCancel?.(); }}>
          {cancelBtn?.content}
        </button>
        <button disabled={confirmBtn?.disabled} onClick={() => { if (!confirmBtn?.disabled) onConfirm?.(); }}>
          {confirmBtn?.content}
        </button>
      </div>
    );
  },
}));

import VehicleConfirmDialog from '../VehicleConfirmDialog';

const INFO = {
  scene: 'proj_abc123def456',
  projectName: '项目A',
  customerName: '客户A',
  vehicleModel: 'XQE',
};

describe('VehicleConfirmDialog（车辆信息确认弹窗）', () => {
  it('visible=false 时不渲染内容', () => {
    render(<VehicleConfirmDialog visible={false} info={INFO} onConfirm={vi.fn()} onCancel={vi.fn()} />);

    expect(screen.queryByTestId('vehicle-confirm-dialog')).not.toBeInTheDocument();
  });

  it('渲染标题与三行「标签 + 值」', () => {
    render(<VehicleConfirmDialog visible info={INFO} onConfirm={vi.fn()} onCancel={vi.fn()} />);

    expect(screen.getByText('车辆信息确认')).toBeInTheDocument();
    expect(screen.getByText('项目名称')).toBeInTheDocument();
    expect(screen.getByText('客户名称')).toBeInTheDocument();
    expect(screen.getByText('车型信息')).toBeInTheDocument();
    expect(screen.getByText('项目A')).toBeInTheDocument();
    expect(screen.getByText('客户A')).toBeInTheDocument();
    expect(screen.getByText('XQE')).toBeInTheDocument();
  });

  it('信息缺项显示占位符而不是空白行', () => {
    render(
      <VehicleConfirmDialog
        visible
        info={{ ...INFO, customerName: '', vehicleModel: '   ' }}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(screen.getAllByText('—')).toHaveLength(2);
    expect(screen.getByText('项目A')).toBeInTheDocument();
  });

  it('info 为 null 时三行全为占位符（不抛错）', () => {
    render(<VehicleConfirmDialog visible info={null} onConfirm={vi.fn()} onCancel={vi.fn()} />);

    expect(screen.getAllByText('—')).toHaveLength(3);
  });

  it('点「确认」触发 onConfirm', () => {
    const onConfirm = vi.fn();
    render(<VehicleConfirmDialog visible info={INFO} onConfirm={onConfirm} onCancel={vi.fn()} />);

    fireEvent.click(screen.getByText('确认'));

    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it('点「暂不」触发 onCancel 且只触发一次', () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    render(<VehicleConfirmDialog visible info={INFO} onConfirm={onConfirm} onCancel={onCancel} />);

    fireEvent.click(screen.getByText('暂不'));

    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it('confirming 期间两个按钮都禁用，再点「确认」不会重复回调', () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    render(<VehicleConfirmDialog visible info={INFO} confirming onConfirm={onConfirm} onCancel={onCancel} />);

    expect(screen.getByText('确认')).toBeDisabled();
    expect(screen.getByText('暂不')).toBeDisabled();

    fireEvent.click(screen.getByText('确认'));
    fireEvent.click(screen.getByText('暂不'));

    expect(onConfirm).not.toHaveBeenCalled();
    expect(onCancel).not.toHaveBeenCalled();
  });
});
