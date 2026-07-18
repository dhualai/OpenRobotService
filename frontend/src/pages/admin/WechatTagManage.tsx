import { useState, useEffect } from 'react';
import { Button, Toast, Loading, Tag, Dialog, Input, Popup } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';

interface TagItem {
  id: number;
  name: string;
  count: number;
}

type BatchOperation = 'tagging' | 'untagging';

export default function WechatTagManage() {
  const [tags, setTags] = useState<TagItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [showCreateDialog, setShowCreateDialog] = useState(false);
  const [showEditDialog, setShowEditDialog] = useState(false);
  const [showBatchDialog, setShowBatchDialog] = useState(false);
  const [batchOperation, setBatchOperation] = useState<BatchOperation>('tagging');
  const [newTagName, setNewTagName] = useState('');
  const [editingTag, setEditingTag] = useState<TagItem | null>(null);
  const [batchOpenids, setBatchOpenids] = useState('');
  const [selectedTagId, setSelectedTagId] = useState<number | null>(null);

  const request = createRequest(API_CONFIG.WECHAT.BASE_URL, 'Wechat');

  const fetchTags = async () => {
    setLoading(true);
    try {
      const data = await request<{ tags: TagItem[] }>('/tag');
      setTags(data.tags || []);
    } catch (err) {
      Toast({ message: `获取标签失败: ${err instanceof Error ? err.message : '未知错误'}`, theme: 'error' });
    } finally {
      setLoading(false);
    }
  };

  const createTag = async () => {
    if (!newTagName.trim()) {
      Toast({ message: '请输入标签名称', theme: 'warning' });
      return;
    }

    setLoading(true);
    try {
      await request('/tag', {
        method: 'POST',
        body: JSON.stringify({ name: newTagName.trim() }),
      });

      Toast({ message: '标签创建成功', theme: 'success' });
      setShowCreateDialog(false);
      setNewTagName('');
      fetchTags();
    } catch (err) {
      Toast({ message: `创建标签失败: ${err instanceof Error ? err.message : '未知错误'}`, theme: 'error' });
    } finally {
      setLoading(false);
    }
  };

  const updateTag = async () => {
    if (!editingTag || !newTagName.trim()) {
      Toast({ message: '请输入标签名称', theme: 'warning' });
      return;
    }

    setLoading(true);
    try {
      await request(`/tag/${editingTag.id}`, {
        method: 'PUT',
        body: JSON.stringify({ name: newTagName.trim() }),
      });

      Toast({ message: '标签更新成功', theme: 'success' });
      setShowEditDialog(false);
      setEditingTag(null);
      setNewTagName('');
      fetchTags();
    } catch (err) {
      Toast({ message: `更新标签失败: ${err instanceof Error ? err.message : '未知错误'}`, theme: 'error' });
    } finally {
      setLoading(false);
    }
  };

  const deleteTag = async (tag: TagItem) => {
    Dialog.confirm({
      title: '确认删除',
      content: `确定要删除标签「${tag.name}」吗？删除后无法恢复。`,
      onConfirm: async () => {
        setLoading(true);
        try {
          await request(`/tag/${tag.id}`, { method: 'DELETE' });
          Toast({ message: '标签删除成功', theme: 'success' });
          fetchTags();
        } catch (err) {
          Toast({ message: `删除标签失败: ${err instanceof Error ? err.message : '未知错误'}`, theme: 'error' });
        } finally {
          setLoading(false);
        }
      },
    });
  };

  const batchTagging = async () => {
    if (!selectedTagId || !batchOpenids.trim()) {
      Toast({ message: '请选择标签并输入openid列表', theme: 'warning' });
      return;
    }

    const openidList = batchOpenids.split(/[\n,，\s]+/).filter(Boolean);
    if (openidList.length === 0) {
      Toast({ message: '请输入有效的openid', theme: 'warning' });
      return;
    }
    if (openidList.length > 100) {
      Toast({ message: '每次最多支持100个openid', theme: 'warning' });
      return;
    }

    setLoading(true);
    try {
      await request('/tag/batch-tagging', {
        method: 'POST',
        body: JSON.stringify({ tag_id: selectedTagId, openid_list: openidList }),
      });

      Toast({ message: '批量打标签成功', theme: 'success' });
      setShowBatchDialog(false);
      setBatchOpenids('');
      setSelectedTagId(null);
      fetchTags();
    } catch (err) {
      Toast({ message: `批量打标签失败: ${err instanceof Error ? err.message : '未知错误'}`, theme: 'error' });
    } finally {
      setLoading(false);
    }
  };

  const batchUntagging = async () => {
    if (!selectedTagId || !batchOpenids.trim()) {
      Toast({ message: '请选择标签并输入openid列表', theme: 'warning' });
      return;
    }

    const openidList = batchOpenids.split(/[\n,，\s]+/).filter(Boolean);
    if (openidList.length === 0) {
      Toast({ message: '请输入有效的openid', theme: 'warning' });
      return;
    }

    setLoading(true);
    try {
      await request('/tag/batch-untagging', {
        method: 'POST',
        body: JSON.stringify({ tag_id: selectedTagId, openid_list: openidList }),
      });

      Toast({ message: '批量取消标签成功', theme: 'success' });
      setShowBatchDialog(false);
      setBatchOpenids('');
      setSelectedTagId(null);
      fetchTags();
    } catch (err) {
      Toast({ message: `批量取消标签失败: ${err instanceof Error ? err.message : '未知错误'}`, theme: 'error' });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchTags();
  }, []);

  return (
    <div style={{ padding: 16 }}>
      <div style={{ display: 'flex', gap: 12, marginBottom: 20 }}>
        <Button theme="primary" block onClick={() => setShowCreateDialog(true)}>
          + 创建标签
        </Button>
        <Button variant="outline" block onClick={fetchTags}>
          刷新
        </Button>
      </div>

      <div style={{ display: 'flex', gap: 12, marginBottom: 20 }}>
        <Button variant="outline" block onClick={() => { setShowBatchDialog(true); setBatchOperation('tagging'); setSelectedTagId(null); }}>
          批量打标签
        </Button>
        <Button variant="outline" block onClick={() => { setShowBatchDialog(true); setBatchOperation('untagging'); setSelectedTagId(null); }}>
          批量取消标签
        </Button>
      </div>

      {loading && <Loading text="加载中..." />}

      {!loading && tags.length > 0 && (
        <div style={{ border: '1px solid #eee', borderRadius: 8, overflow: 'hidden' }}>
          {tags.map((tag, index) => (
            <div
              key={tag.id}
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                padding: '12px 16px',
                borderBottom: index < tags.length - 1 ? '1px solid #f5f5f5' : 'none',
                backgroundColor: '#fff',
              }}
            >
              <div style={{ flex: 1 }}>
                <div style={{ fontWeight: 500 }}>{tag.name}</div>
                <div style={{ fontSize: 12, color: '#999', marginTop: 4 }}>
                  {tag.count} 个用户
                </div>
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <Button size="small" variant="outline" onClick={() => {
                  setEditingTag(tag);
                  setNewTagName(tag.name);
                  setShowEditDialog(true);
                }}>
                  编辑
                </Button>
                <Button size="small" theme="danger" onClick={() => deleteTag(tag)}>
                  删除
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}

      {!loading && tags.length === 0 && (
        <div style={{ textAlign: 'center', padding: '40px 0', color: '#999' }}>
          <div style={{ fontSize: 48, marginBottom: 16 }}>🏷️</div>
          <p>暂无标签</p>
          <p style={{ fontSize: 12, marginTop: 8 }}>点击上方按钮创建用户标签</p>
        </div>
      )}

      <Popup
        visible={showCreateDialog}
        onClose={() => setShowCreateDialog(false)}
        title="创建标签"
        placement="center"
      >
        <div style={{ padding: 16 }}>
          <div style={{ marginBottom: 16 }}>
            <label style={{ display: 'block', fontSize: 14, fontWeight: 500, marginBottom: 8 }}>
              标签名称
            </label>
            <Input
              placeholder="请输入标签名称"
              value={newTagName}
              onChange={(v) => setNewTagName(String(v))}
            />
          </div>

          <div style={{ display: 'flex', gap: 12 }}>
            <Button block variant="outline" onClick={() => setShowCreateDialog(false)}>
              取消
            </Button>
            <Button block theme="primary" onClick={createTag}>
              确认创建
            </Button>
          </div>
        </div>
      </Popup>

      <Popup
        visible={showEditDialog}
        onClose={() => { setShowEditDialog(false); setEditingTag(null); }}
        title="编辑标签"
        placement="center"
      >
        <div style={{ padding: 16 }}>
          <div style={{ marginBottom: 16 }}>
            <label style={{ display: 'block', fontSize: 14, fontWeight: 500, marginBottom: 8 }}>
              标签名称
            </label>
            <Input
              placeholder="请输入标签名称"
              value={newTagName}
              onChange={(v) => setNewTagName(String(v))}
            />
          </div>

          <div style={{ display: 'flex', gap: 12 }}>
            <Button block variant="outline" onClick={() => { setShowEditDialog(false); setEditingTag(null); }}>
              取消
            </Button>
            <Button block theme="primary" onClick={updateTag}>
              确认更新
            </Button>
          </div>
        </div>
      </Popup>

      <Popup
        visible={showBatchDialog}
        onClose={() => { setShowBatchDialog(false); setSelectedTagId(null); }}
        title={batchOperation === 'tagging' ? '批量打标签' : '批量取消标签'}
        placement="center"
      >
        <div style={{ padding: 16 }}>
          <div style={{ marginBottom: 16 }}>
            <label style={{ display: 'block', fontSize: 14, fontWeight: 500, marginBottom: 8 }}>
              选择标签
            </label>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
              {tags.map((tag) => (
                <Tag
                  key={tag.id}
                  variant={selectedTagId === tag.id ? 'solid' : 'outline'}
                  closable={false}
                  onClick={() => setSelectedTagId(tag.id)}
                  style={{ cursor: 'pointer' }}
                >
                  {tag.name}
                </Tag>
              ))}
            </div>
          </div>

          <div style={{ marginBottom: 16 }}>
            <label style={{ display: 'block', fontSize: 14, fontWeight: 500, marginBottom: 8 }}>
              openid列表（每行一个或用逗号分隔）
            </label>
            <textarea
              placeholder="openid1&#10;openid2&#10;openid3"
              rows={4}
              style={{ width: '100%', padding: 12, border: '1px solid #ddd', borderRadius: 8, fontSize: 14 }}
              value={batchOpenids}
              onChange={(e) => setBatchOpenids(e.target.value)}
            />
          </div>

          <div style={{ display: 'flex', gap: 12 }}>
            <Button block variant="outline" onClick={() => { setShowBatchDialog(false); setSelectedTagId(null); }}>
              取消
            </Button>
            <Button 
              block 
              theme={batchOperation === 'tagging' ? 'primary' : 'danger'} 
              onClick={batchOperation === 'tagging' ? batchTagging : batchUntagging}
            >
              {batchOperation === 'tagging' ? '确认打标签' : '确认取消'}
            </Button>
          </div>
        </div>
      </Popup>
    </div>
  );
}