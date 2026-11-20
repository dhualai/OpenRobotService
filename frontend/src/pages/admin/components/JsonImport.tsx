// JSON导入组件
import { useState } from 'react';
import { Textarea, Button, Toast } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';

export default function JsonImport() {
  const [jsonStr, setJsonStr] = useState('');
  const [loading, setLoading] = useState(false);
  const request = createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin');
  // TODO: 新后端导入接口路径待确认

  const handleImport = async () => {
    try {
      const parsed = JSON.parse(jsonStr);
      setLoading(true);
      await request('/data/import/json', { method: 'POST', body: JSON.stringify({ data: parsed }) });
      Toast({ message: 'JSON导入成功', theme: 'success' });
    } catch (err) {
      Toast({ message: `导入失败: ${err instanceof Error ? err.message : 'JSON格式错误'}`, theme: 'error' });
    } finally { setLoading(false); }
  };

  return (
    <div>
      <Textarea
        value={jsonStr}
        onChange={(v) => setJsonStr(String(v))}
        placeholder='[{"key": "value"}]'
        autosize={{ minRows: 6, maxRows: 15 }}
      />
      <Button theme="primary" block style={{ marginTop: 12 }} loading={loading} onClick={handleImport} disabled={!jsonStr.trim()}>
        导入JSON
      </Button>
    </div>
  );
}
