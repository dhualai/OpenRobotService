// 摇人页顶部固定公告条（活动中心入口）
//
// 只常驻展示「当前生效的一条」公告标题（后端排序：已发布 → 置顶优先 → 发布时间倒序），
// 不做轮播；点击整条打开活动中心抽屉（公告全文 + 精品工单 TOP 榜）。
// 没有已发布公告（接口返回 null）时组件不渲染任何内容，不占位、不留空条。
// 拉取失败静默降级（公告条是增强项，不能因它打断正常对话）。
import { useEffect, useState } from 'react';
import { Megaphone, ChevronRight } from 'lucide-react';

import { fetchActiveAnnouncement, type Announcement } from '@/api/announcement';

interface Props {
  /** 点击公告条 → 打开活动中心抽屉 */
  onOpen: () => void;
}

export default function AnnouncementBar({ onOpen }: Props) {
  const [announcement, setAnnouncement] = useState<Announcement | null>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const item = await fetchActiveAnnouncement();
        if (alive) setAnnouncement(item);
      } catch { /* 静默：公告条不可用不影响对话 */ }
    })();
    return () => { alive = false; };
  }, []);

  if (!announcement) return null;

  return (
    <button
      type="button"
      className="announcement-bar"
      onClick={onOpen}
      aria-label={`公告：${announcement.title}，点击查看活动中心`}
    >
      <Megaphone size={15} strokeWidth={2} className="announcement-bar__icon" />
      <span className="announcement-bar__title">{announcement.title}</span>
      <span className="announcement-bar__more">
        活动中心
        <ChevronRight size={14} strokeWidth={2} />
      </span>
    </button>
  );
}
