// 通用分页组件
interface PaginationProps {
  current: number;
  total: number;
  pageSize: number;
  onChange: (page: number) => void;
}

export default function Pagination({ current, total, pageSize, onChange }: PaginationProps) {
  const totalPages = Math.ceil(total / pageSize);
  if (totalPages <= 1) return null;

  const pages: number[] = [];
  for (let i = 1; i <= totalPages; i++) {
    pages.push(i);
  }

  return (
    <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', gap: 8, padding: '16px 0' }}>
      <button
        disabled={current <= 1}
        onClick={() => onChange(current - 1)}
        style={{
          padding: '6px 12px',
          border: '1px solid #ddd',
          borderRadius: 4,
          background: current <= 1 ? '#f5f5f5' : '#fff',
          cursor: current <= 1 ? 'default' : 'pointer',
        }}
      >
        上一页
      </button>
      {pages.map((p) => (
        <button
          key={p}
          onClick={() => onChange(p)}
          style={{
            padding: '6px 12px',
            border: '1px solid #ddd',
            borderRadius: 4,
            background: p === current ? '#0052d9' : '#fff',
            color: p === current ? '#fff' : '#333',
            cursor: 'pointer',
            fontWeight: p === current ? 600 : 400,
          }}
        >
          {p}
        </button>
      ))}
      <button
        disabled={current >= totalPages}
        onClick={() => onChange(current + 1)}
        style={{
          padding: '6px 12px',
          border: '1px solid #ddd',
          borderRadius: 4,
          background: current >= totalPages ? '#f5f5f5' : '#fff',
          cursor: current >= totalPages ? 'default' : 'pointer',
        }}
      >
        下一页
      </button>
    </div>
  );
}
