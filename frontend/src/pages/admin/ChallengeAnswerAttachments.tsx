import { useState } from 'react';
import { downloadChallengeAnswerAttachment, type ChallengeAttachment } from '@/api/knowledgeChallenges';

export default function ChallengeAnswerAttachments({ challengeId, attachments = [], onRemove, disabled = false }: {
  challengeId: string | number;
  attachments?: ChallengeAttachment[];
  onRemove?: (id: string) => void;
  disabled?: boolean;
}) {
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState('');
  async function download(attachment: ChallengeAttachment) {
    setDownloading(true); setError('');
    try { await downloadChallengeAnswerAttachment(String(challengeId), attachment); }
    catch (failure) { setError(failure instanceof Error ? failure.message : '附件下载失败，请重试'); }
    finally { setDownloading(false); }
  }
  if (!attachments.length) return null;
  return <div className="challenge-answer-attachments">
    <ul aria-label="解答附件">{attachments.map((attachment) => <li key={attachment.id}>
      <button type="button" className="knowledge-link challenge-answer-attachments__download" disabled={disabled || downloading} aria-label={`下载附件 ${attachment.name}`} onClick={() => void download(attachment)}>{attachment.name}</button>
      <span className="knowledge-muted">{attachment.size >= 1024 * 1024 ? `${(attachment.size / (1024 * 1024)).toFixed(1)} MB` : `${Math.max(1, Math.ceil(attachment.size / 1024))} KB`}</span>
      {onRemove && <button type="button" className="knowledge-link" aria-label={`删除附件 ${attachment.name}`} disabled={disabled || downloading} onClick={() => onRemove(attachment.id)}>删除</button>}
    </li>)}</ul>
    {error && <p role="alert" className="knowledge-discussion-error">{error}</p>}
  </div>;
}
