import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

function referenceUrl(value: string): string {
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? value : '';
  } catch {
    return '';
  }
}

export default function ChallengeAnswerContent({ content }: { content: string }) {
  return <div className="challenge-content challenge-answer-content">
    <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml urlTransform={referenceUrl} components={{
      a: ({ href, children }) => href
        ? <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>
        : <span>{children}</span>,
      img: ({ alt }) => <span>{alt || '图片'}</span>,
    }}>{content}</ReactMarkdown>
  </div>;
}
