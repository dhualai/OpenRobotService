import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { BookOpen, ChevronRight, ClipboardList, Handshake, Trophy } from 'lucide-react';
import type { Contributor, KnowledgeUser } from '@/api/knowledge';
import { KnowledgeAvatar, KnowledgeState, knowledgeMoney } from './knowledgeShared';

type BoardKind = 'combined' | 'questions' | 'questioners' | 'answerers';

export interface KnowledgePreviewEntry {
  id: string | number;
  rank: number;
  label: string;
  description: string;
  metric: string;
  user?: KnowledgeUser;
}

const boardArtwork = {
  combined: { icon: Trophy, label: '综合月榜' },
  questions: { icon: BookOpen, label: '优质问题' },
  questioners: { icon: ClipboardList, label: '提单贡献' },
  answerers: { icon: Handshake, label: '接单贡献' },
};

export function contributorPreviewEntries(contributors: Contributor[], focus: BoardKind): KnowledgePreviewEntry[] {
  return contributors.slice(0, 3).map((person) => ({
    id: person.user_id,
    rank: person.rank,
    label: person.name,
    description: focus === 'questioners' ? `提单${person.question_count} · 综合${person.contribution_count}`
      : focus === 'answerers' ? `接单${person.answer_count} · 综合${person.contribution_count}`
        : `${person.contribution_count}个有效贡献`,
    metric: person.estimated_reward_fen ? knowledgeMoney(person.estimated_reward_fen) : '—',
    user: person,
  }));
}

export default function KnowledgeRankCard({ title, subtitle, notice, kind, entries, to, hideTitle, loading, error, retry, footer, className = '' }: {
  title: string;
  subtitle: string;
  notice?: string;
  kind: BoardKind;
  entries: KnowledgePreviewEntry[];
  to: string;
  hideTitle?: boolean;
  loading?: boolean;
  error?: string;
  retry?: () => void;
  footer?: ReactNode;
  className?: string;
}) {
  const artwork = boardArtwork[kind];
  const ArtworkIcon = artwork.icon;
  return <section className={`mac-card mac-card--pad knowledge-board-card ${className}`} aria-label={title}>
    <Link className="knowledge-board-card__link" to={to} aria-label={`查看${title}`}>
      <div className="knowledge-board-card__heading">
        <div className="knowledge-board-card__title">{!hideTitle && <h3>{title}</h3>}<span className="knowledge-muted">{subtitle}</span></div>
        <span className="knowledge-board-card__more">查看全部<ChevronRight size={14} aria-hidden /></span>
      </div>
      {notice && <div className="knowledge-note">{notice}</div>}
      {!loading && !error && <div className="knowledge-board-card__body">
        <div className="knowledge-board-cover" data-kind={kind} aria-hidden>
          <span className="knowledge-board-cover__face"><ArtworkIcon size={28} /><strong>{artwork.label}</strong><small>TOP 3</small></span>
        </div>
        <ol className="knowledge-board-preview" aria-label={`${title}前三名`}>
          {[0, 1, 2].map((position) => {
            const entry = entries[position];
            return <li key={entry?.id ?? `empty-${position}`} className="knowledge-board-preview__row">
              <span className="knowledge-board-preview__rank">{entry?.rank ?? position + 1}</span>
              {entry?.user && <KnowledgeAvatar user={entry.user} />}
              <span className="knowledge-board-preview__text"><strong title={entry?.label}>{entry?.label || (kind === 'questions' ? '暂无上榜问题' : '虚位以待')}</strong><small>{entry?.description || '等待有效归档'}</small></span>
              <span className="knowledge-board-preview__metric">{entry?.metric || '—'}</span>
            </li>;
          })}
        </ol>
      </div>}
    </Link>
    <KnowledgeState loading={loading} error={error} retry={retry} />
    {footer && <div className="knowledge-card-footer">{footer}</div>}
  </section>;
}
