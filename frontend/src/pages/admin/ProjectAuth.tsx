// 项目授权管理 - 展示授权记录、申请授权码
import { useState, useEffect } from 'react';
import { Button, Toast, Loading, Dialog } from 'tdesign-mobile-react';
import ClearableInput from '@/shared/components/ClearableInput';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { normalizeList } from '@/shared/utils/list';

interface AuthItem {
  id: string;
  project_code: string;
  machine_code?: string;
  apply_time: string;
  expire_time: string;
  license_code: string;
  applicant: string;
  max_vehicles?: number | null;
}
interface Project { id?: string; project_code?: string; name: string; }

const maskCode = (code: string): string => {
  if (!code) return '';
  return code.length > 10 ? `${code.slice(0, 10)}...` : code;
};

const handleCopyCode = async (text: string) => {
  if (!text) return;
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
    } else {
      const textarea = document.createElement('textarea');
      textarea.value = text;
      textarea.style.position = 'fixed';
      textarea.style.opacity = '0';
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand('copy');
      document.body.removeChild(textarea);
    }
    Toast({ message: '已复制', theme: 'success' });
  } catch {
    Toast({ message: '复制失败，请手动复制', theme: 'error' });
  }
};

const todayStr = (): string => {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
};

export default function ProjectAuth({ selectedProject }: { selectedProject: Project | null }) {
  const [items, setItems] = useState<AuthItem[]>([]);
  const [loading, setLoading] = useState(false);
  const request = createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin');

  const [machineCode, setMachineCode] = useState('');
  const [licenseStartDate, setLicenseStartDate] = useState(todayStr());
  const [licenseEndDate, setLicenseEndDate] = useState(todayStr());
  const [maxVehicles, setMaxVehicles] = useState('');
  const [applyingLicense, setApplyingLicense] = useState(false);

  // 根据选中的项目代码获取授权信息（传 type=all 获取全部授权记录）
  const fetchLicenses = async (projectCode: string) => {
    if (!projectCode) return;
    setLoading(true);
    try {
      const data = await request(`/projects/licenses/${encodeURIComponent(projectCode)}?type=all`, { skipCache: true });
      setItems(normalizeList<AuthItem>(data));
    } catch (err) {
      Toast({ message: `加载授权失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
      setItems([]);
    } finally { setLoading(false); }
  };

  // 选中项目变化时重新拉取授权
  useEffect(() => {
    if (selectedProject) {
      const code = selectedProject.project_code || selectedProject.name;
      fetchLicenses(code);
    } else {
      setItems([]);
    }
  }, [selectedProject?.id]);

  const handleApplyLicense = async () => {
    if (!selectedProject) { Toast({ message: '请先选择一个项目', theme: 'warning' }); return; }
    if (!machineCode.trim()) { Toast({ message: '请输入机器码', theme: 'warning' }); return; }
    if (!licenseStartDate || !licenseEndDate) { Toast({ message: '请选择开始和结束日期', theme: 'warning' }); return; }
    if (licenseStartDate > licenseEndDate) { Toast({ message: '开始日期不能晚于结束日期', theme: 'warning' }); return; }
    if (!maxVehicles.trim()) { Toast({ message: '请输入允许最大车数', theme: 'warning' }); return; }
    const maxVehiclesNum = Number(maxVehicles.trim());
    if (!Number.isFinite(maxVehiclesNum) || maxVehiclesNum <= 0) { Toast({ message: '允许最大车数必须为大于 0 的整数', theme: 'warning' }); return; }

    const projectCode = selectedProject.project_code || selectedProject.name;
    setApplyingLicense(true);
    try {
      const status = await request<{ status?: string; message?: string; license_content?: string }>(
        '/export/apply_project_license',
        {
          method: 'POST',
          body: JSON.stringify({
            project_code: projectCode,
            mac: machineCode.trim(),
            start_date: `${licenseStartDate} 00:00:00`,
            end_date: `${licenseEndDate} 23:59:59`,
            max_vehicles: maxVehiclesNum,
          }),
          timeout: 65000,
        },
      );

      if (status?.status === 'approved') {
        Toast({ message: `授权码申请成功：${status.license_content || ''}`, theme: 'success' });
        setMachineCode('');
        setLicenseStartDate(todayStr());
        setLicenseEndDate(todayStr());
        setMaxVehicles('');
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
          await request(`/project/auth/${item.id}`, { method: 'DELETE' });
          Toast({ message: '授权已撤销', theme: 'success' });
          if (selectedProject) {
            const code = selectedProject.project_code || selectedProject.name;
            fetchLicenses(code);
          }
        } catch (err) {
          Toast({ message: `撤销失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
        }
      },
    });
  };

  if (!selectedProject) {
    return <div style={{ textAlign: 'center', padding: 40, color: '#999' }}>请先选择项目</div>;
  }

  return (
    <div style={{ padding: '0 16px 16px' }}>
      {loading ? <Loading text="加载授权..." /> : (
        <>
          <div style={{ fontSize: 14, fontWeight: 500, marginBottom: 12, color: '#0052d9' }}>
            {selectedProject.name} - 授权记录 ({items.length})
          </div>
          {items.map((item) => (
            <div key={item.id} style={{ background: '#fff', borderRadius: 8, padding: 14, marginBottom: 10, boxShadow: '0 1px 3px rgba(0,0,0,0.06)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
                <div style={{ fontWeight: 500, wordBreak: 'break-all' }}>{maskCode(item.license_code)}</div>
                <span
                  onClick={() => handleCopyCode(item.license_code)}
                  style={{ flexShrink: 0, fontSize: 12, color: '#0052d9', cursor: 'pointer' }}
                >
                  复制
                </span>
              </div>
              {item.machine_code && (
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, marginTop: 4 }}>
                  <div style={{ fontSize: 12, color: '#999', wordBreak: 'break-all' }}>机器码：{maskCode(item.machine_code)}</div>
                  <span
                    onClick={() => handleCopyCode(item.machine_code!)}
                    style={{ flexShrink: 0, fontSize: 12, color: '#0052d9', cursor: 'pointer' }}
                  >
                    复制
                  </span>
                </div>
              )}
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginTop: 4 }}>
                <div>
                  <div style={{ fontSize: 13, color: '#666' }}>有效期：{item.apply_time} ～ {item.expire_time}</div>
                  <div style={{ fontSize: 12, color: '#999' }}>申请人：{item.applicant}</div>
                  <div style={{ fontSize: 12, color: '#999' }}>允许最大车数：{item.max_vehicles != null ? item.max_vehicles : '不限制'}</div>
                </div>
                <Button size="small" theme="danger" variant="outline" onClick={() => handleRevoke(item)}>撤销</Button>
              </div>
            </div>
          ))}
          {items.length === 0 && (
            <div style={{ textAlign: 'center', padding: 40, color: '#999' }}>该项目暂无授权记录</div>
          )}

          {/* 申请授权码 */}
          <div style={{ background: '#fff', borderRadius: 8, padding: 14, marginTop: 16, boxShadow: '0 1px 3px rgba(0,0,0,0.06)' }}>
            <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>申请授权码</div>
            <ClearableInput
              value={machineCode}
              onChange={(v) => setMachineCode(String(v))}
              placeholder="请输入机器码"
              style={{ marginBottom: 10 }}
            />
            <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
              <input
                type="date"
                value={licenseStartDate}
                onChange={(e) => setLicenseStartDate(e.target.value)}
                style={{ flex: 1, border: '1px solid #dcdcdc', borderRadius: 6, padding: '10px 12px', color: licenseStartDate ? '#333' : '#bbb', fontSize: 14, outline: 'none', backgroundColor: '#fff' }}
              />
              <input
                type="date"
                value={licenseEndDate}
                onChange={(e) => setLicenseEndDate(e.target.value)}
                style={{ flex: 1, border: '1px solid #dcdcdc', borderRadius: 6, padding: '10px 12px', color: licenseEndDate ? '#333' : '#bbb', fontSize: 14, outline: 'none', backgroundColor: '#fff' }}
              />
            </div>
            <div style={{ fontSize: 13, color: '#666', marginBottom: 6 }}>允许最大车数 <span style={{ color: '#d54941' }}>*</span></div>
            <ClearableInput
              value={maxVehicles}
              onChange={(v) => setMaxVehicles(String(v).replace(/[^\d]/g, ''))}
              type="number"
              placeholder="请输入大于 0 的整数"
              style={{ marginBottom: 12 }}
            />
            <Button theme="primary" block loading={applyingLicense} onClick={handleApplyLicense}>
              申请授权码
            </Button>
          </div>

        </>
      )}
    </div>
  );
}
