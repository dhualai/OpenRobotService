/**
 * 「AI 生成问题文档」按钮 + 预览确认 —— 提单页（AI 会话）与工单详情（讨论区）共用。
 *
 * 职责边界（只做这三件事）：
 *   1. 把素材交给后端 POST /api/tasks/problem-doc/ai-generate；
 *   2. 生成后弹预览，内容可改，并明确提示「补充段会被替换」；
 *   3. 用户点「写入文档」才回调 onApply(markdown)。
 *
 * 不做写入：两侧落点不同 —— 提单阶段写 overrides.spec_doc，已建工单写
 * PUT /{task_id}/spec-doc，交给调用方决定（见 replaceUserSection）。
 */
import { useState } from 'react';
import { Popup, Toast } from 'tdesign-mobile-react';
import { generateProblemDoc, type ProblemDocSourceItem } from '@/api/specDoc';
// 自带样式：提单页（TicketShareDocSetting 已 import 过，幂等）与工单详情页（SpecDocCard）都要用
import '@/shared/styles/shareDoc.css';

export interface AiProblemDocGeneratorProps {
  /** 待整理的素材（按时间正序） */
  items: ProblemDocSourceItem[];
  projectName?: string;
  scene?: 'conversation' | 'discussion';
  /** 补充段是否已有内容（有则提示会被替换） */
  hasUserContent?: boolean;
  disabled?: boolean;
  label?: string;
  /** 触发按钮类名，默认提单页 macaron 胶囊；工单详情卡片传 spec-card__btn spec-card__btn--primary */
  triggerClassName?: string;
  onApply: (markdown: string) => void;
}

export default function AiProblemDocGenerator({
  items,
  projectName = '',
  scene = 'conversation',
  hasUserContent = false,
  disabled = false,
  label = 'AI 生成问题文档',
  triggerClassName = 'share-doc__btn share-doc__btn--primary',
  onApply,
}: AiProblemDocGeneratorProps) {
  const [generating, setGenerating] = useState(false);
  /** null=未生成/已关闭；string=预览中（可编辑） */
  const [preview, setPreview] = useState<string | null>(null);
  const [notice, setNotice] = useState('');

  const canGenerate = !disabled && !generating && items.length > 0;

  const close = () => {
    setPreview(null);
    setNotice('');
  };

  const generate = async () => {
    setGenerating(true);
    try {
      const res = await generateProblemDoc({ items, project_name: projectName, scene });
      const md = (res?.markdown ?? '').trim();
      if (!md) throw new Error('AI 没有返回内容，请稍后再试');
      setPreview(md);
      setNotice(res.truncated ? `内容较长，已省略较早的 ${res.dropped} 条发言` : '');
    } catch (err) {
      Toast({
        message: err instanceof Error && err.message ? err.message : '生成失败，请稍后重试',
        theme: 'error',
      });
    } finally {
      setGenerating(false);
    }
  };

  const apply = () => {
    const md = (preview ?? '').trim();
    if (!md) return;
    onApply(md);
    close();
    Toast({ message: '已写入问题文档补充段', theme: 'success' });
  };

  return (
    <>
      <button
        type="button"
        className={triggerClassName}
        disabled={!canGenerate}
        onClick={() => void generate()}
      >
        {generating ? '生成中…' : label}
      </button>

      <Popup
        visible={preview !== null}
        onClose={close}
        placement="bottom"
        showOverlay
        closeOnOverlayClick={false}
        style={{ zIndex: 13010 }}
      >
        <div className="share-doc__sheet">
          <h4 className="share-doc__sheet-title">AI 生成的问题文档</h4>
          <p className="share-doc__sheet-desc">
            {hasUserContent
              ? '确认后替换分隔线以下的现有补充内容（项目背景信息不受影响）。可先在本框里修改。'
              : '可先在本框里修改，确认后写入文档的补充段。'}
          </p>
          {notice ? <p className="share-doc__sheet-desc">{notice}</p> : null}
          <textarea
            className="share-doc__textarea"
            value={preview ?? ''}
            rows={10}
            onChange={(e) => setPreview(e.target.value)}
          />
          <div className="share-doc__sheet-actions">
            <button type="button" className="share-doc__btn" onClick={close}>
              取消
            </button>
            <button
              type="button"
              className="share-doc__btn share-doc__btn--primary"
              disabled={!(preview ?? '').trim()}
              onClick={apply}
            >
              写入文档
            </button>
          </div>
        </div>
      </Popup>
    </>
  );
}
