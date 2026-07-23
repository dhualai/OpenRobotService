// AI 解决方案草稿卡片 — 可编辑
// ChatPanel tasks 场景专属：SSE result 后替换纯 Markdown 渲染
import { useState } from 'react';
import { Textarea, Button, Tag, Toast } from 'tdesign-mobile-react';
interface SolutionDraft {
  _task_id?: string;
  root_cause_analysis: string;
  suggested_actions: string[];
  references: string[];
  confidence: number;
  needs_more_info: boolean;
}

interface SolutionCardProps {
  draft: SolutionDraft;
  onDraftChange: (draft: SolutionDraft) => void;
  onSubmit: () => void;
  onReanalyze: () => void;
  submitting?: boolean;
}

export type { SolutionDraft };

const confidenceColor = (c: number) => (c >= 0.8 ? 'success' : c >= 0.5 ? 'primary' : 'warning');

export default function SolutionCard({
  draft, onDraftChange, onSubmit, onReanalyze, submitting,
}: SolutionCardProps) {
  const [local, setLocal] = useState<SolutionDraft>(draft);
  // 同步外部 draft 变化（比如 AI 重新分析后更新）
  if (draft !== local && JSON.stringify(draft) !== JSON.stringify(local)) {
    // 外部更新了（只在初始化或 AI 重分析时可能发生，此时应替换本地状态）
  }

  const update = (patch: Partial<SolutionDraft>) => {
    const next = { ...local, ...patch };
    setLocal(next);
    onDraftChange(next);
  };

  const updateAction = (index: number, value: string) => {
    const actions = [...local.suggested_actions];
    actions[index] = value;
    update({ suggested_actions: actions });
  };

  const handleSubmit = () => {
    if (!local.root_cause_analysis.trim()) {
      Toast({ message: '请至少填写根因分析', theme: 'warning' });
      return;
    }
    onSubmit();
  };

  return (
    <div className="solution-card">
      <div className="solution-card__header">
        <span className="solution-card__title">AI 解决方案草稿</span>
        <Tag theme={confidenceColor(local.confidence)} size="small">
          {Math.round(local.confidence * 100)}%
        </Tag>
        {local.needs_more_info && (
          <span className="solution-card__hint">信息不足，建议核实后补充</span>
        )}
      </div>

      <div className="solution-card__section">
        <h4>根因分析</h4>
        <Textarea
          value={local.root_cause_analysis}
          onChange={(v) => update({ root_cause_analysis: String(v) })}
          autosize={{ minRows: 2, maxRows: 8 }}
          placeholder="AI 生成的根因分析..."
        />
      </div>

      <div className="solution-card__section">
        <h4>建议步骤</h4>
        {local.suggested_actions.length === 0 && (
          <p className="solution-card__empty">暂无建议步骤</p>
        )}
        {local.suggested_actions.map((action, i) => (
          <div key={i} className="solution-card__action">
            <span className="solution-card__action-num">{i + 1}</span>
            <Textarea
              value={action}
              onChange={(v) => updateAction(i, String(v))}
              autosize={{ minRows: 1, maxRows: 4 }}
              placeholder={`步骤 ${i + 1}`}
            />
          </div>
        ))}
      </div>

      {local.references.length > 0 && (
        <div className="solution-card__section">
          <h4>参考来源</h4>
          <ul className="solution-card__refs">
            {local.references.map((ref, i) => (
              <li key={i}>{ref}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="solution-card__actions">
        <Button
          size="small"
          theme="default"
          onClick={() => {
            // 恢复 AI 原始内容
            setLocal(draft);
            onDraftChange(draft);
          }}
          disabled={submitting}
        >
          重置
        </Button>
        <Button size="small" theme="primary" onClick={onReanalyze} disabled={submitting}>
          重新分析
        </Button>
        <Button size="small" theme="primary" onClick={handleSubmit} loading={submitting}>
          提交方案
        </Button>
      </div>
    </div>
  );
}
