// 通用分页组件 —— 支持省略号策略
interface PaginationProps {
  current: number;
  total: number;
  pageSize: number;
  onChange: (page: number) => void;
  siblings?: number; // 当前页两侧显示的页码数量，默认 1
}

const DEFAULT_SIBLINGS = 1;
const BOUNDARY_COUNT = 1; // 始终显示的首尾页数量

/**
 * 生成带省略号的页码序列。
 *
 * 示例（total=20, current=10, siblings=1）：
 *   [1, '...', 9, 10, 11, '...', 20]
 *
 * 规则：
 *  - 首尾各保留 BOUNDARY_COUNT 个页码始终可见
 *  - 当前页两侧各保留 siblings 个页码可见
 *  - 其余区间用 '...' 占位
 */
function buildPageList(
  current: number,
  totalPages: number,
  siblings: number,
): (number | '...')[] {
  const totalNumbers = siblings * 2 + BOUNDARY_COUNT * 2 + 3; // 首+尾+当前+两侧+2个省略号

  // 总页数较少时直接全部展示
  if (totalPages <= totalNumbers) {
    return Array.from({ length: totalPages }, (_, i) => i + 1);
  }

  const leftSibling = Math.max(current - siblings, BOUNDARY_COUNT + 1);
  const rightSibling = Math.min(current + siblings, totalPages - BOUNDARY_COUNT);

  const showLeftEllipsis = leftSibling > BOUNDARY_COUNT + 1;
  const showRightEllipsis = rightSibling < totalPages - BOUNDARY_COUNT;

  const result: (number | '...')[] = [];

  // 左侧
  result.push(1);
  if (showLeftEllipsis) {
    result.push('...');
  } else if (BOUNDARY_COUNT + 1 < leftSibling) {
    // 左边界到左窗口之间的省略（当前页靠近开头时）
    for (let i = BOUNDARY_COUNT + 1; i < leftSibling; i++) {
      result.push(i);
    }
  }

  // 中间窗口
  for (let i = leftSibling; i <= rightSibling; i++) {
    result.push(i);
  }

  // 右侧
  if (showRightEllipsis) {
    result.push('...');
  } else if (rightSibling < totalPages - BOUNDARY_COUNT) {
    // 右窗口到右边界之间的省略（当前页靠近结尾时）
    for (let i = rightSibling + 1; i < totalPages - BOUNDARY_COUNT + 1; i++) {
      result.push(i);
    }
  }

  if (BOUNDARY_COUNT < totalPages) {
    result.push(totalPages);
  }

  return result;
}

export default function Pagination({
  current,
  total,
  pageSize,
  onChange,
  siblings = DEFAULT_SIBLINGS,
}: PaginationProps) {
  const totalPages = Math.ceil(total / pageSize);
  if (totalPages <= 1) return null;

  const pages = buildPageList(current, totalPages, siblings);

  // 与 macaron 组件适配：圆角用 --radius-sm（10px），选中态用马卡龙蓝主色
  const btnStyle = (isActive: boolean) => ({
    padding: '6px 12px',
    border: '1px solid var(--mac-border)',
    borderRadius: 'var(--radius-sm)',
    background: isActive ? 'var(--mac-blue-2)' : '#fff',
    color: isActive ? '#fff' : 'var(--mac-fg)',
    cursor: 'pointer',
    fontWeight: isActive ? 600 : 400,
    minWidth: 32,
    textAlign: 'center' as const,
  });

  return (
    <div
      style={{
        display: 'flex',
        justifyContent: 'center',
        alignItems: 'center',
        gap: 8,
        padding: '16px 0',
        flexShrink: 0,
      }}
    >
      <button
        disabled={current <= 1}
        onClick={() => onChange(current - 1)}
        style={{
          ...btnStyle(false),
          background: current <= 1 ? 'var(--mac-secondary)' : '#fff',
          cursor: current <= 1 ? 'default' : 'pointer',
        }}
      >
        上一页
      </button>

      {pages.map((p, idx) => {
        if (p === '...') {
          return (
            <span
              key={`ellipsis-${idx}`}
              style={{
                padding: '6px 4px',
                color: '#999',
                userSelect: 'none' as const,
              }}
            >
              …
            </span>
          );
        }
        return (
          <button
            key={p}
            onClick={() => onChange(p)}
            style={btnStyle(p === current)}
          >
            {p}
          </button>
        );
      })}

      <button
        disabled={current >= totalPages}
        onClick={() => onChange(current + 1)}
        style={{
          ...btnStyle(false),
          background: current >= totalPages ? 'var(--mac-secondary)' : '#fff',
          cursor: current >= totalPages ? 'default' : 'pointer',
        }}
      >
        下一页
      </button>
    </div>
  );
}
