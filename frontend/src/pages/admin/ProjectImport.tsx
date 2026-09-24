// 项目管理 - 项目导入：新建入口 + 「一键导入所有项目节点内容」。
//
// 新建仍是两个入口（USP项目 / 其他项目），具体项目列表已迁移至「跨项目进度管理」列表页。
//
// 一键导入（2026-09-22 用户口径「增加一个管理员和超级管理员权限的一键导入所有项目节点内容的
// 按钮，可以直接从 project 里导入数据」）：把本地 project 表（企业微信台账的同步镜像）里
// 有值的内容，一次性写进**全部项目**的信息节点——空的填上、与台账不一致的就地覆盖，
// 逐条记进编辑历史；不新建节点（台账有、树里没有的列只报数，那要去详情模板补字段）。
// 前端只管「确认 + 转圈 + 报结果」，比对与落库都在后端一次做完
// （POST /info-nodes/ledger-sync/all，见 backend/docs/...5.18）。
//
// 两个必须的护栏：
//   ① 仅管理员/超级管理员可见可点——与后端 get_current_admin_user 同一判据
//      （permissions 含 admin，与项目工单卡「配置阻滞权重」一致）；接口是直连可达的，
//      真正的闸门在后端，这里只是不给无权的人一个点了就报错的按钮。
//   ② 点了先二次确认：写的是全体项目的数据，误触代价大；确认文案把「会覆盖什么」写清楚，
//      并说明不新建节点、不删节点。
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Dialog, Toast } from 'tdesign-mobile-react';
import { importAllProjectsLedgerApi } from '@/api/infoNodes';
import { useAuthStore } from '@/stores/auth';

const errMsg = (err: unknown, fallback: string) =>
  err instanceof Error && err.message ? err.message : fallback;

/** 结果 → 一句话（「导了多少、还剩多少没匹配上」，失败另起一句） */
function summaryText(result: Awaited<ReturnType<typeof importAllProjectsLedgerApi>>): string {
  const parts = [`已处理 ${result.project_total} 个项目`];
  if (result.filled) parts.push(`新填 ${result.filled} 项`);
  if (result.overwritten) parts.push(`覆盖 ${result.overwritten} 项`);
  if (!result.filled && !result.overwritten) parts.push('没有需要写入的内容');
  if (result.project_skipped) parts.push(`${result.project_skipped} 个项目还没有信息节点，已跳过`);
  if (result.project_failed) parts.push(`${result.project_failed} 个项目失败`);
  return parts.join('，');
}

export default function ProjectImport() {
  const navigate = useNavigate();
  // 一键导入是全局批量写，闸门与后端 get_current_admin_user 对齐（permissions 含 admin）
  const canImportAll = useAuthStore((s) => Array.isArray(s.permissions) && s.permissions.includes('admin'));
  const [importing, setImporting] = useState(false);

  const runImportAll = async () => {
    setImporting(true);
    try {
      const result = await importAllProjectsLedgerApi();
      Toast({ message: summaryText(result), theme: 'success' });
      // 有失败项目时再提示一条：这类要人去各自项目里看（明细只在开发期需要，界面上给个数）
      if (result.project_failed) {
        const first = result.failures[0];
        Toast({
          message: `有 ${result.project_failed} 个项目没导成功${first ? `（如「${first.project_name}」：${first.reason}）` : ''}`,
          theme: 'warning',
        });
      }
    } catch (err) {
      Toast({ message: `导入失败：${errMsg(err, '请稍后重试')}`, theme: 'error' });
    } finally {
      setImporting(false);
    }
  };

  const confirmImportAll = () => {
    if (importing) return;
    Dialog.confirm?.({
      title: '一键导入所有项目节点内容',
      content: '将把项目台账（本地 project 表）里有值的内容，写入全部项目的对应信息节点：'
        + '节点为空的填上，与台账不一致的就地覆盖（旧值留在编辑历史里）。'
        + '不会新建或删除节点；台账里没有对应节点的那部分只统计、不改动。确定继续吗？',
      confirmBtn: '开始导入',
      onConfirm: () => { void runImportAll(); },
    });
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
        <button
          type="button"
          className="mac-btn mac-btn--primary"
          onClick={() => navigate('/admin/project-detail/new')}
        >
          USP项目
        </button>
        <button
          type="button"
          className="mac-btn mac-btn--blue-outline"
          onClick={() => navigate('/admin/project-edit')}
        >
          其他项目
        </button>
      </div>

      {canImportAll && (
        <button
          type="button"
          className="mac-btn mac-btn--outline mac-btn--block"
          disabled={importing}
          onClick={confirmImportAll}
          title="把项目台账里有值的内容导入全部项目的信息节点（空白的填上、与台账不一致的覆盖）"
        >
          {importing ? '导入中…（项目较多，请稍候）' : '一键导入所有项目节点内容'}
        </button>
      )}
    </div>
  );
}
