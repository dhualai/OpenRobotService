// 文件资源浏览器 - 基于 folder_id 驱动的树形浏览，支持下载/分享
import { useState, useEffect, useCallback } from 'react';
import type { CSSProperties } from 'react';
import { Button, Loading, Toast, DialogPlugin } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { formatDateTime } from '@/shared/utils/url';
import { normalizeList } from '@/shared/utils/list';

// 后端 Child schema：resource-folders/root/children 与 resource-folders/{id}/children 的返回项
interface ChildItem {
  id: number;
  name: string;
  child_type: 'folder' | 'resource';
  updated_at?: string;
  resource_size?: number;
  storage_type?: string; // MINIO | OSS
  resource_status?: string;
}

// 面包屑节点：id=null 表示根目录
interface Breadcrumb {
  id: number | null;
  name: string;
}

// 后端 ResourceFolderResponse 中的统计字段（仅取展示所需子集）
interface FolderDetail {
  id: number;
  folder_name: string;
  folder_count: number;
  direct_resource_count: number;
  resource_count: number;
  total_size: number;
}

// POST /resources/sync-oss 的返回结构
interface SyncOssResult {
  status: 'success' | 'error';
  message?: string;
  oss_files_count?: number;
  db_resources_count?: number;
  added?: number;
  deleted?: number;
  updated?: number;
}

const ADMIN_BASE = API_CONFIG.ADMIN.BASE_URL;
const FOLDERS_PREFIX = '/resource-manager/resource-folders';
const RESOURCES_PREFIX = '/resource-manager/resources';

