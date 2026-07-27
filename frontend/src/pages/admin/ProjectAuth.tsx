// 项目授权管理 - 基于接口文档 GET /api/admin/projects/licenses/{project_code}
import { useState, useEffect } from 'react';
import { Button, Toast, Loading, Dialog, Input, Popup } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { normalizeList } from '@/shared/utils/list';

interface AuthItem { id: string; project: string; user: string; role: string; granted_at: string; }
interface Project { id?: string; code?: string; name: string; }

// 关联人员可选角色列表
const ASSOCIATE_ROLES = ['实施', '数据分析师', '数据查看', '研发项目经理', '管理员', '项目对接人'];
// 用户列表占位：待接入后端项目人员绑定接口前，先用固定人员名单代替
const PLACEHOLDER_USERS = [
  '董华来', '张文星', '罗昊', '张俊磊', '胡健楠', '白永奇', '贾爽', '耿洪秀', '陈连鑫',
  '姜钦阳', '刘青源', '毛梦晴', '齐子谦', '田树政', '汪海波', '王卓', '吴佳秀', '吴彦清',
  '夏泽龙', '徐浩南', '张会丽', '朱珊珊',
];

interface AssociateItem { id: string; userId: string; role: string; }

export default function ProjectAuth() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProject, setSelectedProject] = useState<Project | null>(null);
  const [projectCodeInput, setProjectCodeInput] = useState('');
  const [items, setItems] = useState<AuthItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [projectLoading, setProjectLoading] = useState(true);
  const [projectPickerVisible, setProjectPickerVisible] = useState(false);
  const request = createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin');

  // 添加关联人员弹窗（当前为前端占位实现：用户列表用数字 1-9 代替，待接入真实用户列表接口）
  const [associateVisible, setAssociateVisible] = useState(false);
  const [associateUserId, setAssociateUserId] = useState<string | null>(null);
  const [associateRole, setAssociateRole] = useState<string | null>(null);
  const [associateList, setAssociateList] = useState<AssociateItem[]>([]);

  // 加载项目列表供选择
  useEffect(() => {
    request('/projects/')
      .then((data) => setProjects(normalizeList<Project>(data)))
      .catch((err) => Toast({ message: `加载项目失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' }))
      .finally(() => setProjectLoading(false));
  }, []);

  // 根据选中的项目代码获取授权信息
  const fetchLicenses = async (projectCode: string) => {
    if (!projectCode) return;
    setLoading(true);
    try {
      const data = await request(`/projects/licenses/${encodeURIComponent(projectCode)}`);
      setItems(normalizeList<AuthItem>(data));
    } catch (err) {
      Toast({ message: `加载授权失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
      setItems([]);
    } finally { setLoading(false); }
  };

  const handleProjectSelect = (project: Project) => {
    setSelectedProject(project);
    setProjectCodeInput('');
    setProjectPickerVisible(false);
    const code = project.code || project.name; // 使用 code 或 name 作为 project_code
    fetchLicenses(code);
  };

  const handleCustomCodeSubmit = () => {
    if (!projectCodeInput.trim()) return;
    setSelectedProject({ name: projectCodeInput.trim() });
    fetchLicenses(projectCodeInput.trim());
  };

  const handleRevoke = (item: AuthItem) => {
    Dialog.confirm?.({
      title: '确认撤销',
      content: '确定要撤销此授权吗？',
      onConfirm: async () => {
        try {
          // 注意：接口文档暂无删除授权的独立端点，使用原有路径
          await request(`/project/auth/${item.id}`, { method: 'DELETE' });
          Toast({ message: '授权已撤销', theme: 'success' });
          if (selectedProject) {
            const code = selectedProject.code || selectedProject.name;
            fetchLicenses(code);
          }
        } catch (err) {
          Toast({ message: `撤销失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
        }
      },
    });
  };

  // 打开添加关联人员弹窗
  const openAssociate = () => {
    if (!selectedProject) {
      Toast({ message: '请先选择一个项目', theme: 'warning' });
      return;
    }
    setAssociateUserId(null);
    setAssociateRole(null);
    setAssociateVisible(true);
  };

  // 保存关联人员（前端占位：暂存于本地列表，待接入后端项目人员绑定接口）
  const handleSaveAssociate = () => {
    if (!associateUserId) { Toast({ message: '请选择用户', theme: 'warning' }); return; }
    if (!associateRole) { Toast({ message: '请选择角色', theme: 'warning' }); return; }
    setAssociateList((prev) => [
      ...prev,
      { id: `${associateUserId}_${associateRole}_${Date.now()}`, userId: associateUserId, role: associateRole },
    ]);
    Toast({ message: '已添加关联人员', theme: 'success' });
    setAssociateVisible(false);
  };

  return (
    <div style={{ padding: 16 }}>
      <h4 style={{ marginBottom: 12, fontSize: 15, fontWeight: 600 }}>选择项目查看授权</h4>

      {/* 项目选择下拉：点击展开底部列表，避免项目过多时摊开占屏 */}
      {projectLoading ? <Loading text="加载项目..." /> : (
        <div
          onClick={() => setProjectPickerVisible(true)}
          style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            background: '#fff', borderRadius: 8, padding: '12px 14px', marginBottom: 16,
            boxShadow: '0 1px 3px rgba(0,0,0,0.06)', cursor: 'pointer',
          }}
        >
          <div>
            {selectedProject ? (
              <>
                <div style={{ fontWeight: 500 }}>{selectedProject.name}</div>
                {selectedProject.code && <div style={{ fontSize: 12, color: '#999' }}>项目代码：{selectedProject.code}</div>}
              </>
            ) : (
              <span style={{ color: '#bbb', fontSize: 14 }}>请选择项目</span>
            )}
          </div>
          <span style={{ color: '#999' }}>›</span>
        </div>
      )}

      <Popup visible={projectPickerVisible} onClose={() => setProjectPickerVisible(false)} placement="bottom" showOverlay>
        <div style={{ padding: 20, maxHeight: '60vh', overflow: 'auto' }}>
          <h4 style={{ marginBottom: 12 }}>选择项目</h4>
          {projects.map((p) => (
            <div
              key={p.id || p.name}
              onClick={() => handleProjectSelect(p)}
              style={{
                background: selectedProject?.name === p.name ? '#e8f2ff' : '#fff',
                borderRadius: 8,
                padding: '12px 14px',
                marginBottom: 8,
                cursor: 'pointer',
                border: selectedProject?.name === p.name ? '1px solid #0052d9' : '1px solid transparent',
              }}
            >
              <div style={{ fontWeight: 500 }}>{p.name}</div>
              {p.code && <div style={{ fontSize: 12, color: '#999' }}>项目代码：{p.code}</div>}
            </div>
          ))}
          {projects.length === 0 && (
            <div style={{ textAlign: 'center', padding: 30, color: '#999' }}>暂无项目</div>
          )}
        </div>
      </Popup>

      {/* 手动输入项目代码 */}
      <div style={{ marginBottom: 16, display: 'flex', gap: 8 }}>
        <Input
          value={projectCodeInput}
          onChange={(v) => setProjectCodeInput(String(v))}
          placeholder="或输入项目代码查询"
          clearable
          style={{ flex: 1 }}
        />
        <Button size="small" theme="primary" onClick={handleCustomCodeSubmit} disabled={!projectCodeInput.trim()}>
          查询
        </Button>
      </div>

      {/* 授权列表 */}
      {!selectedProject ? (
        <div style={{ textAlign: 'center', padding: 40, color: '#999' }}>请选择一个项目查看授权信息</div>
      ) : loading ? <Loading text="加载授权..." /> : (
        <>
          <div style={{ fontSize: 14, fontWeight: 500, marginBottom: 12, color: '#0052d9' }}>
            {selectedProject.name} - 授权记录 ({items.length})
          </div>
          {items.map((item) => (
            <div key={item.id} style={{ background: '#fff', borderRadius: 8, padding: 14, marginBottom: 10, boxShadow: '0 1px 3px rgba(0,0,0,0.06)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div>
                  <div style={{ fontWeight: 500 }}>{item.project}</div>
                  <div style={{ fontSize: 13, color: '#666' }}>{item.user} - {item.role}</div>
                </div>
                <Button size="small" theme="danger" variant="outline" onClick={() => handleRevoke(item)}>撤销</Button>
              </div>
            </div>
          ))}
          {items.length === 0 && (
            <div style={{ textAlign: 'center', padding: 40, color: '#999' }}>该项目暂无授权记录</div>
          )}
        </>
      )}

      {/* 添加关联人员 */}
      <Button theme="primary" variant="outline" block style={{ marginTop: 16 }} onClick={openAssociate}>
        + 添加关联人员
      </Button>

      {associateList.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <div style={{ fontSize: 13, color: '#999', marginBottom: 8 }}>本次已添加的关联人员</div>
          {associateList.map((a) => (
            <div key={a.id} style={{ background: '#fff', borderRadius: 8, padding: 14, marginBottom: 10, boxShadow: '0 1px 3px rgba(0,0,0,0.06)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div style={{ fontSize: 13, color: '#666' }}>{a.userId} - {a.role}</div>
                <Button
                  size="small"
                  theme="danger"
                  variant="outline"
                  onClick={() => setAssociateList((prev) => prev.filter((x) => x.id !== a.id))}
                >
                  移除
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* 添加关联人员弹窗 */}
      <Popup visible={associateVisible} onClose={() => setAssociateVisible(false)} placement="bottom" showOverlay>
        <div style={{ padding: 20, maxHeight: '70vh', overflow: 'auto' }}>
          <h4 style={{ marginBottom: 4 }}>添加关联人员</h4>
          <div style={{ fontSize: 12, color: '#999', marginBottom: 16 }}>
            项目：{selectedProject?.name}
          </div>

          <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 8 }}>选择用户</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 20 }}>
            {PLACEHOLDER_USERS.map((uid) => (
              <div
                key={uid}
                onClick={() => setAssociateUserId(uid)}
                style={{
                  height: 36, padding: '0 14px', borderRadius: 18,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  cursor: 'pointer', fontSize: 13, fontWeight: 500, whiteSpace: 'nowrap',
                  background: associateUserId === uid ? '#0052d9' : '#f5f5f5',
                  color: associateUserId === uid ? '#fff' : '#333',
                  border: associateUserId === uid ? '1px solid #0052d9' : '1px solid transparent',
                }}
              >
                {uid}
              </div>
            ))}
          </div>

          <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 8 }}>选择角色</div>
          <div style={{ marginBottom: 20 }}>
            {ASSOCIATE_ROLES.map((role) => (
              <div
                key={role}
                onClick={() => setAssociateRole(role)}
                style={{
                  display: 'flex', alignItems: 'center', gap: 10,
                  padding: '10px 0', borderBottom: '1px solid #f5f5f5', cursor: 'pointer',
                }}
              >
                <div
                  style={{
                    width: 16, height: 16, borderRadius: '50%',
                    border: `1px solid ${associateRole === role ? '#0052d9' : '#ccc'}`,
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                  }}
                >
                  {associateRole === role && (
                    <div style={{ width: 8, height: 8, borderRadius: '50%', background: '#0052d9' }} />
                  )}
                </div>
                <div style={{ fontSize: 14 }}>{role}</div>
              </div>
            ))}
          </div>

          <div style={{ display: 'flex', gap: 8 }}>
            <Button theme="default" block onClick={() => setAssociateVisible(false)}>取消</Button>
            <Button theme="primary" block onClick={handleSaveAssociate}>保存</Button>
          </div>
        </div>
      </Popup>
    </div>
  );
}
