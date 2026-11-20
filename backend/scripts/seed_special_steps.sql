-- 特殊 step 种子：为每种 task_type 添加两条特殊 step（等待外部条件 / 等待前置工单）
-- sequence = -2  → 等待外部条件
-- sequence = -1  → 等待前置工单
--
-- 执行前请备份数据：
--   mysqldump -u root -p your_db task_steps > task_steps_backup.sql

INSERT INTO task_steps (task_type, step_name, sequence)
VALUES
  ('PROBLEM', '等待外部条件', -2),
  ('PROBLEM', '等待前置工单', -1),
  ('BUG',     '等待外部条件', -2),
  ('BUG',     '等待前置工单', -1),
  ('FEATURE', '等待外部条件', -2),
  ('FEATURE', '等待前置工单', -1),
  ('SUPPORT', '等待外部条件', -2),
  ('SUPPORT', '等待前置工单', -1),
  ('OTHER',   '等待外部条件', -2),
  ('OTHER',   '等待前置工单', -1);

-- 验证：
-- SELECT * FROM task_steps ORDER BY task_type, sequence;
-- 每种 task_type 应看到 2 条 sequence<0 的记录排在正常步骤之前