function formatSize(bytes?: number): string {
  if (bytes == null) return '';
  if (bytes === 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let n = bytes;
  let i = 0;
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024;
    i++;
  }
  return `${n.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

export default function FileExplorer() {
  const [items, setItems] = useState<ChildItem[]>([]);
  const [breadcrumb, setBreadcrumb] = useState<Breadcrumb[]>([{ id: null, name: '根目录' }]);
  const [folderInfo, setFolderInfo] = useState<FolderDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [sharingId, setSharingId] = useState<number | null>(null);
  const [syncing, setSyncing] = useState(false);
  const request = createRequest(ADMIN_BASE, 'Admin');

  // 拉取当前层级的子节点。skipCache 保证文件列表实时性
  const fetchFolder = useCallback(
    async (crumb: Breadcrumb) => {
      console.log('[FileExplorer] fetchFolder start, crumb=', crumb);
      setLoading(true);
      try {
        let children: ChildItem[];
        if (crumb.id == null) {
          children = await request<ChildItem[]>(`${FOLDERS_PREFIX}/root/children?storage_type=OSS`, { skipCache: true });
          setFolderInfo(null);
        } else {
          children = await request<ChildItem[]>(`${FOLDERS_PREFIX}/${crumb.id}/children?storage_type=OSS`, { skipCache: true });
          // 并行获取文件夹统计信息（失败不影响列表展示）
          request<FolderDetail>(`${FOLDERS_PREFIX}/${crumb.id}`, { skipCache: true })
            .then(setFolderInfo)
            .catch(() => setFolderInfo(null));
        }
        const normalized = normalizeList<ChildItem>(children);
        console.log('[FileExplorer] fetchFolder ok, count=', normalized.length, normalized);
        setItems(normalized);
      } catch (err) {
        console.error('[FileExplorer] fetchFolder error', err);
        Toast({ message: String(err), theme: 'error' });
        setItems([]);
      } finally {
        setLoading(false);
      }
    },
    [request],
  );

  useEffect(() => {
    console.log('[FileExplorer] mounted, breadcrumb=', breadcrumb);
    fetchFolder(breadcrumb[breadcrumb.length - 1]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const enterFolder = (item: ChildItem) => {
    const next = [...breadcrumb, { id: item.id, name: item.name }];
    setBreadcrumb(next);
    fetchFolder(next[next.length - 1]);
  };

  const jumpTo = (index: number) => {
    if (index === breadcrumb.length - 1) return;
    const next = breadcrumb.slice(0, index + 1);
    setBreadcrumb(next);
    fetchFolder(next[next.length - 1]);
  };

  const download = (item: ChildItem) => {
    // 代理下载端点直接返回文件流，支持浏览器内预览/下载
    window.open(`${ADMIN_BASE}${RESOURCES_PREFIX}/${item.id}/download`, '_blank');
  };

  // 分享：获取 60 分钟有效的预签名 URL 并复制到剪贴板
  const share = async (item: ChildItem) => {
    setSharingId(item.id);
    try {
      const res = await request<{ download_url?: string }>(
        `${RESOURCES_PREFIX}/${item.id}/download-url?expires_minutes=60`,
        { skipCache: true },
      );
      const url = res?.download_url;
      if (!url) throw new Error('未获取到下载链接');

      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(url);
      } else {
        const ta = document.createElement('textarea');
        ta.value = url;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
      }
      Toast({ message: '下载链接已复制（60 分钟内有效）', theme: 'success' });
    } catch (err) {
      Toast({ message: `分享失败: ${String(err)}`, theme: 'error' });
    } finally {
      setSharingId(null);
    }
  };

  // OSS ↔ DB 同步：对比 OSS 文件与数据库，自动新增缺失、软删除多余
  const runSyncOss = async () => {
    const currentFolder = breadcrumb[breadcrumb.length - 1];
    setSyncing(true);
    try {
      const query =
        currentFolder.id != null
          ? `?folder_id=${currentFolder.id}&owner_id=system`
          : `?owner_id=system`;
      const res = await request<SyncOssResult>(`${RESOURCES_PREFIX}/sync-oss${query}`, {
        method: 'POST',
        skipCache: true,
      });
      if (res?.status === 'error') {
        Toast({ message: res.message || '同步失败', theme: 'error' });
      } else {
        Toast({
          message: `同步完成：OSS ${res?.oss_files_count ?? 0} 个 · 新增 ${res?.added ?? 0} · 删除 ${res?.deleted ?? 0}`,
          theme: 'success',
        });
        await fetchFolder(currentFolder); // 刷新当前列表
      }
    } catch (err) {
      Toast({ message: `同步失败: ${String(err)}`, theme: 'error' });
    } finally {
      setSyncing(false);
    }
  };

  const handleSyncOss = () => {
    const currentFolder = breadcrumb[breadcrumb.length - 1];
    const scope = currentFolder.id == null ? '全部 OSS 资源' : `当前文件夹「${currentFolder.name}」`;
    const dlg = DialogPlugin.confirm!({
      title: '同步 OSS 资源',
      content: `将对比 OSS 与数据库：OSS 有 DB 无则新增，DB 有 OSS 无则软删除。\n同步范围：${scope}\n确认执行？`,
      confirmBtn: '开始同步',
      cancelBtn: '取消',
      onConfirm: () => {
        dlg.destroy();
        void runSyncOss();
      },
    });
  };

  // 文件夹优先，同类按名称排序
  const sorted = [...items].sort((a, b) => {
    if (a.child_type !== b.child_type) return a.child_type === 'folder' ? -1 : 1;
    return (a.name || '').localeCompare(b.name || '');
  });

  return (
    <div style={{ padding: 12 }}>
      {/* 操作栏 */}
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 8 }}>
        <Button size="small" variant="outline" loading={syncing} onClick={handleSyncOss}>
          🔄 同步 OSS
        </Button>
      </div>

      {/* 面包屑导航 */}
      <div style={breadcrumbStyle}>
        {breadcrumb.map((c, i) => {
          const isLast = i === breadcrumb.length - 1;
          const clickable = !isLast;
          return (
            <span key={`${c.id}-${i}`} style={{ display: 'inline-flex', alignItems: 'center' }}>
              {i > 0 && <span style={{ margin: '0 4px', color: '#ccc' }}>/</span>}
              <span
                onClick={() => clickable && jumpTo(i)}
                style={{
                  cursor: clickable ? 'pointer' : 'default',
                  color: clickable ? '#0052d9' : '#333',
                  fontWeight: clickable ? 400 : 600,
                }}
              >
                {c.name}
              </span>
            </span>
          );
        })}
      </div>

      {/* 当前文件夹统计信息 */}
      {folderInfo && (
        <div style={infoBarStyle}>
          <span>📁 {folderInfo.folder_count} 子文件夹</span>
          <span>📄 {folderInfo.direct_resource_count} 个文件</span>
          <span>📦 总大小 {formatSize(folderInfo.total_size)}</span>
        </div>
      )}

      {loading ? (
        <Loading text="加载中..." />
      ) : sorted.length === 0 ? (
        <div style={emptyStyle}>此文件夹为空</div>
      ) : (
        sorted.map((item) => {
          const isFolder = item.child_type === 'folder';
          return (
            <div key={`${item.child_type}-${item.id}`} style={isFolder ? folderRowStyle : fileRowStyle}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0, flex: 1 }}>
                <span style={{ fontSize: 18 }}>{isFolder ? '📁' : '📄'}</span>
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div
                    onClick={() => isFolder && enterFolder(item)}
                    style={{
                      fontWeight: 500,
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                      cursor: isFolder ? 'pointer' : 'default',
                    }}
                  >
                    {item.name}
                  </div>
                  <div style={{ fontSize: 11, color: '#bbb', marginTop: 2 }}>
                    {formatDateTime(item.updated_at ?? '')}
                    {item.storage_type ? ` · ${item.storage_type}` : ''}
                    {item.resource_size != null ? ` · ${formatSize(item.resource_size)}` : ''}
                  </div>
                </div>
              </div>

              {!isFolder && (
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
                  <Button
                    size="small"
                    variant="outline"
                    loading={sharingId === item.id}
                    onClick={(e: React.MouseEvent) => {
                      e.stopPropagation();
                      share(item);
                    }}
                  >
                    分享
                  </Button>
                  <Button
                    size="small"
                    theme="primary"
                    onClick={(e: React.MouseEvent) => {
                      e.stopPropagation();
                      download(item);
                    }}
                  >
                    下载
                  </Button>
                </div>
              )}
            </div>
          );
        })
      )}
    </div>
  );
}

const breadcrumbStyle: CSSProperties = {
  display: 'flex',
  flexWrap: 'wrap',
  alignItems: 'center',
  marginBottom: 10,
  fontSize: 13,
  lineHeight: 1.6,
};

const infoBarStyle: CSSProperties = {
  display: 'flex',
  gap: 16,
  background: '#f5f7fa',
  borderRadius: 6,
  padding: '8px 10px',
  marginBottom: 10,
  fontSize: 12,
  color: '#888',
};

const emptyStyle: CSSProperties = {
  textAlign: 'center',
  color: '#bbb',
  padding: '48px 0',
  fontSize: 13,
};

const folderRowStyle: CSSProperties = {
  background: '#fff',
  borderRadius: 8,
  padding: 14,
  marginBottom: 10,
  boxShadow: '0 1px 3px rgba(0,0,0,0.06)',
  display: 'flex',
  alignItems: 'center',
  gap: 8,
  cursor: 'pointer',
};

const fileRowStyle: CSSProperties = {
  background: '#fff',
  borderRadius: 8,
  padding: 14,
  marginBottom: 10,
  boxShadow: '0 1px 3px rgba(0,0,0,0.06)',
  display: 'flex',
  alignItems: 'center',
  gap: 8,
};
