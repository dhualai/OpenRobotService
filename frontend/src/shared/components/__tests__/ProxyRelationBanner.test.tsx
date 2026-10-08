// 代他人提单：工单详情页关系横幅的统一文案渲染。
// 口径：所有视角统一「代 X 提交」（X=被代提人姓名，未注册/未实名缺省「未知用户」）；
// 视角标记（is_agent / is_principal）由后端按 token 判定后下发，前端只按标记渲染，
// 不自行拼身份判定；非参与人（无任何视角标记）→ 不渲染。
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import type { ReactNode } from 'react';

// api 层隔离：本测试只验证渲染，不触网（ack / decline 仅在点击时调用）
vi.mock('@/api/ticket', () => ({
  ackProxyRelation: vi.fn(),
  declineProxyRelation: vi.fn(),
}));

// tdesign 轻量替身：Popup 仅在 visible 时输出内容，避免常驻 DOM 干扰断言
vi.mock('tdesign-mobile-react', () => ({
  Button: ({ children, onClick }: { children?: ReactNode; onClick?: () => void }) => (
    <button onClick={onClick}>{children}</button>
  ),
  Popup: ({ children, visible }: { children?: ReactNode; visible?: boolean }) =>
    visible ? <div data-testid="proxy-popup">{children}</div> : null,
  Textarea: ({ value, onChange }: { value?: string; onChange?: (v: unknown) => void }) => (
    <textarea value={value} onChange={(e) => onChange?.(e.target.value)} />
  ),
  Toast: vi.fn(),
}));

import ProxyRelationBanner from '../ProxyRelationBanner';
import type { ProxyRelation } from '@/api/ticket';

const makeRelation = (over: Partial<ProxyRelation> = {}): ProxyRelation => ({
  id: 1,
  task_id: 100,
  relation_status: 'pending',
  source: 'manual',
  remark: null,
  is_agent: false,
  is_principal: false,
  is_assignee: false,
  agent_name: '张三',
  principal_name: '李四',
  notified_at: null,
  acked_at: null,
  declined_at: null,
  created_at: null,
  ...over,
});

describe('ProxyRelationBanner', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('代理人视角：统一文案「代 被代提人 提交」+ 关系状态胶囊', () => {
    render(<ProxyRelationBanner ticketId={100} relation={makeRelation({ is_agent: true })} />);

    expect(screen.getByText(/代/)).toBeInTheDocument();
    expect(screen.getByText('李四')).toBeInTheDocument();
    expect(screen.getByText('待确认')).toBeInTheDocument();
  });

  it('被代理人 pending：统一文案 + 展示「确认跟进 / 与我无关」两个操作', () => {
    render(<ProxyRelationBanner ticketId={100} relation={makeRelation({ is_principal: true })} />);

    expect(screen.getByText('确认跟进')).toBeInTheDocument();
    expect(screen.getByText('与我无关')).toBeInTheDocument();
  });

  it('被代理人 acknowledged：弱化只读态，不再出现操作按钮', () => {
    render(
      <ProxyRelationBanner
        ticketId={100}
        relation={makeRelation({ is_principal: true, relation_status: 'acknowledged' })}
      />,
    );

    expect(screen.getByText('已跟进')).toBeInTheDocument();
    expect(screen.queryByText('确认跟进')).toBeNull();
  });

  it('接单人视角：统一文案（只展示被代提人）+ 关系状态胶囊，只读无操作按钮', () => {
    render(
      <ProxyRelationBanner
        ticketId={100}
        relation={makeRelation({ is_assignee: true, relation_status: 'acknowledged' })}
      />,
    );

    expect(screen.getByText('李四')).toBeInTheDocument();
    expect(screen.queryByText('张三')).toBeNull();
    expect(screen.getByText('已跟进')).toBeInTheDocument();
    expect(screen.queryByText('确认跟进')).toBeNull();
  });

  it('被代提人未注册/未实名（姓名缺省）：统一缺省「未知用户」', () => {
    render(
      <ProxyRelationBanner
        ticketId={100}
        relation={makeRelation({ is_agent: true, principal_name: null })}
      />,
    );

    expect(screen.getByText('未知用户')).toBeInTheDocument();
    expect(screen.queryByText('李四')).toBeNull();
  });

  it('非参与人：不渲染（脱敏，后端无视角标记时无内容）', () => {
    const { container } = render(
      <ProxyRelationBanner
        ticketId={100}
        relation={makeRelation({ agent_name: null, principal_name: null })}
      />,
    );

    expect(container).toBeEmptyDOMElement();
  });
});
