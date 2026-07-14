// 资源管理 - 从 HelpDesk DocumentManagement 迁移（33KB 原文件）
import { useState, useEffect } from 'react';
import { Button, Loading, Toast } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { formatDateTime } from '@/shared/utils/url';
import { normalizeList } from '@/shared/utils/list';

interface ResourceItem { id: string; name: string; type: 'file' | 'folder'; size?: number; updated_at: string; }

export default function ResourceManage() {
  const [items, setItems] = useState<ResourceItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [path, setPath] = useState<string[]>([]);
  const request = createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin');

  const fetchItems = async (folderPath: string[] = []) => {
    setLoading(true);
    try {
      const folderParam = folderPath.length > 0 ? `?folder=${encodeURIComponent(folderPath.join('/'))}` : '';
      const data = await request(`/resource-manager/resources/${folderParam}`);
      setItems(normalizeList<ResourceItem>(data));
    } catch (err) { Toast({ message: String(err), theme: 'error' }); }
    finally { setLoading(false); }
  };

  useEffect(() => { fetchItems(); }, []);

  const navigateTo = (item: ResourceItem) => {
    if (item.type === 'folder') {
      const newPath = [...path, item.name];
      setPath(newPath);
      fetchItems(newPath);
    } else {
      // 触发文件下载
      window.open(`${API_CONFIG.ADMIN.BASE_URL}/resource-manager/resources/${item.id}/download`, '_blank');
    }
  };

  const goBack = () => {
    const newPath = path.slice(0, -1);
    setPath(newPath);
    fetchItems(newPath);
  };

  return (
    <div style={{ padding: 16 }}>
      <div style={{ marginBottom: 12 }}>
        {path.length > 0 && (
          <Button size="small" variant="outline" onClick={goBack}>← 返回上级</Button>
        )}
        <span style={{ marginLeft: 8, color: '#999', fontSize: 13 }}>/{path.join('/') || '根目录'}</span>
      </div>
      {loading ? <Loading text="加载中..." /> : (
        items.map((item) => (
          <div key={item.id} onClick={() => navigateTo(item)} style={{ background: '#fff', borderRadius: 8, padding: 14, marginBottom: 10, boxShadow: '0 1px 3px rgba(0,0,0,0.06)', cursor: 'pointer' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span>{item.type === 'folder' ? '📁' : '📄'}</span>
                <span style={{ fontWeight: 500 }}>{item.name}</span>
              </div>
              <div style={{ fontSize: 12, color: '#999' }}>
                {item.size ? `${(item.size / 1024).toFixed(1)} KB` : ''}
              </div>
            </div>
            <div style={{ fontSize: 12, color: '#bbb', marginTop: 4 }}>{formatDateTime(item.updated_at)}</div>
          </div>
        ))
      )}
    </div>
  );
}
