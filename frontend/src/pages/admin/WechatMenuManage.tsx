import { useState, useEffect } from 'react';
import { Button, Toast, Loading, Tag, Dialog, Input, Popup } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';

interface MenuButton {
  type: string;
  name: string;
  key?: string;
  url?: string;
  sub_button?: MenuButton[];
}

interface MenuData {
  button: MenuButton[];
}

export default function WechatMenuManage() {
  const [menuData, setMenuData] = useState<MenuData | null>(null);
  const [loading, setLoading] = useState(false);
  const [showCreateDialog, setShowCreateDialog] = useState(false);
  const [newMenuName, setNewMenuName] = useState('');
  const [newMenuType, setNewMenuType] = useState('click');
  const [newMenuKey, setNewMenuKey] = useState('');
  const [newMenuUrl, setNewMenuUrl] = useState('');

  const request = createRequest(API_CONFIG.WECHAT.BASE_URL, 'Wechat');

  const fetchMenu = async () => {
    setLoading(true);
    try {
      const response = await request<{ data: { menu: MenuData } }>('/get_menu');
      setMenuData(response.data?.menu ?? null);
    } catch (err) {
      Toast({ message: `获取菜单失败: ${err instanceof Error ? err.message : '未知错误'}`, theme: 'error' });
    } finally {
      setLoading(false);
    }
  };

  const createMenu = async () => {
    if (!newMenuName.trim()) {
      Toast({ message: '请输入菜单名称', theme: 'warning' });
      return;
    }
    if (newMenuType === 'click' && !newMenuKey.trim()) {
      Toast({ message: '点击类型菜单请输入菜单KEY', theme: 'warning' });
      return;
    }
    if (newMenuType === 'view' && !newMenuUrl.trim()) {
      Toast({ message: '跳转类型菜单请输入URL', theme: 'warning' });
      return;
    }

    const hasExistingMenu = menuData && menuData.button && menuData.button.length > 0;

    if (hasExistingMenu) {
      Dialog.confirm?.({
        title: '覆盖确认',
        content: '创建新菜单将覆盖当前所有菜单，确定要继续吗？',
        onConfirm: async () => {
          await submitMenu();
        },
      });
    } else {
      await submitMenu();
    }
  };

  const submitMenu = async () => {
    setLoading(true);
    try {
      const button: MenuButton = {
        type: newMenuType,
        name: newMenuName.trim(),
        ...(newMenuType === 'click' && { key: newMenuKey.trim() }),
        ...(newMenuType === 'view' && { url: newMenuUrl.trim() }),
      };

      await request('/create_menu', {
        method: 'POST',
        body: JSON.stringify({ button: [button] }),
      });

      Toast({ message: '菜单创建成功', theme: 'success' });
      setShowCreateDialog(false);
      setNewMenuName('');
      setNewMenuType('click');
      setNewMenuKey('');
      setNewMenuUrl('');
      fetchMenu();
    } catch (err) {
      Toast({ message: `创建菜单失败: ${err instanceof Error ? err.message : '未知错误'}`, theme: 'error' });
    } finally {
      setLoading(false);
    }
  };

  const deleteMenu = async () => {
    Dialog.confirm?.({
      title: '确认删除',
      content: '删除后需要重新创建菜单，确定要删除吗？',
      onConfirm: async () => {
        setLoading(true);
        try {
          await request('/delete_menu', { method: 'DELETE' });
          Toast({ message: '菜单删除成功', theme: 'success' });
          setMenuData(null);
        } catch (err) {
          Toast({ message: `删除菜单失败: ${err instanceof Error ? err.message : '未知错误'}`, theme: 'error' });
        } finally {
          setLoading(false);
        }
      },
    });
  };

  useEffect(() => {
    fetchMenu();
  }, []);

  const renderMenuButton = (btn: MenuButton, level = 0) => (
    <div key={btn.key || btn.name} style={{ paddingLeft: level * 20, marginBottom: 12 }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '10px 16px',
          backgroundColor: '#f8f9fa',
          borderRadius: 8,
        }}
      >
        <div>
          <div style={{ fontWeight: 500 }}>{btn.name}</div>
          <div style={{ fontSize: 12, color: '#999', marginTop: 4 }}>
            <span>类型: {btn.type === 'click' ? '点击' : btn.type === 'view' ? '跳转' : btn.type}</span>
            {btn.key && <span style={{ marginLeft: 8 }}>KEY: {btn.key}</span>}
            {btn.url && <span style={{ marginLeft: 8 }}>URL: {btn.url}</span>}
          </div>
        </div>
        {btn.sub_button && btn.sub_button.length > 0 && (
          <Tag variant="outline">子菜单({btn.sub_button.length})</Tag>
        )}
      </div>
      {btn.sub_button && btn.sub_button.map((subBtn) => renderMenuButton(subBtn, level + 1))}
    </div>
  );

  return (
    <div style={{ padding: 16 }}>
      <div style={{ display: 'flex', gap: 12, marginBottom: 20 }}>
        <Button theme="primary" block onClick={() => setShowCreateDialog(true)}>
          + 创建菜单
        </Button>
        {menuData && (
          <Button theme="danger" block onClick={deleteMenu}>
            删除菜单
          </Button>
        )}
        <Button variant="outline" block onClick={fetchMenu}>
          刷新
        </Button>
      </div>

      {loading && <Loading text="加载中..." />}

      {!loading && menuData && menuData.button && menuData.button.length > 0 && (
        <div>
          <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 16 }}>当前菜单</h3>
          {menuData.button.map((btn) => renderMenuButton(btn))}
        </div>
      )}

      {!loading && (!menuData || !menuData.button || menuData.button.length === 0) && (
        <div style={{ textAlign: 'center', padding: '40px 0', color: '#999' }}>
          <div style={{ fontSize: 48, marginBottom: 16 }}>📋</div>
          <p>暂无菜单</p>
          <p style={{ fontSize: 12, marginTop: 8 }}>点击上方按钮创建微信公众号菜单</p>
        </div>
      )}

      <Popup
        visible={showCreateDialog}
        onClose={() => setShowCreateDialog(false)}
        placement="center"
        showOverlay
      >
        <div style={{ padding: 16 }}>
          <h4 style={{ marginBottom: 16, fontSize: 16, fontWeight: 600 }}>创建菜单</h4>
          <div style={{ marginBottom: 16 }}>
            <label style={{ display: 'block', fontSize: 14, fontWeight: 500, marginBottom: 8 }}>
              菜单名称
            </label>
            <Input
              placeholder="请输入菜单名称"
              value={newMenuName}
              onChange={(v) => setNewMenuName(String(v))}
            />
          </div>

          <div style={{ marginBottom: 16 }}>
            <label style={{ display: 'block', fontSize: 14, fontWeight: 500, marginBottom: 8 }}>
              菜单类型
            </label>
            <div style={{ display: 'flex', gap: 12 }}>
              <Button
                theme={newMenuType === 'click' ? 'primary' : 'default'}
                block
                onClick={() => setNewMenuType('click')}
              >
                点击事件
              </Button>
              <Button
                theme={newMenuType === 'view' ? 'primary' : 'default'}
                block
                onClick={() => setNewMenuType('view')}
              >
                跳转链接
              </Button>
            </div>
          </div>

          {newMenuType === 'click' && (
            <div style={{ marginBottom: 16 }}>
              <label style={{ display: 'block', fontSize: 14, fontWeight: 500, marginBottom: 8 }}>
                菜单KEY
              </label>
              <Input
                placeholder="用于识别点击事件"
                value={newMenuKey}
                onChange={(v) => setNewMenuKey(String(v))}
              />
            </div>
          )}

          {newMenuType === 'view' && (
            <div style={{ marginBottom: 16 }}>
              <label style={{ display: 'block', fontSize: 14, fontWeight: 500, marginBottom: 8 }}>
                跳转URL
              </label>
              <Input
                placeholder="请输入跳转链接"
                value={newMenuUrl}
                onChange={(v) => setNewMenuUrl(String(v))}
              />
            </div>
          )}

          <div style={{ display: 'flex', gap: 12 }}>
            <Button block variant="outline" onClick={() => setShowCreateDialog(false)}>
              取消
            </Button>
            <Button block theme="primary" onClick={createMenu}>
              确认创建
            </Button>
          </div>
        </div>
      </Popup>
    </div>
  );
}