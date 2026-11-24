-- ==============================================================================
-- 消除 admin 角色特殊性：新增 5 个权限编码 + 绑定到 admin 角色
-- 执行前请确认：
--   1. 当前环境的 admin 角色 Role.name = '超级管理员' 或 '管理员'
--   2. 所有业务代码中的 is_admin_user() 调用点已替换为 has_permission_code()
-- 幂等设计：INSERT IGNORE / ON DUPLICATE KEY UPDATE 可安全重复执行
-- ==============================================================================

-- ── 1. 新增 5 个权限编码（permissions 表） ────────────────────────────────────

INSERT IGNORE INTO permissions (id, code, name, resource_type, action, description, enabled)
VALUES
  ('perm_backend_tasks_delete',
   'backend:tasks:delete',
   '工单硬删除',
   'backend',
   'delete',
   '有权限的用户可直接从数据库硬删除工单（含评论等关联数据）；无此权限的处理人/提单人仅能软删除（关单）',
   'true'),

  ('perm_backend_archive_report_write',
   'backend:archive-report:write',
   '知识沉淀报告管理',
   'backend',
   'write',
   '有权限的用户可编辑/提交/审核知识沉淀报告草稿',
   'true'),

  ('perm_backend_knowledge_challenge_manage',
   'backend:knowledge-challenge:manage',
   '高价值问题专区管理',
   'backend',
   'operate',
   '有权限的用户可管理高价值问题专区（设置截止时间、发布、审核答案等）',
   'true'),

  ('perm_backend_knowledge_review_operate',
   'backend:knowledge-review:operate',
   '知识榜审核',
   'backend',
   'operate',
   '有权限的用户可作为知识综合榜的备选审核人（无需显式配置 reviewer 字段）',
   'true'),

  ('perm_backend_options_approve',
   'backend:options:approve',
   '审核工单默认处理人',
   'backend',
   'operate',
   '公司/部门/项目等主数据录入审核工单的默认指派权限，拥有此权限的第一个活跃用户将被指派为处理人',
   'true');


-- ── 2. 将新编码绑定到 admin 角色（role_permissions 表） ────────────────────────
-- role_id 通过 Role.name 子查询匹配，兼容不同环境的 role id 差异

-- 超级管理员角色
INSERT IGNORE INTO role_permissions (id, role_id, permission_id)
SELECT
  CONCAT('rp_admin_new_perm_', p.id)    AS id,
  r.id                                  AS role_id,
  p.id                                  AS permission_id
FROM roles r
CROSS JOIN (
  SELECT 'perm_backend_tasks_delete'            AS id
  UNION ALL SELECT 'perm_backend_archive_report_write'
  UNION ALL SELECT 'perm_backend_knowledge_challenge_manage'
  UNION ALL SELECT 'perm_backend_knowledge_review_operate'
  UNION ALL SELECT 'perm_backend_options_approve'
) p
WHERE r.name IN ('超级管理员', 'admin')
  AND NOT EXISTS (
    SELECT 1 FROM role_permissions rp
    WHERE rp.role_id = r.id AND rp.permission_id = p.id
  );

-- 管理员角色（如果存在单独的管理员角色）
INSERT IGNORE INTO role_permissions (id, role_id, permission_id)
SELECT
  CONCAT('rp_admin_new_perm_', p.id)    AS id,
  r.id                                  AS role_id,
  p.id                                  AS permission_id
FROM roles r
CROSS JOIN (
  SELECT 'perm_backend_tasks_delete'            AS id
  UNION ALL SELECT 'perm_backend_archive_report_write'
  UNION ALL SELECT 'perm_backend_knowledge_challenge_manage'
  UNION ALL SELECT 'perm_backend_knowledge_review_operate'
  UNION ALL SELECT 'perm_backend_options_approve'
) p
WHERE r.name IN ('管理员', 'Administrator')
  AND NOT EXISTS (
    SELECT 1 FROM role_permissions rp
    WHERE rp.role_id = r.id AND rp.permission_id = p.id
  );


-- ── 3. 验证查询 ──────────────────────────────────────────────────────────────

-- 查看刚插入的 5 个权限编码
SELECT id, code, name, description FROM permissions
WHERE code IN (
  'backend:tasks:delete',
  'backend:archive-report:write',
  'backend:knowledge-challenge:manage',
  'backend:knowledge-review:operate',
  'backend:options:approve'
);

-- 查看 admin 角色已绑定了哪些新权限
SELECT
  r.name                              AS role_name,
  p.code                              AS permission_code,
  p.name                              AS permission_name
FROM roles r
JOIN role_permissions rp ON rp.role_id = r.id
JOIN permissions p      ON p.id = rp.permission_id
WHERE r.name IN ('超级管理员', '管理员', 'admin')
  AND p.code IN (
    'backend:tasks:delete',
    'backend:archive-report:write',
    'backend:knowledge-challenge:manage',
    'backend:knowledge-review:operate',
    'backend:options:approve'
  )
ORDER BY r.name, p.code;
