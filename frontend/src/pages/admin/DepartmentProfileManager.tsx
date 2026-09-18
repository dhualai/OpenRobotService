// 部门职责画像管理（AI 派单 R2 部门分类依据）
// 挂载在责任模块树页面的「部门职责」标签下，与责任树互不影响。
// 数据源：GET/PUT /users/department-profiles（departments 表 profile_text/examples）
// 保存后后端通知 AI 热更新，R2 部门分类立即用最新职责描述。
import { useEffect, useState } from 'react';
import { Toast, Loading } from 'tdesign-mobile-react';

interface DeptProfile {
  id: string;
  name: string;
  company_id?: string | null;
  profile_text: string;
  examples: { title: string; dept?: string }[];
}

interface Props {
  request: <T>(url: string, opts?: any) => Promise<T>;
}

export default function DepartmentProfileManager({ request }: Props) {
  const [depts, setDepts] = useState<DeptProfile[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    request<DeptProfile[]>('/users/department-profiles')
      .then((data) => {
        const sorted = (data || []).slice().sort((a, b) => {
          // 有职责描述的排前面，无描述的在后；同组内按部门名排序
          const aHas = (a.profile_text || '').trim() ? 0 : 1;
          const bHas = (b.profile_text || '').trim() ? 0 : 1;
          if (aHas !== bHas) return aHas - bHas;
          return (a.name || '').localeCompare(b.name || '', 'zh-Hans-CN');
        });
        setDepts(sorted);
      })
      .catch(() => Toast({ message: '加载部门职责失败', theme: 'error' }))
      .finally(() => setLoading(false));
  }, [request]);

  const updateProfile = (i: number, val: string) =>
    setDepts((prev) => prev.map((d, idx) => (idx === i ? { ...d, profile_text: val } : d)));

  const updateExample = (i: number, ei: number, val: string) =>
    setDepts((prev) => prev.map((d, idx) =>
      idx !== i ? d : { ...d, examples: d.examples.map((e, k) => (k === ei ? { ...e, title: val } : e)) }));

  const addExample = (i: number) =>
    setDepts((prev) => prev.map((d, idx) => (idx === i ? { ...d, examples: [...d.examples, { title: '' }] } : d)));

  const removeExample = (i: number, ei: number) =>
    setDepts((prev) => prev.map((d, idx) =>
      idx !== i ? d : { ...d, examples: d.examples.filter((_, k) => k !== ei) }));

  const save = async () => {
    setSaving(true);
    try {
      const payload = depts.map((d) => ({
        id: d.id,
        profile_text: d.profile_text,
        examples: d.examples.filter((e) => (e.title || '').trim()),
      }));
      const res = await request<{ code: number }>('/users/department-profiles', {
        method: 'PUT',
        body: JSON.stringify({ departments: payload }),
      });
      Toast({ message: res?.code === 0 ? '已保存，AI 将热更新部门画像' : '保存失败', theme: res?.code === 0 ? 'success' : 'error' });
    } catch {
      Toast({ message: '保存失败，请重试', theme: 'error' });
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return <div style={{ display: 'flex', justifyContent: 'center', paddingTop: 40 }}><Loading text="加载中..." /></div>;
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <p style={{ margin: 0, color: '#666', fontSize: 13 }}>
          维护各部门职责描述与典型工单示例，供 AI 派单判断部门（保存后热更新）。
        </p>
        <button type="button" className="mac-chip mac-chip--tag-blue" onClick={save} disabled={saving}>
          {saving ? '保存中...' : '保存职责'}
        </button>
      </div>

      {depts.length === 0 && (
        <div style={{ textAlign: 'center', color: '#999', padding: 40 }}>暂无已审核部门</div>
      )}

      {depts.map((d, i) => (
        <div key={d.id} className="mac-iface-row" style={{ flexDirection: 'column', alignItems: 'stretch', marginBottom: 12, padding: 12 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
            <strong style={{ fontSize: 15 }}>{d.name}</strong>
          </div>
          <label style={{ fontSize: 12, color: '#888' }}>部门职责描述</label>
          <textarea
            className="mac-dp-textarea"
            value={d.profile_text}
            onChange={(e) => updateProfile(i, e.target.value)}
            rows={5}
            placeholder="例：【负责】...【不负责】...【典型现象】..."
            style={{ width: '100%', boxSizing: 'border-box', border: '1px solid var(--mac-border)', borderRadius: 8, padding: 8, fontSize: 13, lineHeight: 1.6, resize: 'vertical' }}
          />
          <label style={{ fontSize: 12, color: '#888', marginTop: 8 }}>典型工单示例</label>
          {d.examples.map((ex, ei) => (
            <div key={ei} style={{ display: 'flex', gap: 6, alignItems: 'center', marginTop: 4 }}>
              <input
                className="mac-dp-example"
                value={ex.title}
                onChange={(e) => updateExample(i, ei, e.target.value)}
                placeholder="示例工单标题"
                style={{ flex: 1, border: '1px solid var(--mac-border)', borderRadius: 6, padding: '4px 8px', fontSize: 13 }}
              />
              <button type="button" className="mac-btn mac-btn--ghost" onClick={() => removeExample(i, ei)}>×</button>
            </div>
          ))}
          <button type="button" className="mac-btn mac-btn--ghost" style={{ marginTop: 6, alignSelf: 'flex-start' }} onClick={() => addExample(i)}>
            + 添加示例
          </button>
        </div>
      ))}
    </div>
  );
}
