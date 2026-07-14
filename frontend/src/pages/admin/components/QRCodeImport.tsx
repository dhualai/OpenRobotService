// 二维码导入组件
import { useState } from 'react';
import { Input, Button, Toast } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';

export default function QRCodeImport() {
  const [code, setCode] = useState('');
  const [loading, setLoading] = useState(false);
  const request = createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin');
  // TODO: 新后端导入接口路径待确认

  const handleScan = async () => {
    if (!code.trim()) return;
    setLoading(true);
    try {
      await request('/data/import/qrcode', { method: 'POST', body: JSON.stringify({ code }) });
      Toast({ message: '扫描成功', theme: 'success' });
    } catch (err) {
      Toast({ message: `扫描失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally { setLoading(false); }
  };

  return (
    <div>
      <Input placeholder="输入或扫描二维码内容" value={code} onChange={(v) => setCode(String(v))} clearable />
      <Button theme="primary" block style={{ marginTop: 12 }} loading={loading} onClick={handleScan} disabled={!code.trim()}>
        确认导入
      </Button>
    </div>
  );
}
