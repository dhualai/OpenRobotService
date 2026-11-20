/**
 * 图片客户端压缩：上传前用 canvas 重采样到限定尺寸并转 JPEG/WebP，
 * 大幅减小上行体积（10MB 原图 → 1-2MB），降低 TTFB 与 VLM base64 编码开销。
 *
 * 非图片文件原样返回。压缩失败（如 SVG/损坏图）降级返回原文件，不阻塞上传。
 */

/** 压缩结果 */
export interface CompressResult {
  /** 压缩后的 File（同名，type 可能改为 jpeg/webp）；无需压缩或失败时返回原文件 */
  file: File;
  /** 是否实际进行了压缩 */
  compressed: boolean;
  /** 压缩前大小（字节） */
  originalSize: number;
  /** 压缩后大小（字节） */
  resultSize: number;
}

const DEFAULT_MAX_DIM = 1920;       // 最长边上限（px）
const DEFAULT_QUALITY = 0.82;       // JPEG 质量
const MIN_SIZE_TO_COMPRESS = 300 * 1024; // <300KB 不压缩（收益小，省一次解码）

/** 读取 File 为 HTMLImageElement（用 createImageBitmap 优先，更快且不污染 DOM） */
async function loadImage(file: File): Promise<{ bitmap: ImageBitmap | null; img: HTMLImageElement | null }> {
  // createImageBitmap 在微信 WebView 支持不全，降级到 <img>
  if (typeof createImageBitmap === 'function') {
    try {
      const bitmap = await createImageBitmap(file);
      return { bitmap, img: null };
    } catch {
      /* 降级 */
    }
  }
  return new Promise((resolve) => {
    const img = new Image();
    const url = URL.createObjectURL(file);
    img.onload = () => {
      URL.revokeObjectURL(url);
      resolve({ bitmap: null, img });
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      resolve({ bitmap: null, img: null });
    };
    img.src = url;
  });
}

function getDimensions(w: number, h: number, maxDim: number): { w: number; h: number } {
  if (w <= maxDim && h <= maxDim) return { w, h };
  if (w >= h) {
    return { w: maxDim, h: Math.round((h / w) * maxDim) };
  }
  return { w: Math.round((w / h) * maxDim), h: maxDim };
}

/** 选择输出 MIME：WebP 体积更小且主流浏览器/微信支持；不支持时回退 jpeg */
function pickOutputMime(): { mime: string; ext: string } {
  const canvas = document.createElement('canvas');
  if (canvas.toDataURL('image/webp', 0.8).startsWith('data:image/webp')) {
    return { mime: 'image/webp', ext: '.webp' };
  }
  return { mime: 'image/jpeg', ext: '.jpg' };
}

/**
 * 压缩图片文件。
 * @param file 原始 File
 * @param opts.maxDim 最长边像素上限，默认 1920
 * @param opts.quality JPEG/WebP 质量 0~1，默认 0.82
 */
export async function compressImage(
  file: File,
  opts: { maxDim?: number; quality?: number } = {},
): Promise<CompressResult> {
  const originalSize = file.size;
  const maxDim = opts.maxDim ?? DEFAULT_MAX_DIM;
  const quality = opts.quality ?? DEFAULT_QUALITY;

  // 非图片或太小：不压缩
  if (!file.type.startsWith('image/')) {
    return { file, compressed: false, originalSize, resultSize: originalSize };
  }
  // SVG 是矢量，canvas 渲染会丢失矢量特性且可能失真，跳过
  if (file.type === 'image/svg+xml') {
    return { file, compressed: false, originalSize, resultSize: originalSize };
  }
  // GIF 动图：canvas 只能渲染第一帧，压缩后会丢失动画，跳过
  if (file.type === 'image/gif') {
    return { file, compressed: false, originalSize, resultSize: originalSize };
  }
  if (originalSize < MIN_SIZE_TO_COMPRESS) {
    return { file, compressed: false, originalSize, resultSize: originalSize };
  }

  try {
    const { bitmap, img } = await loadImage(file);
    const srcW = bitmap?.width ?? img?.naturalWidth ?? 0;
    const srcH = bitmap?.height ?? img?.naturalHeight ?? 0;
    if (!srcW || !srcH) {
      return { file, compressed: false, originalSize, resultSize: originalSize };
    }
    const { w: outW, h: outH } = getDimensions(srcW, srcH, maxDim);
    const canvas = document.createElement('canvas');
    canvas.width = outW;
    canvas.height = outH;
    const ctx = canvas.getContext('2d');
    if (!ctx) {
      return { file, compressed: false, originalSize, resultSize: originalSize };
    }
    // 白底（PNG 透明转 JPEG 不会变黑）
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, outW, outH);
    const source = bitmap ?? img;
    ctx.drawImage(source as CanvasImageSource, 0, 0, outW, outH);
    if (bitmap) bitmap.close();

    const { mime, ext } = pickOutputMime();
    const blob: Blob | null = await new Promise((resolve) =>
      canvas.toBlob(resolve, mime, quality),
    );
    if (!blob) {
      return { file, compressed: false, originalSize, resultSize: originalSize };
    }
    // 压缩后反而更大（极少见，如已高度压缩的图）：保留原文件
    if (blob.size >= originalSize) {
      return { file, compressed: false, originalSize, resultSize: originalSize };
    }
    // 改名：原 stem + 新扩展名（保留原名可读性，后端 object_path 用此名）
    const stem = file.name.replace(/\.[^.]+$/, '');
    const newName = `${stem}${ext}`;
    const compressedFile = new File([blob], newName, { type: mime, lastModified: Date.now() });
    return { file: compressedFile, compressed: true, originalSize, resultSize: blob.size };
  } catch {
    return { file, compressed: false, originalSize, resultSize: originalSize };
  }
}
