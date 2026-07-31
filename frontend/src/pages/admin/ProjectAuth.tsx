// 项目授权管理 - 基于接口文档 GET /api/admin/projects/licenses/{project_code}
import { useState, useEffect } from 'react';
import { Button, Toast, Loading, Dialog, Input, Popup, DateTimePicker } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { normalizeList } from '@/shared/utils/list';
import UserSelect from '@/shared/components/UserSelect';
import type { UserItem } from '@/api/users';

interface AuthItem {
  id: string;
  project_code: string;
  machine_code?: string;
  apply_time: string;
  expire_time: string;
  license_code: string;
  applicant: string;
}
interface Project { id?: string; code?: string; name: string; }

const maskCode = (code: string): string => {
  if (!code) return '';
  return code.slice(0, 30) + '*'.repeat(10);
};

const todayStr = (): string => {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
};

// 关联人员可选角色列表
const ASSOCIATE_ROLES = ['实施', '数据分析师', '数据查看', '研发项目经理', '管理员', '项目对接人'];

interface AssociateItem { id: string; userId: string; userName: string; role: string; }

export default function ProjectAuth() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProject, setSelectedProject] = useState<Project | null>(null);
  const [projectSearch, setProjectSearch] = useState('');
  const [items, setItems] = useState<AuthItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [projectLoading, setProjectLoading] = useState(true);
  const [projectPickerVisible, setProjectPickerVisible] = useState(false);
  const request = createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin');

  // 添加关联人员弹窗：用户列表接入真实的可指派人员接口 GET /api/tasks/assignable-users
  const [associateVisible, setAssociateVisible] = useState(false);
  const [associateUser, setAssociateUser] = useState<UserItem | null>(null);
  const [associateRole, setAssociateRole] = useState<string | null>(null);
  const [associateList, setAssociateList] = useState<AssociateItem[]>([]);

  // 申请授权码：机器码 + 开始/结束日期，调用 POST /export/apply_project_license（经 MQTT 审批）
  const [machineCode, setMachineCode] = useState('');
  const [licenseStartDate, setLicenseStartDate] = useState(todayStr());
  const [licenseEndDate, setLicenseEndDate] = useState(todayStr());
  const [startDatePickerVisible, setStartDatePickerVisible] = useState(false);
  const [endDatePickerVisible, setEndDatePickerVisible] = useState(false);
  const [applyingLicense, setApplyingLicense] = useState(false);

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
    setProjectSearch('');
    setProjectPickerVisible(false);
    const code = project.code || project.name; // 使用 code 或 name 作为 project_code
    fetchLicenses(code);
  };

  // 模糊匹配：按名称或代码任意关键词片段过滤（不要求项目代码，普通人记不住代码）
  const filteredProjects = projectSearch.trim()
    ? projects.filter((p) => {
        const kw = projectSearch.trim().toLowerCase();
        return p.name.toLowerCase().includes(kw) || (p.code || '').toLowerCase().includes(kw);
      })
    : projects;

  // 申请授权码：机器码 + 开始/结束日期 → 提交给后端，后端通过 MQTT 向设备端申请审批（最长约60秒）
  const handleApplyLicense = async () => {
    if (!selectedProject) { Toast({ message: '请先选择一个项目', theme: 'warning' }); return; }
    if (!machineCode.trim()) { Toast({ message: '请输入机器码', theme: 'warning' }); return; }
    if (!licenseStartDate || !licenseEndDate) { Toast({ message: '请选择开始和结束日期', theme: 'warning' }); return; }
    if (licenseStartDate > licenseEndDate) { Toast({ message: '开始日期不能晚于结束日期', theme: 'warning' }); return; }

    const projectCode = selectedProject.code || selectedProject.name;
    setApplyingLicense(true);
    try {
      const status = await request<{ status?: string; message?: string; license_content?: string }>(
        '/export/apply_project_license',
        {
          method: 'POST',
          body: JSON.stringify({
            project_code: projectCode,
            mac: machineCode.trim(),
            start_date: licenseStartDate,
            end_date: licenseEndDate,
          }),
          timeout: 65000, // 后端需等待 MQTT 审批结果，最长约 60 秒，长于默认的 30 秒超时
        },
      );

      if (status?.status === 'approved') {
        Toast({ message: `授权码申请成功：${status.license_content || ''}`, theme: 'success' });
        setMachineCode('');
        setLicenseStartDate(todayStr());
        setLicenseEndDate(todayStr());
        fetchLicenses(projectCode);
      } else if (status?.status === 'rejected') {
        Toast({ message: `申请被拒绝${status.message ? '：' + status.message : ''}`, theme: 'error' });
      } else {
        Toast({ message: status?.message || '申请未获批准，请稍后重试', theme: 'error' });
      }
    } catch (err) {
      Toast({ message: `申请授权码失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setApplyingLicense(false);
    }
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
    setAssociateUser(null);
    setAssociateRole(null);
    setAssociateVisible(true);
  };

  // 保存关联人员（前端占位：暂存于本地列表，待接入后端项目人员绑定接口）
  const handleSaveAssociate = () => {
    if (!associateUser) { Toast({ message: '请选择用户', theme: 'warning' }); return; }
    if (!associateRole) { Toast({ message: '请选择角色', theme: 'warning' }); return; }
    setAssociateList((prev) => [
      ...prev,
      { id: `${associateUser.id}_${associateRole}_${Date.now()}`, userId: associateUser.id, userName: associateUser.name || associateUser.username, role: associateRole },
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
        <div style={{ padding: 20, maxHeight: '70vh', overflow: 'auto' }}>
          <h4 style={{ marginBottom: 12 }}>选择项目</h4>
          <Input
            value={projectSearch}
            onChange={(v) => setProjectSearch(String(v))}
            placeholder="输入项目名称关键词模糊查找"
            clearable
            style={{ marginBottom: 12 }}
          />
          {filteredProjects.map((p) => (
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
          {filteredProjects.length === 0 && (
            <div style={{ textAlign: 'center', padding: 30, color: '#999' }}>未找到匹配的项目</div>
          )}
        </div>
      </Popup>

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
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                <div>
                  <div style={{ fontWeight: 500 }}>{maskCode(item.license_code)}</div>
                  {item.machine_code && <div style={{ fontSize: 12, color: '#999' }}>机器码：{maskCode(item.machine_code)}</div>}
                  <div style={{ fontSize: 13, color: '#666' }}>有效期：{item.apply_time} ～ {item.expire_time}</div>
                  <div style={{ fontSize: 12, color: '#999' }}>申请人：{item.applicant}</div>
                </div>
                <Button size="small" theme="danger" variant="outline" onClick={() => handleRevoke(item)}>撤销</Button>
              </div>
            </div>
          ))}
          {items.length === 0 && (
            <div style={{ textAlign: 'center', padding: 40, color: '#999' }}>该项目暂无授权记录</div>
          )}

          {/* 申请授权码：输入机器码 + 开始/结束日期，经 MQTT 向设备端申请授权 */}
          <div style={{ background: '#fff', borderRadius: 8, padding: 14, marginTop: 16, boxShadow: '0 1px 3px rgba(0,0,0,0.06)' }}>
            <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>申请授权码</div>
            <Input
              value={machineCode}
              onChange={(v) => setMachineCode(String(v))}
              placeholder="请输入机器码"
              clearable
              style={{ marginBottom: 10 }}
            />
            <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
              <div
                onClick={() => setStartDatePickerVisible(true)}
                style={{ flex: 1, border: '1px solid #dcdcdc', borderRadius: 6, padding: '10px 12px', color: licenseStartDate ? '#333' : '#bbb', fontSize: 14, cursor: 'pointer' }}
              >
                {licenseStartDate || '开始日期'}
              </div>
              <div
                onClick={() => setEndDatePickerVisible(true)}
                style={{ flex: 1, border: '1px solid #dcdcdc', borderRadius: 6, padding: '10px 12px', color: licenseEndDate ? '#333' : '#bbb', fontSize: 14, cursor: 'pointer' }}
              >
                {licenseEndDate || '结束日期'}
              </div>
            </div>
            <Button theme="primary" block loading={applyingLicense} onClick={handleApplyLicense}>
              申请授权码
            </Button>
          </div>

          <Popup visible={startDatePickerVisible} onClose={() => setStartDatePickerVisible(false)} placement="bottom">
            <DateTimePicker
              mode="date"
              title="选择开始日期"
              format="YYYY-MM-DD"
              value={licenseStartDate || undefined}
              onConfirm={(v) => { setLicenseStartDate(String(v)); setStartDatePickerVisible(false); }}
              onCancel={() => setStartDatePickerVisible(false)}
            />
          </Popup>
          <Popup visible={endDatePickerVisible} onClose={() => setEndDatePickerVisible(false)} placement="bottom">
            <DateTimePicker
              mode="date"
              title="选择结束日期"
              format="YYYY-MM-DD"
              value={licenseEndDate || undefined}
              onConfirm={(v) => { setLicenseEndDate(String(v)); setEndDatePickerVisible(false); }}
              onCancel={() => setEndDatePickerVisible(false)}
            />
          </Popup>
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
                <div style={{ fontSize: 13, color: '#666' }}>{a.userName} - {a.role}</div>
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
          <div style={{ marginBottom: 20 }}>
            <UserSelect
              value={associateUser?.id}
              onChange={setAssociateUser}
              placeholder="请选择用户"
              title="选择用户"
            />
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
