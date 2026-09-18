// 人员流转「自适应流线箭头」（发起人 ——→ 处理人），列表卡片 / 详情页共用。
//
// 结构：细线（<i>，flex:1 撑满）+ 箭头头部（SVG，反向 V 形折线），两段零间距相接。
// 线身长度随可用空间自适应伸缩：
//   无堆叠头像 → 容器固定 32px（线身 25 + 头部 7）；
//   有堆叠头像 → 容器由 left/right 相对堆叠撑开，线身随之变长。
//
// 为何不整根 SVG：线长需自适应，而 preserveAspectRatio="none" 拉伸会把箭头头部
// 一起拉变形；固定 viewBox 的整根箭头无法只伸长线身。故拆「CSS 线身 + SVG 头部」。
//
// ⚠️ 连接要点（踩过坑）：SVG 头部是「左端张开、右端收成尖」的 V 形，中线附近
// 的墨迹只在右端翼尖处。若线身止于 SVG 左边界（x=0），中线处就会出现约 5.5px
// 空白 → 视觉上「线与箭头断开」。因此头部 SVG 用负左外边距让线身右端探入开口内，
// 直达翼尖 x 坐标，中线才是连续墨迹（像素扫描段数必须为 1）。
//
// 颜色用 currentColor 驱动（本组件根节点 .task-card2__person-arrow 的 color），
// 历史卡片 / 详情页的蓝色 token 覆盖自动生效。
type Props = {
  /** 线身粗细（= 头部描边宽，px）。默认 1.2，比旧图标（2）更细 */
  strokeWidth?: number;
  /** 头部尺寸：宽（翅展的一半 = 两翼水平投影，px）。高度自动按 2 倍换算（两翼 45°） */
  headWidth?: number;
};

/** 头部宽度默认值。⚠️ 与 global.css 中 `.person-arrow__head { margin-left: -7px }` 必须一致 */
export const HEAD_WIDTH = 7;

export default function PersonArrow({ strokeWidth = 1.2, headWidth = HEAD_WIDTH }: Props) {
  // 头部 viewBox：宽 = headWidth（翼尖到两翼开口端的水平距离），高 = 2 × headWidth（45°）
  const headHeight = headWidth * 2;
  return (
    <span className="task-card2__person-arrow" aria-hidden="true">
      {/* 线身：flex:1 自适应撑满；右端探入头部开口、直达翼尖，保证中线连续 */}
      <i className="person-arrow__line" style={{ height: strokeWidth }} />
      {/* 箭头头部：V 形折线（左端张开、右端收尖），负左外边距与线身交叠。
          margin-left 内联传入，与 headWidth 同源，避免改 prop 后 CSS 里的 -7px 失配。 */}
      <svg
        className="person-arrow__head"
        width={headWidth}
        height={headHeight}
        viewBox={`0 0 ${headWidth} ${headHeight}`}
        style={{ marginLeft: -headWidth }}
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
        focusable="false"
      >
        <path
          d={`M0 0 L${headWidth} ${headHeight / 2} L0 ${headHeight}`}
          stroke="currentColor"
          strokeWidth={strokeWidth}
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </span>
  );
}
