/**
 * 项目信息编辑弹窗：提单页「补充信息」用（原右侧侧滑抽屉，桌面端遮挡提单弹窗且点遮罩不关，
 * 2026-09-30 改为弹窗：PC 居中大窗 / 移动端底部弹层，点遮罩均可关闭）。
 *
 * 内容直接复用信息树编辑页（`pages/admin/ProjectInfoEdit` 的嵌入模式，见其 props）——
 * 编辑能力（填值 / 增补 / 上传附件）只有一份实现，弹窗不另写一套。
 * 之所以 shared → pages 单向引用：pages/admin/ProjectInfoEdit 不反向依赖 shared 组件，
 * 不存在循环；相比在 shared 里重写一遍编辑 UI，这样更不容易两处逻辑漂移。
 */
import { Popup } from 'tdesign-mobile-react';
import ProjectInfoEdit from '@/pages/admin/ProjectInfoEdit';
import { isPC } from '@/shared/utils/device';
import type { ProjectInfoNode } from '@/shared/utils/projectInfoTree';

interface Props {
  visible: boolean;
  projectId: string;
  projectName?: string;
  onClose: () => void;
  /** 树数据变化回调（提单页据此实时重算共享文档） */
  onTreeChange?: (nodes: ProjectInfoNode[]) => void;
}

export default function ProjectInfoEditDrawer({
  visible,
  projectId,
  projectName = '',
  onClose,
  onTreeChange,
}: Props) {
  // UA 判定一次即可（同一会话不会变）：PC 居中大窗、移动端底部弹层
  const pc = isPC();
  return (
    <Popup
      visible={visible}
      placement={pc ? 'center' : 'bottom'}
      onClose={onClose}
      showOverlay
      closeOnOverlayClick
      style={{ zIndex: 13010 }}
    >
      {/* 懒挂载：不可见时不渲染编辑器子树，避免无谓拉树 */}
      {visible && (
        <div className={`info-drawer${pc ? ' info-drawer--center' : ''}`}>
          <div className="info-drawer__head">
            <span className="info-drawer__title">
              补充项目信息{projectName ? ` · ${projectName}` : ''}
            </span>
            <button type="button" className="info-drawer__done" onClick={onClose}>
              完成
            </button>
          </div>
          <div className="info-drawer__body">
            <ProjectInfoEdit projectId={projectId} embedded onTreeChange={onTreeChange} />
          </div>
        </div>
      )}
    </Popup>
  );
}
