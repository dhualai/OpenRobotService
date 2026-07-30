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
  responsibility_modules?: Record<string, string[]> | null;
  job_level?: number;
  duty_text?: string | null;
  permissions?: string[];
  roles?: Record<string, string[]>;
  projectPermissions?: Record<string, Record<string, string[]>>;
  external_credentials?: Record<string, Record<string, string>>;
  avatar_resource_id?: number | null;
}

interface UserCreateData {
  username: string;
  password: string;
  name?: string;
  department?: string;
  responsibility_modules?: Record<string, string[]>;
  job_level?: number;
  duty_text?: string;
  status?: string;
}

interface UserUpdateData {
  name?: string;
  department?: string;
  responsibility_modules?: Record<string, string[]>;
  job_level?: number;
  duty_text?: string;
  status?: string;
  password?: string;
}

interface ModuleEntry {
  module: string;
  keywords: string[];
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

const modulesToEntries = (mods?: Record<string, string[]> | null): ModuleEntry[] => {
  if (!mods) return [];
  return Object.entries(mods).map(([module, keywords]) => ({
    module,
    keywords: Array.isArray(keywords) ? [...keywords] : [],
  }));
};

const entriesToModules = (entries: ModuleEntry[]): Record<string, string[]> | undefined => {
  const result: Record<string, string[]> = {};
  for (const e of entries) {
    const key = e.module.trim();
    if (key) {
      const kws = e.keywords.map((k) => k.trim()).filter(Boolean);
      if (kws.length > 0) {
        result[key] = kws;
      }
    }
  }
  return Object.keys(result).length > 0 ? result : undefined;
};

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
    responsibility_modules: undefined,
    job_level: 1,
    duty_text: '',
    status: 'active',
  });

  const [moduleEntries, setModuleEntries] = useState<ModuleEntry[]>([]);
  const [keywordInputs, setKeywordInputs] = useState<Record<number, string>>({});

  const [keyword, setKeyword] = useState('');

  const [editLoading, setEditLoading] = useState(false);
  const [detailVisible, setDetailVisible] = useState(false);
  const [detailUser, setDetailUser] = useState<User | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

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
      responsibility_modules: undefined,
      job_level: 1,
      duty_text: '',
      status: 'active',
    });
    setModuleEntries([]);
    setKeywordInputs({});
    setEditVisible(true);
  };

  const openEdit = async (user: User) => {
    setEditingUsername(user.username);
    setEditLoading(true);

    try {
      const detail = await request<User>(`/users/${user.username}/detail`);
      setForm({
        username: detail.username,
        password: '',
        name: detail.name || '',
        department: detail.department || '',
        responsibility_modules: detail.responsibility_modules || undefined,
        job_level: detail.job_level ?? 1,
        duty_text: detail.duty_text || '',
        status: detail.status || 'active',
      });
      setModuleEntries(modulesToEntries(detail.responsibility_modules));
    } catch {
      setForm({
        username: user.username,
        password: '',
        name: user.name || '',
        department: user.department || '',
        responsibility_modules: user.responsibility_modules || undefined,
        job_level: user.job_level ?? 1,
        duty_text: user.duty_text || '',
        status: user.status || 'active',
      });
      setModuleEntries(modulesToEntries(user.responsibility_modules));
    }
    setKeywordInputs({});
    setEditLoading(false);
    setEditVisible(true);
  };

  const openDetail = async (user: User) => {
    setDetailUser(user);
    setDetailVisible(true);
    setDetailLoading(true);
    try {
      const detail = await request<User>(`/users/${user.username}/detail`);
      setDetailUser(detail);
    } catch {
      Toast({ message: '加载详情失败', theme: 'error' });
    } finally {
      setDetailLoading(false);
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

    const modules = entriesToModules(moduleEntries);

    setIsSaving(true);
    try {
      if (editingUsername) {
        const updateData: UserUpdateData = {
          name: form.name || undefined,
          department: form.department || undefined,
          responsibility_modules: modules,
          job_level: form.job_level,
          duty_text: form.duty_text || undefined,
        };
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
          responsibility_modules: modules,
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

  const addModule = () => {
    setModuleEntries((prev) => [...prev, { module: '', keywords: [] }]);
  };

  const removeModule = (index: number) => {
    setModuleEntries((prev) => prev.filter((_, i) => i !== index));
    setKeywordInputs((prev) => {
      const next: Record<number, string> = {};
      for (const [k, v] of Object.entries(prev)) {
        const ki = Number(k);
        next[ki > index ? ki - 1 : ki] = v;
      }
      return next;
    });
  };

  const updateModuleName = (index: number, name: string) => {
    setModuleEntries((prev) => prev.map((e, i) => (i === index ? { ...e, module: name } : e)));
  };

  const addKeyword = (index: number) => {
    const val = (keywordInputs[index] || '').trim();
    if (!val) return;
    setModuleEntries((prev) =>
      prev.map((e, i) => (i === index ? { ...e, keywords: [...e.keywords, val] } : e))
    );
    setKeywordInputs((prev) => ({ ...prev, [index]: '' }));
  };

  const removeKeyword = (moduleIndex: number, keywordIndex: number) => {
    setModuleEntries((prev) =>
      prev.map((e, i) =>
        i === moduleIndex
          ? { ...e, keywords: e.keywords.filter((_, ki) => ki !== keywordIndex) }
          : e
      )
    );
  };

  const updateKeywordInput = (index: number, value: string) => {
    setKeywordInputs((prev) => ({ ...prev, [index]: value }));
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
            onClick={() => openDetail(user)}
            style={{
              background: '#fff',
              borderRadius: 8,
              padding: 14,
              marginBottom: 10,
              boxShadow: '0 1px 3px rgba(0,0,0,0.06)',
              cursor: 'pointer',
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
                  <span style={{ fontWeight: 500, fontSize: 15 }}>{user.name || user.username}</span>
                  {user.name && user.name !== user.username && (
                    <span style={{ fontSize: 13, color: '#888' }}>@{user.username}</span>
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

                {user.responsibility_modules && Object.keys(user.responsibility_modules).length > 0 && (
                  <div style={{ marginBottom: 4 }}>
                    {Object.entries(user.responsibility_modules).map(([mod, keywords]) => (
                      <div key={mod} style={{ marginBottom: 3 }}>
                        <span
                          style={{
                            fontSize: 11,
                            fontWeight: 500,
                            padding: '1px 6px',
                            borderRadius: 3,
                            background: '#fff7e6',
                            color: '#d46b08',
                            marginRight: 4,
                          }}
                        >
                          {mod}
                        </span>
                        {keywords.map((kw) => (
                          <span
                            key={kw}
                            style={{
                              fontSize: 11,
                              padding: '1px 5px',
                              borderRadius: 2,
                              background: '#f0f0f0',
                              color: '#666',
                              marginRight: 3,
                            }}
                          >
                            {kw}
                          </span>
                        ))}
                      </div>
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
                <Button size="small" variant="outline" onClick={(e) => { e.stopPropagation(); openEdit(user); }}>
                  编辑
                </Button>
                <Button
                  size="small"
                  theme="danger"
                  variant="outline"
                  onClick={(e) => { e.stopPropagation(); handleDelete(user); }}
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

          {editLoading ? (
            <div style={{ textAlign: 'center', padding: 40 }}>
              <Loading text="加载用户信息..." />
            </div>
          ) : (
          <Form>
            {!editingUsername && (
              <>
                <FormItem label="用户名">
                  <Input
                    value={form.username}
                    onChange={(v) => setForm((p) => ({ ...p, username: String(v) }))}
                    placeholder="登录账号"
                    clearable
                  />
                </FormItem>

                <FormItem label="密码">
                  <Input
                    value={form.password}
                    onChange={(v) => setForm((p) => ({ ...p, password: String(v) }))}
                    placeholder="初始密码"
                    type="password"
                    clearable
                  />
                </FormItem>
              </>
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

            {!editingUsername && (
              <FormItem label="状态">
                <RadioGroup
                  value={form.status}
                  onChange={(v) => setForm((p) => ({ ...p, status: v as string }))}
                  options={STATUS_OPTIONS}
                />
              </FormItem>
            )}

            <FormItem label="责任模块">
              <div style={{ marginBottom: 8 }}>
                {moduleEntries.length === 0 && (
                  <div style={{ fontSize: 12, color: '#bbb', marginBottom: 8 }}>
                    暂未设置，点击下方按钮添加
                  </div>
                )}

                {moduleEntries.map((entry, idx) => (
                  <div
                    key={idx}
                    style={{
                      border: '1px solid #e5e5e5',
                      borderRadius: 6,
                      padding: 10,
                      marginBottom: 10,
                      background: '#fafafa',
                    }}
                  >
                    <div style={{ display: 'flex', gap: 6, marginBottom: 8, alignItems: 'center' }}>
                      <span style={{ fontSize: 12, color: '#666', whiteSpace: 'nowrap', minWidth: 48 }}>
                        模块 {idx + 1}
                      </span>
                      <Input
                        value={entry.module}
                        onChange={(v) => updateModuleName(idx, String(v))}
                        placeholder="模块名，如：调度USP"
                        clearable
                        style={{ flex: 1 }}
                      />
                      <Button
                        size="small"
                        variant="text"
                        theme="danger"
                        onClick={() => removeModule(idx)}
                      >
                        删除
                      </Button>
                    </div>

                    {entry.keywords.length > 0 && (
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginBottom: 8 }}>
                        {entry.keywords.map((kw, ki) => (
                          <span
                            key={ki}
                            style={{
                              display: 'inline-flex',
                              alignItems: 'center',
                              gap: 4,
                              fontSize: 12,
                              padding: '2px 8px',
                              borderRadius: 3,
                              background: '#e8f0fe',
                              color: '#0052d9',
                            }}
                          >
                            {kw}
                            <span
                              style={{ cursor: 'pointer', fontWeight: 'bold' }}
                              onClick={() => removeKeyword(idx, ki)}
                            >
                              ×
                            </span>
                          </span>
                        ))}
                      </div>
                    )}

                    <div style={{ display: 'flex', gap: 6 }}>
                      <Input
                        value={keywordInputs[idx] || ''}
                        onChange={(v) => updateKeywordInput(idx, String(v))}
                        placeholder="输入职责关键字"
                        clearable
                        style={{ flex: 1 }}
                      />
                      <Button size="small" variant="outline" onClick={() => addKeyword(idx)}>
                        添加
                      </Button>
                    </div>
                  </div>
                ))}

                <Button size="small" variant="outline" onClick={addModule}>
                  + 添加模块
                </Button>
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
          )}
        </div>
      </Popup>

      <Popup visible={detailVisible} onClose={() => setDetailVisible(false)} placement="bottom" showOverlay>
        <div style={{ padding: 20, maxHeight: '85vh', overflow: 'auto' }}>
          <h4 style={{ marginBottom: 16 }}>用户详情</h4>

          {detailLoading ? (
            <div style={{ textAlign: 'center', padding: 40 }}>
              <Loading text="加载详情..." />
            </div>
          ) : detailUser ? (
            <>
              <div style={{ marginBottom: 16 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                  <span style={{ fontWeight: 600, fontSize: 17 }}>{detailUser.name || detailUser.username}</span>
                  {detailUser.name && detailUser.name !== detailUser.username && (
                    <span style={{ fontSize: 14, color: '#888' }}>@{detailUser.username}</span>
                  )}
                  <span
                    style={{
                      fontSize: 12,
                      padding: '2px 8px',
                      borderRadius: 4,
                      background: detailUser.status === 'active' ? '#e8f5e9' : '#f5f5f5',
                      color: detailUser.status === 'active' ? '#2ba471' : '#999',
                    }}
                  >
                    {detailUser.status === 'active' ? '活跃' : '未激活'}
                  </span>
                </div>

                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 8 }}>
                  <span
                    style={{
                      fontSize: 12,
                      padding: '3px 10px',
                      borderRadius: 4,
                      background: `${getJobLevelColor(detailUser.job_level)}15`,
                      color: getJobLevelColor(detailUser.job_level),
                      border: `1px solid ${getJobLevelColor(detailUser.job_level)}30`,
                    }}
                  >
                    {getJobLevelLabel(detailUser.job_level)}
                  </span>
                  {detailUser.department && (
                    <span
                      style={{
                        fontSize: 12,
                        padding: '3px 10px',
                        borderRadius: 4,
                        background: '#e8f0fe',
                        color: '#0052d9',
                      }}
                    >
                      🏢 {detailUser.department}
                    </span>
                  )}
                </div>
              </div>

              {detailUser.responsibility_modules && Object.keys(detailUser.responsibility_modules).length > 0 && (
                <div style={{ marginBottom: 16 }}>
                  <div style={{ fontSize: 13, fontWeight: 500, color: '#333', marginBottom: 8 }}>
                    📌 责任模块
                  </div>
                  {Object.entries(detailUser.responsibility_modules).map(([mod, keywords]) => (
                    <div
                      key={mod}
                      style={{
                        border: '1px solid #eee',
                        borderRadius: 6,
                        padding: '8px 10px',
                        marginBottom: 6,
                        background: '#fafafa',
                      }}
                    >
                      <span style={{ fontSize: 12, fontWeight: 500, color: '#d46b08', marginRight: 6 }}>
                        {mod}
                      </span>
                      {keywords.map((kw) => (
                        <span
                          key={kw}
                          style={{
                            fontSize: 11,
                            padding: '1px 6px',
                            borderRadius: 3,
                            background: '#fff',
                            color: '#666',
                            marginRight: 4,
                          }}
                        >
                          {kw}
                        </span>
                      ))}
                    </div>
                  ))}
                </div>
              )}

              {detailUser.duty_text && (
                <div style={{ marginBottom: 16 }}>
                  <div style={{ fontSize: 13, fontWeight: 500, color: '#333', marginBottom: 6 }}>
                    📋 职责画像
                  </div>
                  <div
                    style={{
                      fontSize: 13,
                      color: '#555',
                      lineHeight: 1.6,
                      background: '#f8f8f8',
                      padding: '10px 12px',
                      borderRadius: 6,
                    }}
                  >
                    {detailUser.duty_text}
                  </div>
                </div>
              )}

              {detailUser.roles && Object.keys(detailUser.roles).length > 0 && (
                <div style={{ marginBottom: 16 }}>
                  <div style={{ fontSize: 13, fontWeight: 500, color: '#333', marginBottom: 8 }}>
                    🎯 全局角色
                  </div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                    {Object.entries(detailUser.roles).map(([projectId, roleIds]) => (
                      <div key={projectId}>
                        {roleIds.map((roleId) => (
                          <span
                            key={roleId}
                            style={{
                              fontSize: 12,
                              padding: '3px 10px',
                              borderRadius: 4,
                              background: '#e6f7ff',
                              color: '#0050b3',
                              marginRight: 4,
                            }}
                          >
                            {roleId}
                          </span>
                        ))}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {detailUser.projectPermissions && Object.keys(detailUser.projectPermissions).length > 0 && (
                <div style={{ marginBottom: 16 }}>
                  <div style={{ fontSize: 13, fontWeight: 500, color: '#333', marginBottom: 8 }}>
                    📂 项目角色
                  </div>
                  {Object.entries(detailUser.projectPermissions).map(([projectId, rolesMap]) => (
                    <div
                      key={projectId}
                      style={{
                        border: '1px solid #e6f7ff',
                        borderRadius: 6,
                        padding: '8px 10px',
                        marginBottom: 6,
                        background: '#f0faff',
                      }}
                    >
                      <span style={{ fontSize: 12, fontWeight: 500, color: '#0050b3' }}>
                        项目 {projectId}
                      </span>
                      <div style={{ marginTop: 4, display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                        {Object.entries(rolesMap).map(([roleKey, perms]) => (
                          <span
                            key={roleKey}
                            style={{
                              fontSize: 11,
                              padding: '2px 6px',
                              borderRadius: 3,
                              background: '#fff',
                              color: '#0050b3',
                              marginRight: 4,
                            }}
                          >
                            {roleKey}
                          </span>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {detailUser.permissions && detailUser.permissions.length > 0 && (
                <div style={{ marginBottom: 16 }}>
                  <div style={{ fontSize: 13, fontWeight: 500, color: '#333', marginBottom: 8 }}>
                    🔐 权限列表
                  </div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                    {detailUser.permissions.map((perm) => (
                      <span
                        key={perm}
                        style={{
                          fontSize: 11,
                          padding: '2px 8px',
                          borderRadius: 3,
                          background: '#fff0f0',
                          color: '#cf1322',
                          fontFamily: 'monospace',
                        }}
                      >
                        {perm}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {detailUser.external_credentials && Object.keys(detailUser.external_credentials).length > 0 && (
                <div style={{ marginBottom: 16 }}>
                  <div style={{ fontSize: 13, fontWeight: 500, color: '#333', marginBottom: 8 }}>
                    🔗 外部凭据
                  </div>
                  {Object.entries(detailUser.external_credentials).map(([key, cred]) => (
                    <div
                      key={key}
                      style={{
                        border: '1px solid #f0e6ff',
                        borderRadius: 6,
                        padding: '8px 10px',
                        marginBottom: 6,
                        background: '#f9f0ff',
                      }}
                    >
                      <div style={{ fontSize: 12, fontWeight: 500, color: '#531dab', marginBottom: 4 }}>
                        {key.toUpperCase()}
                      </div>
                      {Object.entries(cred).map(([credKey, credValue]) => (
                        <div key={credKey} style={{ fontSize: 12, color: '#555' }}>
                          <span style={{ color: '#888' }}>{credKey}：</span>
                          <span style={{ fontFamily: 'monospace' }}>
                            {credKey === 'password' ? '••••••' : credValue}
                          </span>
                        </div>
                      ))}
                    </div>
                  ))}
                </div>
              )}
            </>
          ) : null}

          <div style={{ marginTop: 16 }}>
            <Button theme="default" block onClick={() => setDetailVisible(false)}>
              关闭
            </Button>
          </div>
        </div>
      </Popup>
    </div>
  );
}