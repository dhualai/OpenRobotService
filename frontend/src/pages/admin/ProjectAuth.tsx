// 项目授权管理 - 展示授权记录、申请授权码
// 样式参考 macaron projects.auth 页：授权记录条目卡 + 幽灵按钮 + 申请面板。
import { useState, useEffect } from 'react';
import { Toast, Loading, Dialog } from 'tdesign-mobile-react';
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
          await request(`/projects/licenses/${item.id}`, { method: 'DELETE' });
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
    return <div className="mac-empty">请先选择项目</div>;
  }

  return (
    <div>
      {loading ? <Loading text="加载授权..." /> : (
        <>
          <div style={{ marginBottom: 12, fontSize: 11.5, color: 'var(--mac-muted-fg)' }}>
            <span style={{ fontWeight: 600, color: 'var(--mac-fg)' }}>{selectedProject.name}</span>
            <span> · 授权记录</span>
          </div>
          {items.map((item) => (
            <div key={item.id} className="mac-item" style={{ marginBottom: 10 }}>
              <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="mac-item__title">{maskCode(item.license_code)}</div>
                  <div style={{ marginTop: 6, display: 'flex', flexDirection: 'column', gap: 4 }}>
                    {item.machine_code && (
                      <div className="mac-labelvalue">
                        <span className="mac-labelvalue__label">机器码</span>
                        <span className="mac-labelvalue__value">{maskCode(item.machine_code)}</span>
                      </div>
                    )}
                    <div className="mac-labelvalue">
                      <span className="mac-labelvalue__label">有效期</span>
                      <span className="mac-labelvalue__value">{item.apply_time} ~ {item.expire_time}</span>
                    </div>
                    <div className="mac-labelvalue">
                      <span className="mac-labelvalue__label">申请人</span>
                      <span className="mac-labelvalue__value">{item.applicant}</span>
                    </div>
                    <div className="mac-labelvalue">
                      <span className="mac-labelvalue__label">最大车数</span>
                      <span className="mac-labelvalue__value">{item.max_vehicles != null ? item.max_vehicles : '不限制'}</span>
                    </div>
                  </div>
                </div>
                <div style={{ flexShrink: 0, display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 6 }}>
                  <button type="button" className="mac-btn mac-btn--ghost" onClick={() => handleCopyCode(item.license_code)}>
                    复制授权码
                  </button>
                  {item.machine_code && (
                    <button type="button" className="mac-btn mac-btn--ghost" onClick={() => handleCopyCode(item.machine_code!)}>
                      复制机器码
                    </button>
                  )}
                  <button type="button" className="mac-btn mac-btn--ghost" onClick={() => handleRevoke(item)}>
                    撤销
                  </button>
                </div>
              </div>
            </div>
          ))}
          {items.length === 0 && (
            <div className="mac-empty">该项目暂无授权记录</div>
          )}

          {/* 申请授权码 */}
          <div className="mac-panel" style={{ marginTop: 16 }}>
            <div style={{ fontSize: 12.5, fontWeight: 600, marginBottom: 12, color: 'var(--mac-fg)' }}>申请授权码</div>
            <input
              className="mac-input"
              style={{ marginBottom: 10 }}
              value={machineCode}
              onChange={(e) => {
                let v = e.target.value;
                // 自动补全：若不是 = 号结尾，补一个 = 号
                if (v && !v.endsWith('=')) v = v + '=';
                setMachineCode(v);
              }}
              placeholder="请输入机器码"
            />
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 12 }}>
              <input
                type="date"
                className="mac-input"
                value={licenseStartDate}
                onChange={(e) => setLicenseStartDate(e.target.value)}
              />
              <input
                type="date"
                className="mac-input"
                value={licenseEndDate}
                onChange={(e) => setLicenseEndDate(e.target.value)}
              />
            </div>
            <div style={{ fontSize: 11, color: 'var(--mac-muted-fg)', marginBottom: 6 }}>允许最大车数 <span style={{ color: '#d54941' }}>*</span></div>
            <input
              className="mac-input"
              style={{ marginBottom: 12 }}
              value={maxVehicles}
              onChange={(e) => setMaxVehicles(e.target.value.replace(/[^\d]/g, ''))}
              inputMode="numeric"
              placeholder="请输入大于 0 的整数"
            />
            <button
              type="button"
              className="mac-btn mac-btn--primary mac-btn--block"
              disabled={applyingLicense}
              onClick={handleApplyLicense}
            >
              {applyingLicense ? '申请中...' : '申请授权码'}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
