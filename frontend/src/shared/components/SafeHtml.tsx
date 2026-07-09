// HTML 安全渲染组件 - 基于 dompurify
import { useMemo } from 'react';
import DOMPurify from 'dompurify';

interface SafeHtmlProps {
  html: string;
  className?: string;
  tag?: string;
}

export default function SafeHtml({ html, className, tag = 'div' }: SafeHtmlProps) {
  const sanitized = useMemo(() => DOMPurify.sanitize(html), [html]);
  const Component = tag as keyof JSX.IntrinsicElements;
  return <Component className={className} dangerouslySetInnerHTML={{ __html: sanitized }} />;
}
