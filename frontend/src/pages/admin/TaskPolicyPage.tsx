/**
 * 工单关联策略配置 —— 管理员页面
 * 允许在线开关：前置/子工单阻塞 + 重复工单状态同步
 * 权限：frontend:admin:task-policy:manage
 * 入口：后台管理 → 其他（AdminEntries）
 */
import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Navbar, Loading, Toast } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { useAuthStore } from '@/stores/auth';

export const PERM_TASK_POLICY = 'frontend:admin:task-policy:manage';

const request = createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin');

interface PolicyItem {
  value: boolean;
  default: boolean;
  description: string;
}

type PolicyMap = Record<string, PolicyItem>;

// 策略 key 中文标签（前端展示用）
const POLICY_LABELS: Record<string, { title: string; tone: 'red' | 'blue' | 'gray' | 'purple' }> = {
  block_predecessor_on_resolved: {
    title: '前置工单 → 阻塞「已解决」',
    tone: 'red',
  },
  block_predecessor_on_closed: {
    title: '前置工单 → 阻塞「已关闭」',
    tone: 'red',
  },
  block_subtask_on_resolved: {
    title: '子工单 → 阻塞「已解决」',
    tone: 'blue',
  },
  block_subtask_on_closed: {
    title: '子工单 → 阻塞「已关闭」',
    tone: 'blue',
  },
  duplicate_status_sync_enabled: {
    title: '重复工单 → 状态自动同步',
    tone: 'purple',
  },
};

function unwrap<T>(raw: unknown): T {
  if (raw && typeof raw === 'object' && 'data' in raw) {
    return (raw as { data: T }).data;
  }
  return raw as T;
}

export default function TaskPolicyPage() {
  const navigate = useNavigate();
  const allowed = useAuthStore((s) => s.hasPermission(PERM_TASK_POLICY));

  const [loading, setLoading] = useState(true);
  const [savingKey, setSavingKey] = useState<string | null>(null);
  const [policies, setPolicies] = useState<PolicyMap | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = unwrap<PolicyMap>(await request('/task-policy', { skipCache: true }));
      setPolicies(data);
    } catch (e) {
      Toast({ message: e instanceof Error ? e.message : '加载策略失败', theme: 'error' });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (allowed) load();
  }, [allowed, load]);

  const toggle = async (key: string) => {
    if (!policies) return;
    const cur = policies[key];
    if (!cur) return;
    const nextValue = !cur.value;

    // 乐观更新（UI 立即切换，成功后保持，失败回滚）
    setSavingKey(key);
    setPolicies((prev) => prev ? { ...prev, [key]: { ...prev[key], value: nextValue } } : prev);

    try {
      await request('/task-policy', {
        method: 'PATCH',
        body: JSON.stringify({ [key]: nextValue }),
        headers: { 'Content-Type': 'application/json' },
      });
      Toast({
        message: `已${nextValue ? '开启' : '关闭'}：${POLICY_LABELS[key]?.title ?? key}`,
        theme: 'success',
      });
    } catch (e) {
      // 回滚
      setPolicies((prev) => prev ? { ...prev, [key]: { ...prev[key], value: cur.value } } : prev);
      Toast({ message: e instanceof Error ? e.message : '保存失败', theme: 'error' });
    } finally {
      setSavingKey(null);
    }
  };

  if (!allowed) {
    return (
      <div className="admin-view">
        <Navbar title="工单策略" leftArrow onLeftClick={() => navigate('/admin/entries')} fixed />
        <div className="admin-no-perm">您没有权限访问此页面</div>
      </div>
    );
  }

  return (
    <div className="admin-view">
      <Navbar title="工单关联规则" leftArrow onLeftClick={() => navigate('/admin/entries')} fixed />

      <div className="task-policy-wrap">
        {loading ? (
          <div className="task-policy-loading">
            <Loading text="加载中..." />
          </div>
        ) : !policies ? (
          <div className="task-policy-empty">加载失败，请返回重试</div>
        ) : (
          <>
            {/* 前置工单 */}
            <section className="task-policy-group">
              <h3 className="task-policy-group__title">前置工单（PREDECESSOR）</h3>
              <p className="task-policy-group__desc">前置工单是依赖关系——当前工单需要等前置工单完成后才能继续。</p>
              {['block_predecessor_on_resolved', 'block_predecessor_on_closed'].map((key) => (
                <PolicyRow
                  key={key}
                  itemKey={key}
                  item={policies[key]}
                  disabled={savingKey !== null}
                  loading={savingKey === key}
                  onToggle={toggle}
                />
              ))}
            </section>

            {/* 子工单 */}
            <section className="task-policy-group">
              <h3 className="task-policy-group__title">子工单（SUBTASK）</h3>
              <p className="task-policy-group__desc">子工单是层级归属——默认仅弱关联、不阻塞任何状态流转（管理员可开启阻塞）。</p>
              {['block_subtask_on_resolved', 'block_subtask_on_closed'].map((key) => (
                <PolicyRow
                  key={key}
                  itemKey={key}
                  item={policies[key]}
                  disabled={savingKey !== null}
                  loading={savingKey === key}
                  onToggle={toggle}
                />
              ))}
            </section>

            {/* 重复工单 */}
            <section className="task-policy-group">
              <h3 className="task-policy-group__title">重复工单（DUPLICATE）</h3>
              <p className="task-policy-group__desc">标记为重复的工单——开启后，一方状态变更会自动同步给所有重复方。每个被同步的工单都会写独立的操作日志。</p>
              {['duplicate_status_sync_enabled'].map((key) => (
                <PolicyRow
                  key={key}
                  itemKey={key}
                  item={policies[key]}
                  disabled={savingKey !== null}
                  loading={savingKey === key}
                  onToggle={toggle}
                />
              ))}
            </section>

            <p className="task-policy-footer">
              修改立即生效（Redis 缓存 60 秒刷新）。如需临时绕过阻塞，可由管理员直接操作工单状态。
            </p>
          </>
        )}
      </div>
    </div>
  );
}

function PolicyRow({
  itemKey,
  item,
  disabled,
  loading,
  onToggle,
}: {
  itemKey: string;
  item: PolicyItem | undefined;
  disabled: boolean;
  loading: boolean;
  onToggle: (key: string) => void;
}) {
  if (!item) return null;
  const meta = POLICY_LABELS[itemKey];
  const toneClass = `task-policy-row--${meta?.tone ?? 'gray'}`;
  const isDefaultDifferent = item.value !== item.default;

  return (
    <button
      type="button"
      className={`task-policy-row ${toneClass}`}
      onClick={() => !disabled && onToggle(itemKey)}
      disabled={disabled}
    >
      <div className="task-policy-row__body">
        <div className="task-policy-row__title">{meta?.title ?? itemKey}</div>
        <div className="task-policy-row__desc">{item.description}</div>
        {isDefaultDifferent && (
          <div className="task-policy-row__hint">当前值与默认值（{item.default ? '开启' : '关闭'}）不同</div>
        )}
      </div>
      <div
        className={`task-policy-row__switch ${item.value ? 'on' : 'off'} ${loading ? 'loading' : ''}`}
        role="switch"
        aria-checked={item.value}
      >
        <span className="task-policy-row__thumb" />
      </div>
    </button>
  );
}
