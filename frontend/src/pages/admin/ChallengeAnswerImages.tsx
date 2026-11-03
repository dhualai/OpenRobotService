import { useState } from 'react';
import ImageLightbox from '@/shared/components/ImageLightbox';
import { toAppUrl } from '@/shared/utils/markdown';

export default function ChallengeAnswerImages({ images = [], onRemove, disabled = false }: {
  images?: string[]; onRemove?: (index: number) => void; disabled?: boolean;
}) {
  const [preview, setPreview] = useState<string>();
  if (!images.length) return null;
  return <div className="challenge-answer-images" aria-label="解答图片">
    {images.map((image, index) => <div className="challenge-answer-images__item" key={`${image}:${index}`}>
      <button type="button" className="challenge-answer-images__preview" aria-label={`预览解答图片${index + 1}`} onClick={() => setPreview(toAppUrl(image))}>
        <img src={toAppUrl(image)} alt={`解答图片${index + 1}`} loading="lazy" />
      </button>
      {onRemove && <button type="button" className="knowledge-link" disabled={disabled} onClick={() => onRemove(index)} aria-label={`删除解答图片${index + 1}`}>删除</button>}
    </div>)}
    <ImageLightbox src={preview} alt="解答图片" open={!!preview} onClose={() => setPreview(undefined)} />
  </div>;
}
