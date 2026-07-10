// HTML 安全渲染组件 - 基于 dompurify
import { useMemo, createElement } from 'react';
import DOMPurify from 'dompurify';

interface SafeHtmlProps {
  html: string;
  className?: string;
  tag?: string;
}

export default function SafeHtml({ html, className, tag = 'div' }: SafeHtmlProps) {
  const sanitized = useMemo(() => DOMPurify.sanitize(html), [html]);
  return createElement(tag, {
    className,
    dangerouslySetInnerHTML: { __html: sanitized },
  });
}
