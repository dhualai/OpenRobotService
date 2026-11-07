import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import ImageLightbox from '@/shared/components/ImageLightbox';
import { toAppUrl } from '@/shared/utils/markdown';

const UPLOADED_IMAGE = /^\/api\/tasks\/files\/[A-Za-z0-9_-]+\/spec-doc\/images\/[0-9a-f]{32}\.(?:png|jpg|jpeg|gif|webp|bmp)$/;

function UploadedAnswerImage({ src, alt }: { src: string; alt?: string }) {
  const [preview, setPreview] = useState(false);
  const imageUrl = toAppUrl(src);
  return <>
    <button type="button" className="challenge-answer-inline-image" aria-label={`预览${alt || '解答图片'}`} onClick={() => setPreview(true)}>
      <img src={imageUrl} alt={alt || '解答图片'} loading="lazy" />
    </button>
    <ImageLightbox src={imageUrl} alt={alt || '解答图片'} open={preview} onClose={() => setPreview(false)} />
  </>;
}

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
    <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml urlTransform={(value, key) => key === 'src'
      ? UPLOADED_IMAGE.test(value) ? value : ''
      : referenceUrl(value)} components={{
      a: ({ href, children }) => href
        ? <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>
        : <span>{children}</span>,
      img: ({ src, alt }) => src && UPLOADED_IMAGE.test(src)
        ? <UploadedAnswerImage src={src} alt={alt} />
        : <span>{alt || '图片'}</span>,
    }}>{content}</ReactMarkdown>
  </div>;
}
