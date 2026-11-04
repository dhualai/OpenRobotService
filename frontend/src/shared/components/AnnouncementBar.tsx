import { useEffect, useMemo, useState } from 'react';
import { Megaphone, ChevronRight } from 'lucide-react';

import { fetchActiveAnnouncement } from '@/api/announcement';
import { fetchKnowledgeOverview, fetchKnowledgeQuestions } from '@/api/knowledge';
import { fetchChallenges } from '@/api/knowledgeChallenges';

export const ACTIVITY_ROTATION_MS = 4000;

interface Props {
  /** 点击公告条 → 打开活动中心抽屉 */
  onOpen: () => void;
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
    if (entries.length < 2) return;
    const timer = window.setInterval(() => setIndex((previous) => (previous + 1) % entries.length), ACTIVITY_ROTATION_MS);
    return () => window.clearInterval(timer);
  }, [entries]);

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
      <span className="announcement-bar__title" key={`${entry.category}:${entry.title}`}>{entry.title}</span>
      <span className="announcement-bar__more">
        活动中心
        <ChevronRight size={14} strokeWidth={2} />
      </span>
    </button>
  );
}
