// 数据导入页 - 从 BackgroundService DataImport 迁移
import { useState } from 'react';
import { Tabs, TabPanel, Button, Upload, Toast, Loading } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';

export default function DataImport() {
  const [tab, setTab] = useState('file');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const request = createRequest(API_CONFIG.PROJECT.BASE_URL, 'Project');

  const handleFileUpload = async (file: File) => {
    setLoading(true);
    try {
      const formData = new FormData();
      formData.append('file', file);
      const data = await request<{ message: string }>('/data/import/file', {
        method: 'POST',
        body: formData,
      });
      Toast({ message: data.message || '导入成功', theme: 'success' });
    } catch (err) {
      Toast({ message: `导入失败: ${err instanceof Error ? err.message : '未知错误'}`, theme: 'error' });
    } finally {
      setLoading(false);
    }
  };

  const handleJsonImport = async (jsonStr: string) => {
    setLoading(true);
    try {
      const data = await request<{ message: string }>('/data/import/json', {
        method: 'POST',
        body: JSON.stringify({ data: JSON.parse(jsonStr) }),
      });
      Toast({ message: data.message || '导入成功', theme: 'success' });
    } catch (err) {
      Toast({ message: `导入失败: ${err instanceof Error ? err.message : '未知错误'}`, theme: 'error' });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ padding: 16 }}>
      <Tabs value={tab} onChange={(v) => setTab(String(v))}>
        <TabPanel value="file" label="文件导入">
          <div style={{ padding: '24px 0' }}>
            <Upload
              accept=".csv,.xlsx,.xls"
              max={1}
              onSuccess={({ fileList }) => {
                if (fileList?.[0]?.raw) handleFileUpload(fileList[0].raw);
              }}
            />
            <p style={{ color: '#999', fontSize: 13, marginTop: 12, textAlign: 'center' }}>
              支持 CSV、Excel 格式
            </p>
          </div>
        </TabPanel>
        <TabPanel value="json" label="JSON导入">
          <div style={{ padding: '24px 0' }}>
            <textarea
              placeholder='[{"name": "...", "value": ...}]'
              rows={8}
              style={{ width: '100%', padding: 12, border: '1px solid #ddd', borderRadius: 8, fontSize: 14 }}
              onChange={(e) => { /* store for submit */ }}
            />
            <Button theme="primary" block style={{ marginTop: 16 }} onClick={() => handleJsonImport('[{}]')}>
              导入JSON数据
            </Button>
          </div>
        </TabPanel>
      </Tabs>
      {loading && <Loading text="导入中..." />}
    </div>
  );
}
