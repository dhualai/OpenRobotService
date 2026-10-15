import { type ReactNode, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

export function Modal({
  title,
  children,
  footer,
  wide,
  onClose,
}: {
  title: string;
  children?: ReactNode;
  footer: ReactNode;
  wide?: boolean;
  onClose: () => void;
}) {
  const card = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    card.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.preventDefault();
      closeRef.current();
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = prev;
      document.removeEventListener("keydown", onKey);
    };
  }, []);
  return createPortal(
    <div className="dlg" onClick={() => closeRef.current()}>
      <div
        ref={card}
        className={wide ? "dlg-card is-wide" : "dlg-card"}
        role="dialog"
        aria-modal="true"
        aria-labelledby="dlg-title"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id="dlg-title">{title}</h2>
        {children}
        <div className="dlg-foot">{footer}</div>
      </div>
    </div>,
    document.body,
  );
}

export function Confirm({
  title,
  body,
  note,
  confirm = "确定",
  danger,
  pending,
  error,
  onCancel,
  onConfirm,
}: {
  title: string;
  body?: string;
  note?: string;
  confirm?: string;
  danger?: boolean;
  pending?: boolean;
  error?: string;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const go = useRef(onConfirm);
  go.current = onConfirm;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Enter" || pending) return;
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "TEXTAREA" || t.tagName === "BUTTON")) return;
      e.preventDefault();
      go.current();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [pending]);
  return (
    <Modal
      title={title}
      onClose={onCancel}
      footer={
        <>
          <button type="button" className="btn" onClick={onCancel} disabled={pending}>
            取消
          </button>
          <button
            type="button"
            className={danger ? "btn danger" : "btn primary"}
            disabled={pending}
            onClick={onConfirm}
          >
            {confirm}
          </button>
        </>
      }
    >
      {body ? <p className="dlg-body">{body}</p> : null}
      {note ? <p className="dlg-note">{note}</p> : null}
      {error ? <p className="err">{error}</p> : null}
    </Modal>
  );
}

export function PopupMenu({
  x,
  y,
  onClose,
  children,
}: {
  x: number;
  y: number;
  onClose: () => void;
  children: ReactNode;
}) {
  const box = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  const [pos, setPos] = useState({ left: x, top: y });
  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    setPos({
      left: Math.max(8, Math.min(x, window.innerWidth - r.width - 8)),
      top: Math.max(8, Math.min(y, window.innerHeight - r.height - 8)),
    });
  }, [x, y]);
  useEffect(() => {
    function onPtr(e: PointerEvent) {
      if (!box.current?.contains(e.target as Node)) closeRef.current();
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") closeRef.current();
    }
    function onScroll() {
      closeRef.current();
    }
    document.addEventListener("pointerdown", onPtr, true);
    document.addEventListener("keydown", onKey);
    document.addEventListener("scroll", onScroll, true);
    return () => {
      document.removeEventListener("pointerdown", onPtr, true);
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("scroll", onScroll, true);
    };
  }, []);
  return createPortal(
    <div ref={box} className="ctx-menu" role="menu" style={{ left: pos.left, top: pos.top }}>
      {children}
    </div>,
    document.body,
  );
}
