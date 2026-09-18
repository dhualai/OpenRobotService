// 评论区参与人「头像堆叠」——列表卡片（系统任务 / 历史工单）共用。
//
// 展示口径（用户 2026-09-18 定）：
//   发起人 | 参与人头像堆叠（>3 折叠为 +N，顺序由后端按「评论数降序 → 评论时间降序」排好）| 箭头 | 处理人
//   发起人与堆叠之间**无**箭头；堆叠与处理人之间**有**箭头（箭头由调用方渲染，不在本组件内）。
//   红点：某参与人发布了「当前登录用户未读」的评论 → 该头像右上角亮红点。
//
// 点击：点头像开「参与讨论」名单浮层（含未读标记与评论数），点名单行跳详情页讨论区，
//   沿用详情页 DiscussionPanel 的定位逻辑（锚点 comment-<id> / is-flash 高亮，由目标页读取 URL 参数后执行）。
import { useState } from 'react';
import AvatarImg from '@/shared/components/AvatarImg';
import TitleEllipsis from '@/shared/components/TitleEllipsis';
import { avatarUrl } from '@/api/profile';

/** 列表卡片参与人条目（后端 TicketParticipantItem / ai list_all_tickets participants 同构） */
export interface ParticipantItem {
  username: string;
  name?: string | null;
  avatar_resource_id?: number | null;
  comment_count?: number;
  last_comment_at?: string | null;
  has_unread?: boolean;
}

/** 头像堆叠最多外显几个，超出折叠为 +N */
export const MAX_VISIBLE_PARTICIPANTS = 3;

type Props = {
  participants: ParticipantItem[];
  /** username → avatar_resource_id 查找表；缺省时回退首字母头像 */
  avatarMap?: Map<string, number>;
  /** 点头像跳详情页讨论区（由调用方拼接路由） */
  onLocate?: (p: ParticipantItem) => void;
};

/** 单个堆叠头像：有资源 id 走图片，否则首字母兜底；has_unread 时右上角红点 */
function StackAvatar({ item, avatarMap, extraClass }: { item: ParticipantItem; avatarMap?: Map<string, number>; extraClass?: string }) {
  const label = item.name || item.username;
  const avatarId = item.avatar_resource_id ?? avatarMap?.get(item.username);
  return (
    <span className={`participant-stack__item${item.has_unread ? ' has-unread' : ''}${extraClass ? ` ${extraClass}` : ''}`}>
      <AvatarImg
        className="task-card2__avatar task-card2__avatar--img participant-stack__img"
        src={avatarId ? avatarUrl(avatarId) : null}
        alt={label}
        fallback={<span className="task-card2__avatar participant-stack__fallback">{label.slice(0, 1).toUpperCase()}</span>}
      />
      {item.has_unread && <i className="participant-stack__dot" aria-hidden="true" />}
    </span>
  );
}

export default function ParticipantStack({ participants, avatarMap, onLocate }: Props) {
  // 展开浮层：展示完整参与人名单（头像 + 姓名 + 评论数 + 未读），
  // 避免 +N 折叠后信息不可达。
  const [expanded, setExpanded] = useState(false);

  const list = (participants || []).filter((p) => p && p.username);
  if (list.length === 0) return null;

  const visible = list.slice(0, MAX_VISIBLE_PARTICIPANTS);
  const overflow = list.length - visible.length;
  const unreadCount = list.filter((p) => p.has_unread).length;
  const title = `参与人：${list.map((p) => p.name || p.username).join('、')}`
    + (unreadCount > 0 ? `（${unreadCount} 人有未读评论）` : '');

  return (
    <span className="participant-stack-wrap">
      <button
        type="button"
        className="task-card2__participants participant-stack"
        title={title}
        aria-label={title}
        onClick={(e) => {
          // 阻止冒泡：卡片整体 onClick 会进详情页，这里只开／关名单浮层
          e.stopPropagation();
          setExpanded((v) => !v);
        }}
      >
        {visible.map((p) => <StackAvatar key={p.username} item={p} avatarMap={avatarMap} />)}
        {overflow > 0 && (
          <span className="task-card2__participant task-card2__participant--overflow">+{overflow}</span>
        )}
      </button>

      {expanded && (
        <div className="participant-stack__pop" onClick={(e) => e.stopPropagation()}>
          <div className="participant-stack__pop-head">参与讨论（{list.length}）</div>
          <ul className="participant-stack__pop-list">
            {list.map((p) => (
              <li
                key={p.username}
                className="participant-stack__pop-row"
                onClick={() => { setExpanded(false); onLocate?.(p); }}
              >
                <StackAvatar item={p} avatarMap={avatarMap} extraClass="participant-stack__pop-avatar" />
                <TitleEllipsis text={p.name || p.username} lines={1} titleClassName="participant-stack__pop-name" as="span" fontSize={13} />
                <span className="participant-stack__pop-count">{p.comment_count || 0} 条</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </span>
  );
}
