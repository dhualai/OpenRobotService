import { useLayoutEffect, useRef, useState } from 'react';
import { ChevronDown } from 'lucide-react';

function collapseDupLines(text: string): string {
  const lines = text.replace(/\r\n/g, '\n').split('\n').map((l) => l.trim()).filter(Boolean);
  return lines.filter((l, i) => l !== lines[i - 1]).join('\n');
}

/** 派单提醒 / 派单原因：一行放得下就不折叠；超出才省略并点开看全文。 */
export default function DispatchFold({
  label,
  text,
  variant,
}: {
  label: string;
  text: string;
  variant: 'tip' | 'reason';
}) {
  const [open, setOpen] = useState(false);
  const [overflows, setOverflows] = useState(false);
  const clipRef = useRef<HTMLSpanElement>(null);
  const cleaned = collapseDupLines(text);
  const preview = cleaned.replace(/\s+/g, ' ');
  const hardBreak = cleaned.includes('\n');
  const foldable = overflows || hardBreak;

  useLayoutEffect(() => {
    const el = clipRef.current;
    if (!el) return;

    const measure = () => {
      const shown = getComputedStyle(el).display !== 'none';
      if (!shown) return;
      const clipped = el.scrollWidth > el.clientWidth + 1;
      setOverflows(clipped);
      if (!clipped && !hardBreak) setOpen(false);
    };

    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    const fold = el.closest('.dispatch-fold');
    if (fold) ro.observe(fold);
    window.addEventListener('resize', measure);
    return () => {
      ro.disconnect();
      window.removeEventListener('resize', measure);
    };
  }, [preview, hardBreak, open]);

  if (!cleaned) return null;

  return (
    <div className={`dispatch-fold dispatch-fold--${variant}${foldable ? ' is-foldable' : ''}${open && foldable ? ' is-open' : ''}`}>
      <button
        type="button"
        className="dispatch-fold__header"
        onClick={() => { if (foldable) setOpen((v) => !v); }}
        aria-expanded={foldable ? open : undefined}
      >
        <span className="dispatch-fold__preview">
          <span className="dispatch-fold__label">{label}</span>
          <span className="dispatch-fold__sep">：</span>
          <span ref={clipRef} className="dispatch-fold__clip">{preview}</span>
        </span>
        {foldable ? <ChevronDown size={14} className="dispatch-fold__chevron" aria-hidden /> : null}
      </button>
      {foldable ? (
        <div className="dispatch-fold__bodywrap">
          <div className="dispatch-fold__body">{cleaned}</div>
        </div>
      ) : null}
    </div>
  );
}
