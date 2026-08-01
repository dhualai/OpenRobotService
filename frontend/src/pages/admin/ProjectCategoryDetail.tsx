// 项目标签下钻明细 —— 点击仪表盘中「调度阶段」或「紧急度」标签后展示对应项目列表
// 通过路由参数 dimension（stage / urgency）区分两种下钻来源，复用同一个页面。
import { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Navbar, Loading } from 'tdesign-mobile-react';
import { fetchProjectsByStage, fetchProjectsByUrgency, type ProjectListItem } from '@/api/dashboard';
import { PROJECT_STAGE_MAP, URGENCY_MAP } from '@/shared/constants/dashboard';
import { useAuthStore } from '@/stores/auth';

export default function ProjectCategoryDetail() {
  const { dimension = 'stage', key = '' } = useParams<{ dimension: string; key: string }>();
  const navigate = useNavigate();
  const { projectIds } = useAuthStore();
  const [items, setItems] = useState<ProjectListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);

  const isUrgency = dimension === 'urgency';
  const meta = isUrgency ? URGENCY_MAP[key] : PROJECT_STAGE_MAP[key];
  const backendReady = isUrgency ? true : (PROJECT_STAGE_MAP[key]?.backendReady ?? false);

  const load = useCallback(async () => {
    setLoading(true);
    const res = isUrgency ? await fetchProjectsByUrgency(key, projectIds) : await fetchProjectsByStage(key, projectIds);
    setItems(res.items);
    setTotal(res.total);
    setLoading(false);
  }, [key, isUrgency, projectIds]);

  useEffect(() => { load(); }, [load]);

  return (
    <div>
      <Navbar title={`${meta?.label || key} · 项目明细`} leftArrow onLeftClick={() => navigate(-1)} fixed />
      <div style={{ padding: '16px', paddingTop: 64 }}>
        <p style={{ fontSize: 13, color: '#999', marginBottom: 12 }}>共 {total} 个项目</p>

        {loading ? <Loading text="加载中..." /> : (
          items.length === 0 ? (
            <div style={{ textAlign: 'center', padding: 40, color: '#999' }}>
              暂无数据
              {!backendReady && (
                <div style={{ marginTop: 8, fontSize: 12, color: '#bbb' }}>
                  该分类后端接口/枚举尚未接入，见 docs/工程文档.md
                </div>
              )}
            </div>
          ) : (
            items.map((p) => (
              <div
                key={p.id}
                style={{
                  background: '#fff', borderRadius: 8, padding: 14, marginBottom: 10,
                  boxShadow: '0 1px 3px rgba(0,0,0,0.06)', cursor: 'pointer',
                }}
                onClick={() => navigate(`/admin/project-detail/${p.id}`)}
              >
                <div style={{ fontWeight: 600, fontSize: 15 }}>{p.name}</div>
                <div style={{ fontSize: 12, color: '#999', marginTop: 4 }}>
                  {p.project_code} · 对接人: {p.contact_person || '未指定'}
                </div>
              </div>
            ))
          )
        )}
      </div>
    </div>
  );
}
