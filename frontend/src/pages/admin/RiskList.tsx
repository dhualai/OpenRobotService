// 风险列表 - 从 BackgroundService tmp-project 迁移
import { useState, useEffect } from 'react';
import { Button, Toast, Loading, Dialog } from 'tdesign-mobile-react';
import { useNavigate } from 'react-router-dom';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { normalizeList } from '@/shared/utils/list';

interface Risk { id: string; title: string; level: string; project: string; status: string; created_at: string; }

const RISK_COLORS: Record<string, string> = { high: '#d54941', medium: '#e37318', low: '#2ba471' };

export default function RiskList() {
  const navigate = useNavigate();
  const [risks, setRisks] = useState<Risk[]>([]);
  const [loading, setLoading] = useState(true);
  const request = createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin');

  const fetchRisks = async () => {
    setLoading(true);
    try {
      const data = await request('/projects/risks/');
      setRisks(normalizeList<Risk>(data));
    } catch (e) {
      Toast({ message: String(e), theme: 'error' });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchRisks(); }, []);

  const handleDelete = (id: string) => {
    Dialog.confirm?.({ title: '确认删除', content: '确定要删除此风险项吗？', onConfirm: async () => { await request(`/projects/risks/${id}`, { method: 'DELETE' }); fetchRisks(); } });
  };

  if (loading) return <Loading text="加载中..." />;

  return (
    <div style={{ padding: 16 }}>
      <Button theme="primary" block style={{ marginBottom: 16 }} onClick={() => navigate('/app/admin/risk-edit')}>新建风险项</Button>
      {risks.map((r) => (
        <div key={r.id} style={{ background: '#fff', borderRadius: 8, padding: 14, marginBottom: 10, boxShadow: '0 1px 3px rgba(0,0,0,0.06)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
            <div style={{ flex: 1 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                <span style={{ width: 8, height: 8, borderRadius: '50%', background: RISK_COLORS[r.level] || '#999', display: 'inline-block' }} />
                <strong>{r.title}</strong>
              </div>
              <div style={{ fontSize: 13, color: '#666' }}>{r.project} · {r.level}</div>
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <Button size="small" variant="outline" onClick={() => navigate(`/app/admin/risk-edit/${r.id}`)}>编辑</Button>
              <Button size="small" theme="danger" variant="outline" onClick={() => handleDelete(r.id)}>删除</Button>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
