// 通用可过滤下拉选择器：下拉展开选项列表 + 模糊搜索过滤
// 用于选项为简单字符串的场景（如公司、部门）。复用 user-select__* 样式。
import { useMemo, useState } from 'react';

interface Props {
  value?: string;
  onChange?: (val: string) => void;
  options: string[];
  placeholder?: string;
  title?: string;
  searchPlaceholder?: string;
}

export default function FilterableSelect({
  value,
  onChange,
  options,
  placeholder = '请选择',
  title = '选择',
  searchPlaceholder = '搜索…',
}: Props) {
  const [visible, setVisible] = useState(false);
  const [keyword, setKeyword] = useState('');

  const filtered = useMemo(() => {
    const kw = keyword.trim().toLowerCase();
    return kw ? options.filter((o) => o.toLowerCase().includes(kw)) : options;
  }, [options, keyword]);

  const handlePick = (val: string) => {
    onChange?.(val);
    setVisible(false);
    setKeyword('');
  };

  return (
    <div className="user-select">
      <button type="button" className="user-select__trigger" onClick={() => setVisible(true)}>
        {value ? (
          <span className="user-select__trigger-text">{value}</span>
        ) : (
          <span className="user-select__trigger-placeholder">{placeholder}</span>
        )}
        <span className="user-select__arrow">▾</span>
      </button>

      {visible && (
        <>
          <div className="user-select__mask" onClick={() => setVisible(false)} />
          <div className="user-select__panel">
            <div className="user-select__panel-header">
              <span>{title}</span>
              <span className="user-select__close" onClick={() => setVisible(false)}>✕</span>
            </div>
            <input
              className="tasks-search user-select__search"
              placeholder={searchPlaceholder}
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
            />
            <div className="user-select__list">
              {filtered.length === 0 ? (
                <div className="user-select__empty">未找到匹配项</div>
              ) : (
                filtered.map((o) => (
                  <div
                    key={o}
                    className={`user-select__item ${o === value ? 'is-selected' : ''}`}
                    onClick={() => handlePick(o)}
                  >
                    <div className="user-select__item-name">{o}</div>
                  </div>
                ))
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
