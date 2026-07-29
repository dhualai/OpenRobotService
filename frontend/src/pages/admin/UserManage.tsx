import { useState, useEffect, useCallback, useMemo } from 'react';
import { Button, Toast, Loading, Dialog, Input, Popup, Form, FormItem, Textarea, RadioGroup } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { normalizeList } from '@/shared/utils/list';

interface User {
  id: string;
  username: string;
  name?: string | null;
  status?: string;
  department?: string | null;
  responsibility_modules?: string[] | null;
  job_level?: number;
  duty_text?: string | null;
}

interface UserCreateData {
  username: string;
  password: string;
  name?: string;
  department?: string;
  responsibility_modules?: string[];
  job_level?: number;
  duty_text?: string;
  status?: string;
}

interface UserUpdateData {
  name?: string;
  department?: string;
  responsibility_modules?: string[];
  job_level?: number;
  duty_text?: string;
  status?: string;
  password?: string;
}

const JOB_LEVEL_OPTIONS = [
  { label: '一线', value: 1 },
  { label: '管理/审核', value: 2 },
  { label: '仅兜底', value: 3 },
];

const STATUS_OPTIONS = [
  { label: '活跃', value: 'active' },
  { label: '未激活', value: 'inactive' },
];

export default function UserManage() {
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);

  const [editVisible, setEditVisible] = useState(false);
  const [editingUsername, setEditingUsername] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);

  const [form, setForm] = useState<UserCreateData>({
    username: '',
    password: '',
    name: '',
    department: '',
    responsibility_modules: [],
    job_level: 1,
    duty_text: '',
    status: 'active',
  });

  const [keyword, setKeyword] = useState('');
  const [tagInput, setTagInput] = useState('');

  const request = useMemo(() => createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin'), []);

  const fetchUsers = useCallback(async () => {
    setLoading(true);
    try {
      const data = await request<User[]>('/users/?skip=0&limit=1000');
      setUsers(normalizeList<User>(data));
    } catch (e) {
      Toast({ message: String(e), theme: 'error' });
    } finally {
      setLoading(false);
    }
  }, [request]);

  useEffect(() => {
    fetchUsers();
  }, [fetchUsers]);

  const handleSearch = () => {
    setKeyword(keyword.trim());
  };

  const filteredUsers = useMemo(() => {
    if (!keyword) return users;
    const kw = keyword.toLowerCase();
    return users.filter(
      (u) =>
        (u.username && u.username.toLowerCase().includes(kw)) ||
        (u.name && u.name.toLowerCase().includes(kw)) ||
        (u.department && u.department.toLowerCase().includes(kw))
    );
  }, [users, keyword]);

  const openCreate = () => {
    setEditingUsername(null);
    setForm({
      username: '',
      password: '',
      name: '',
      department: '',
      responsibility_modules: [],
      job_level: 1,
      duty_text: '',
      status: 'active',
    });
    setTagInput('');
    setEditVisible(true);
  };

  const openEdit = async (user: User) => {
    setEditingUsername(user.username);
    setEditVisible(true);

    try {
      const detail = await request<User>(`/users/${user.username}/detail`);
      setForm({
        username: detail.username,
        password: '',
        name: detail.name || '',
        department: detail.department || '',
        responsibility_modules: detail.responsibility_modules || [],
        job_level: detail.job_level ?? 1,
        duty_text: detail.duty_text || '',
        status: detail.status || 'active',
      });
    } catch {
      setForm({
        username: user.username,
        password: '',
        name: user.name || '',
        department: user.department || '',
        responsibility_modules: user.responsibility_modules || [],
        job_level: user.job_level ?? 1,
        duty_text: user.duty_text || '',
        status: user.status || 'active',
      });
    }
  };

  const handleSave = async () => {
    if (!editingUsername) {
      if (!form.username.trim()) {
        Toast({ message: '请输入用户名', theme: 'warning' });
        return;
      }
      if (!form.password.trim()) {
        Toast({ message: '请输入密码', theme: 'warning' });
        return;
      }
    }

    setIsSaving(true);
    try {
      if (editingUsername) {
        const updateData: UserUpdateData = {
          name: form.name || undefined,
          department: form.department || undefined,
          responsibility_modules: form.responsibility_modules,
          job_level: form.job_level,
          duty_text: form.duty_text || undefined,
          status: form.status,
        };
        if (form.password.trim()) {
          updateData.password = form.password;
        }
        await request(`/users/${editingUsername}`, {
          method: 'PUT',
          body: JSON.stringify(updateData),
        });
        Toast({ message: '用户已更新', theme: 'success' });
      } else {
        const createData: UserCreateData = {
          username: form.username,
          password: form.password,
          name: form.name || undefined,
          department: form.department || undefined,
          responsibility_modules: form.responsibility_modules,
          job_level: form.job_level,
          duty_text: form.duty_text || undefined,
          status: form.status,
        };
        await request('/users/', {
          method: 'POST',
          body: JSON.stringify(createData),
        });
        Toast({ message: '用户已创建', theme: 'success' });
      }
      setEditVisible(false);
      fetchUsers();
    } catch (err) {
      Toast({
        message: `保存失败: ${err instanceof Error ? err.message : ''}`,
        theme: 'error',
      });
    } finally {
      setIsSaving(false);
    }
  };

  const handleDelete = (user: User) => {
    Dialog.confirm?.({
      title: '确认删除',
      content: `确定要删除用户「${user.username}」吗？此操作不可撤销。`,
      onConfirm: async () => {
        try {
          await request(`/users/${user.username}`, { method: 'DELETE' });
          Toast({ message: '已删除', theme: 'success' });
          fetchUsers();
        } catch (err) {
          Toast({
            message: `删除失败: ${err instanceof Error ? err.message : ''}`,
            theme: 'error',
          });
        }
      },
    });
  };

  const addTag = () => {
    const val = tagInput.trim();
    if (val && !form.responsibility_modules?.includes(val)) {
      setForm((prev) => ({
        ...prev,
        responsibility_modules: [...(prev.responsibility_modules || []), val],
      }));
    }
    setTagInput('');
  };

  const removeTag = (tag: string) => {
    setForm((prev) => ({
      ...prev,
      responsibility_modules: (prev.responsibility_modules || []).filter((t) => t !== tag),
    }));
  };

  const getJobLevelLabel = (level?: number) => {
    const opt = JOB_LEVEL_OPTIONS.find((o) => o.value === level);
    return opt ? opt.label : '未知';
  };

  const getJobLevelColor = (level?: number) => {
    if (level === 1) return '#2ba471';
    if (level === 2) return '#ff7d00';
    if (level === 3) return '#e34d59';
    return '#999';
  };

  if (loading) return <Loading text="加载用户列表..." />;

  return (
    <div style={{ padding: 16 }}>
      <Button theme="primary" block style={{ marginBottom: 16 }} onClick={openCreate}>
        新建用户
      </Button>

      <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
        <Input
          value={keyword}
          onChange={(v) => setKeyword(String(v))}
          placeholder="搜索用户名/姓名/部门…"
          clearable
          style={{ flex: 1 }}
        />
        <Button size="small" theme="primary" onClick={handleSearch}>搜索</Button>
      </div>

      {filteredUsers.length === 0 ? (
        <div style={{ textAlign: 'center', padding: 40, color: '#999' }}>
          {keyword ? '未找到匹配的用户' : '暂无用户，请点击"新建用户"添加'}
        </div>
      ) : (
        filteredUsers.map((user) => (
          <div
            key={user.id}
            style={{
              background: '#fff',
              borderRadius: 8,
              padding: 14,
              marginBottom: 10,
              boxShadow: '0 1px 3px rgba(0,0,0,0.06)',
            }}
          >
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'flex-start',
              }}
            >
              <div style={{ flex: 1 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                  <span style={{ fontWeight: 500, fontSize: 15 }}>{user.username}</span>
                  {user.name && (
                    <span style={{ fontSize: 13, color: '#666' }}>{user.name}</span>
                  )}
                  <span
                    style={{
                      fontSize: 11,
                      padding: '1px 6px',
                      borderRadius: 4,
                      background: user.status === 'active' ? '#e8f5e9' : '#f5f5f5',
                      color: user.status === 'active' ? '#2ba471' : '#999',
                    }}
                  >
                    {user.status === 'active' ? '活跃' : '未激活'}
                  </span>
                </div>

                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 4 }}>
                  <span
                    style={{
                      fontSize: 11,
                      padding: '2px 8px',
                      borderRadius: 4,
                      background: `${getJobLevelColor(user.job_level)}15`,
                      color: getJobLevelColor(user.job_level),
                      border: `1px solid ${getJobLevelColor(user.job_level)}30`,
                    }}
                  >
                    {getJobLevelLabel(user.job_level)}
                  </span>
                  {user.department && (
                    <span
                      style={{
                        fontSize: 11,
                        padding: '2px 8px',
                        borderRadius: 4,
                        background: '#e8f0fe',
                        color: '#0052d9',
                      }}
                    >
                      🏢 {user.department}
                    </span>
                  )}
                </div>

                {user.responsibility_modules && user.responsibility_modules.length > 0 && (
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginBottom: 4 }}>
                    {user.responsibility_modules.map((mod) => (
                      <span
                        key={mod}
                        style={{
                          fontSize: 11,
                          padding: '1px 6px',
                          borderRadius: 3,
                          background: '#f0f0f0',
                          color: '#666',
                        }}
                      >
                        {mod}
                      </span>
                    ))}
                  </div>
                )}

                {user.duty_text && (
                  <div
                    style={{
                      fontSize: 12,
                      color: '#888',
                      marginTop: 4,
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      display: '-webkit-box',
                      WebkitLineClamp: 2,
                      WebkitBoxOrient: 'vertical',
                    }}
                  >
                    📋 {user.duty_text}
                  </div>
                )}
              </div>

              <div style={{ display: 'flex', gap: 6, marginLeft: 8, flexShrink: 0 }}>
                <Button size="small" variant="outline" onClick={() => openEdit(user)}>
                  编辑
                </Button>
                <Button
                  size="small"
                  theme="danger"
                  variant="outline"
                  onClick={() => handleDelete(user)}
                >
                  删除
                </Button>
              </div>
            </div>
          </div>
        ))
      )}

      <Popup visible={editVisible} onClose={() => setEditVisible(false)} placement="bottom" showOverlay>
        <div style={{ padding: 20, maxHeight: '85vh', overflow: 'auto' }}>
          <h4 style={{ marginBottom: 16 }}>{editingUsername ? '编辑用户' : '新建用户'}</h4>

          <Form>
            <FormItem label="用户名">
              <Input
                value={form.username}
                onChange={(v) => setForm((p) => ({ ...p, username: String(v) }))}
                placeholder="登录账号"
                clearable
                disabled={!!editingUsername}
              />
            </FormItem>

            {!editingUsername && (
              <FormItem label="密码">
                <Input
                  value={form.password}
                  onChange={(v) => setForm((p) => ({ ...p, password: String(v) }))}
                  placeholder="初始密码"
                  type="password"
                  clearable
                />
              </FormItem>
            )}

            {editingUsername && (
              <FormItem label="重置密码">
                <Input
                  value={form.password}
                  onChange={(v) => setForm((p) => ({ ...p, password: String(v) }))}
                  placeholder="留空则不修改"
                  type="password"
                  clearable
                />
              </FormItem>
            )}

            <FormItem label="姓名">
              <Input
                value={form.name || ''}
                onChange={(v) => setForm((p) => ({ ...p, name: String(v) }))}
                placeholder="真实姓名"
                clearable
              />
            </FormItem>

            <FormItem label="部门">
              <Input
                value={form.department || ''}
                onChange={(v) => setForm((p) => ({ ...p, department: String(v) }))}
                placeholder="部门/团队"
                clearable
              />
            </FormItem>

            <FormItem label="职级">
              <RadioGroup
                value={form.job_level}
                onChange={(v) => setForm((p) => ({ ...p, job_level: v as number }))}
                options={JOB_LEVEL_OPTIONS}
              />
            </FormItem>

            <FormItem label="状态">
              <RadioGroup
                value={form.status}
                onChange={(v) => setForm((p) => ({ ...p, status: v as string }))}
                options={STATUS_OPTIONS}
              />
            </FormItem>

            <FormItem label="责任模块">
              <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
                <Input
                  value={tagInput}
                  onChange={(v) => setTagInput(String(v))}
                  placeholder="输入后点击添加"
                  clearable
                  style={{ flex: 1 }}
                />
                <Button size="small" variant="outline" onClick={addTag}>
                  添加
                </Button>
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                {(form.responsibility_modules || []).map((mod) => (
                  <span
                    key={mod}
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: 4,
                      fontSize: 12,
                      padding: '3px 8px',
                      borderRadius: 4,
                      background: '#e8f0fe',
                      color: '#0052d9',
                    }}
                  >
                    {mod}
                    <span
                      style={{ cursor: 'pointer', fontWeight: 'bold' }}
                      onClick={() => removeTag(mod)}
                    >
                      ×
                    </span>
                  </span>
                ))}
                {(!form.responsibility_modules || form.responsibility_modules.length === 0) && (
                  <span style={{ fontSize: 12, color: '#bbb' }}>暂未设置</span>
                )}
              </div>
            </FormItem>

            <FormItem label="职责画像">
              <Textarea
                value={form.duty_text || ''}
                onChange={(v) => setForm((p) => ({ ...p, duty_text: String(v) }))}
                placeholder="供 AI 派单匹配参考的职责描述…"
                autosize
                maxlength={500}
              />
            </FormItem>

            <FormItem>
              <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
                <Button theme="default" block onClick={() => setEditVisible(false)}>
                  取消
                </Button>
                <Button theme="primary" block onClick={handleSave} loading={isSaving}>
                  保存
                </Button>
              </div>
            </FormItem>
          </Form>
        </div>
      </Popup>
    </div>
  );
}