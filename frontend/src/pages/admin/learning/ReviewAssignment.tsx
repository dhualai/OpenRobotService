import { useEffect, useMemo, useState } from 'react';
import { Alert, Button, Select, Space, Spin } from 'antd';
import { listLearningReviewers, type LearningReviewer } from '@/api/learning';

export default function ReviewAssignment({ value, onChange, disabled }: {
  value?: string; onChange: (id?: string) => void; disabled: boolean;
}) {
  const [users, setUsers] = useState<LearningReviewer[]>([]);
  const [department, setDepartment] = useState<string>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [refreshKey, setRefreshKey] = useState(0);
  useEffect(() => {
    let active = true;
    setLoading(true); setError('');
    listLearningReviewers().then((result) => { if (active) setUsers(result.items); })
      .catch((cause) => { if (active) setError(cause instanceof Error ? cause.message : '审核人列表加载失败'); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [refreshKey]);
  const departmentKey = (user: LearningReviewer) => user.department_id || user.department || '__none__';
  const departments = useMemo(() => Array.from(new Map(users.map((user) => [departmentKey(user),
    { value: departmentKey(user), label: user.department || '未设置部门' }])).values()), [users]);
  const filtered = users.filter((user) => !department || departmentKey(user) === department);
  return <Space direction="vertical" style={{ width: '100%' }}>
    <Alert type="info" showIcon message="提交后将在系统任务 → 学习任务中生成审核工单。选择审核人则直接指派；不选则走现有自动派单流程。" />
    {error && <Alert type="error" role="alert" message={error} action={<Button disabled={disabled} onClick={() => setRefreshKey((current) => current + 1)}>重新加载</Button>} />}
    <label htmlFor="learning-review-department">按部门筛选</label>
    <Select id="learning-review-department" aria-label="按部门筛选审核人" showSearch allowClear optionFilterProp="label"
      placeholder="全部部门" value={department} disabled={disabled || loading} options={departments}
      style={{ width: '100%' }} onChange={(next?: string) => { setDepartment(next); onChange(undefined); }} />
    <label htmlFor="learning-reviewer">审核人（可选）</label>
    <Select id="learning-reviewer" aria-label="审核人" showSearch allowClear optionFilterProp="label" loading={loading}
      placeholder="不选择，自动派单" value={value} onChange={onChange} disabled={disabled || loading}
      notFoundContent={loading ? <Spin size="small" /> : '没有匹配的在职用户'} style={{ width: '100%' }}
      options={filtered.map((user) => ({ value: user.id, label: `${user.name}（${user.username}） · ${user.department || '未设置部门'}` }))} />
    <span>全量在职用户，不限制后台管理角色；支持按姓名、账号搜索。{value ? '将直接指派给所选审核人。' : '当前未指定审核人，将自动派单。'}</span>
  </Space>;
}
