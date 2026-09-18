// 评论区参与人「头像堆叠」——列表卡片（系统任务 / 历史工单）共用。
//
// 展示口径（用户 2026-09-18 定）：
//   发起人 | 参与人头像堆叠（>3 折叠为 +N，顺序由后端按「评论数降序 → 评论时间降序」排好）| → 处理人
//   堆叠下方一条短下划线（宽度以堆叠总宽为准、左右各外扩 6px），末端实心箭头，
//   形如「参与人的下划线」——由本组件内部渲染（anchor 即堆叠自身，故宽度自适应）。
//   红点：某参与人发布了「当前登录用户未读」的评论 → 该头像右上角亮红点。
//   +N 圆形：与头像同尺寸的圆形徽标（对齐讨论区气泡下已读头像堆叠的 +N 视觉）。
//
// 点击：整个堆叠是一个按钮，点击直接跳工单详情页讨论区（按作者定位到其最近一条评论）。
//   不在此处展开名单浮层——名单信息以详情页讨论区为准，避免列表页承载过重交互。
import AvatarImg from '@/shared/components/AvatarImg';
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
  /** 点击堆叠跳详情页讨论区（由调用方拼接路由） */
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
  const list = (participants || []).filter((p) => p && p.username);
  if (list.length === 0) return null;

  const visible = list.slice(0, MAX_VISIBLE_PARTICIPANTS);
  const overflow = list.length - visible.length;
  const unreadCount = list.filter((p) => p.has_unread).length;
  const title = `参与人：${list.map((p) => p.name || p.username).join('、')}`
    + (unreadCount > 0 ? `（${unreadCount} 人有未读评论）` : '');

  return (
    <button
      type="button"
      className="task-card2__participants participant-stack"
      title={title}
      aria-label={title}
      onClick={(e) => {
        // 阻止冒泡：卡片整体 onClick 也进详情页，这里走「带作者定位」的专用入口
        e.stopPropagation();
        // 跳转语义：以堆叠中最新发言者（后端已按「评论数降序 → 时间降序」排序）为定位目标
        const target = list[0];
        if (target) onLocate?.(target);
      }}
    >
      {visible.map((p) => <StackAvatar key={p.username} item={p} avatarMap={avatarMap} />)}
      {overflow > 0 && (
        <span className="task-card2__participant task-card2__participant--overflow">+{overflow}</span>
      )}
      {/* 堆叠下方的「长横线 + 实心三角箭头」。
          为何不用 lucide ArrowRight：它是 24x24 固定 viewBox 图标，
          图形只占中间一小块（四周有留白），且斜线是 stroke 描边，
          改成 fill 会填充出 V 形轮廓 → 视觉上「线与箭头断开」。
          故：横线由 CSS ::before 撑开（flex:1），三角头用自绘 SVG（顶满 viewBox、无留白），
          两者在 flex 容器内零间距相接，天然连成一体。
          pointer-events:none 保证点击穿透到 button 本体（点箭头也跳讨论区）。 */}
      <span className="task-card2__person-arrow" aria-hidden="true">
        <svg
          className="task-card2__person-arrow-head"
          viewBox="0 0 6 10"
          width="6"
          height="10"
          focusable="false"
        >
          {/* 实心三角：顶满 viewBox 边界，根部（左侧竖直边）与横线严丝合缝 */}
          <path d="M0 0 L6 5 L0 10 Z" />
        </svg>
      </span>
    </button>
  );
}
