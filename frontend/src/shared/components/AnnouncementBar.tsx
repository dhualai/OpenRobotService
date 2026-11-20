import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import { Megaphone, ChevronRight } from 'lucide-react';

import { fetchActiveAnnouncement } from '@/api/announcement';
import { fetchKnowledgeOverview, fetchKnowledgeQuestions } from '@/api/knowledge';
import { fetchChallenges } from '@/api/knowledgeChallenges';

export const ACTIVITY_ROTATION_MS = 4000;
const TITLE_SCROLL_SPEED = 40;
const TITLE_SCROLL_PAUSE_MS = 1000;

interface Props {
  /** 点击公告条 → 打开活动中心抽屉 */
  onOpen: () => void;
}

function AnnouncementTitle({ title, scrollable, onNext }: { title: string; scrollable: boolean; onNext?: () => void }) {
  const viewport = useRef<HTMLSpanElement>(null);
  const content = useRef<HTMLSpanElement>(null);
  const [overflow, setOverflow] = useState(0);
  const [cycle, setCycle] = useState(0);
  const scrollDuration = overflow / TITLE_SCROLL_SPEED * 1000;
  const rotationDuration = Math.max(ACTIVITY_ROTATION_MS, scrollDuration + TITLE_SCROLL_PAUSE_MS * 2);

  useLayoutEffect(() => {
    const viewportElement = viewport.current;
    const contentElement = content.current;
    if (!scrollable || !viewportElement || !contentElement) return;
    const measure = () => setOverflow(viewportElement.clientWidth > 0
      ? Math.max(0, contentElement.scrollWidth - viewportElement.clientWidth) : 0);
    measure();
    const observer = typeof ResizeObserver === 'undefined' ? undefined : new ResizeObserver(measure);
    observer?.observe(viewportElement);
    observer?.observe(contentElement);
    window.addEventListener('resize', measure);
    return () => {
      observer?.disconnect();
      window.removeEventListener('resize', measure);
    };
  }, [scrollable, title, cycle]);

  useEffect(() => {
    if (!onNext && overflow === 0) return;
    const timer = window.setTimeout(() => {
      if (onNext) onNext();
      else setCycle((previous) => previous + 1);
    }, rotationDuration);
    return () => window.clearTimeout(timer);
  }, [onNext, overflow, rotationDuration, cycle]);

  const style = overflow > 0 ? {
    '--announcement-scroll-distance': `-${overflow}px`,
    '--announcement-scroll-duration': `${scrollDuration}ms`,
    '--announcement-scroll-delay': `${TITLE_SCROLL_PAUSE_MS}ms`,
  } as CSSProperties : undefined;

  return <span ref={viewport} className={`announcement-bar__title${overflow > 0 ? ' announcement-bar__title--scrolling' : ''}`}>
    {scrollable ? <span ref={content} key={cycle} className="announcement-bar__title-text" style={style}>{title}</span> : title}
  </span>;
}

export default function AnnouncementBar({ onOpen }: Props) {
  const [announcementTitle, setAnnouncementTitle] = useState('');
  const [questionTitles, setQuestionTitles] = useState<string[]>([]);
  const [challengeTitles, setChallengeTitles] = useState<string[]>([]);
  const [index, setIndex] = useState(0);

  useEffect(() => {
    let alive = true;
    void fetchActiveAnnouncement().then((item) => {
      if (alive) setAnnouncementTitle(item?.title || '');
    }).catch(() => undefined);
    void fetchKnowledgeOverview().then((overview) => overview.month).catch(() => '')
      .then((month) => alive ? fetchKnowledgeQuestions(month, 'likes', 1, 10) : undefined)
      .then((result) => { if (alive) setQuestionTitles((result?.items || []).map((item) => item.title)); })
      .catch(() => undefined);
    void fetchChallenges('active', 1).then((result) => {
      if (alive) setChallengeTitles((result?.items || []).map((item) => item.title));
    }).catch(() => undefined);
    return () => { alive = false; };
  }, []);

  const entries = useMemo(() => {
    const groups = [
      { category: '公告', titles: [announcementTitle] },
      { category: '优质问题', titles: questionTitles },
      { category: '高价值问题', titles: challengeTitles },
    ].map((group) => ({ ...group, titles: group.titles.filter((title) => title.trim()) }));
    const items: { category: string; title: string }[] = [];
    const count = Math.max(...groups.map((group) => group.titles.length));
    for (let position = 0; position < count; position++) {
      for (const group of groups) {
        if (group.titles[position]) items.push({ category: group.category, title: group.titles[position] });
      }
    }
    return items;
  }, [announcementTitle, questionTitles, challengeTitles]);

  useEffect(() => {
    setIndex(0);
  }, [entries]);
  const nextEntry = useCallback(() => setIndex((previous) => (previous + 1) % entries.length), [entries.length]);

  const entry = entries[index % entries.length];
  if (!entry) return null;

  return (
    <button
      type="button"
      className="announcement-bar"
      onClick={onOpen}
      aria-label={`${entry.category}：${entry.title}，点击查看活动中心`}
    >
      <Megaphone size={15} strokeWidth={2} className="announcement-bar__icon" />
      <span className="announcement-bar__category">{entry.category}</span>
      <AnnouncementTitle key={`${index}:${entry.category}:${entry.title}`} title={entry.title} scrollable={entry.category === '高价值问题'} onNext={entries.length > 1 ? nextEntry : undefined} />
      <span className="announcement-bar__more">
        活动中心
        <ChevronRight size={14} strokeWidth={2} />
      </span>
    </button>
  );
}
