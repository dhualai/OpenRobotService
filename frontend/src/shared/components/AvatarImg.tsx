// 头像图片统一兜底组件
// 场景：users.avatar_resource_id 悬空（如跨环境导入的用户数据）/ 头像对象丢失 / 当前环境无对应资源时，
// 资源下载接口会返回 404，若直接渲染 <img> 会显示破图并产生无意义的 404 请求噪音。
// 本组件在「无 src 或图片加载失败」时渲染调用方传入的占位（与"无头像"分支同款的首字母/图标），
// 加载成功时渲染普通 <img>（透传 className/其余 img 属性，样式与原来完全一致）。
// src 变化（如上传新头像、列表翻页）后自动复位失败态并重新加载。
import { useEffect, useRef, useState } from 'react';

export interface AvatarImgProps
  extends Omit<React.ImgHTMLAttributes<HTMLImageElement>, 'src' | 'onError' | 'onLoad'> {
  /** 头像图片地址；空/undefined 时不发起请求，直接渲染 fallback */
  src?: string | null;
  /** 加载失败 / 无 src 时的占位内容；缺省渲染 alt 首字符文本 */
  fallback?: React.ReactNode;
}

export default function AvatarImg({ src, alt, className, fallback, ...imgProps }: AvatarImgProps) {
  const [failed, setFailed] = useState(false);
  // 头像源变化时复位失败态，避免更换头像/加载新列表后停留在旧占位
  const prevSrcRef = useRef<string | null | undefined>(src);
  useEffect(() => {
    if (prevSrcRef.current !== src) {
      prevSrcRef.current = src;
      setFailed(false);
    }
  }, [src]);

  if (!src || failed) {
    return fallback ?? (
      <span className={className} aria-label={alt}>
        {(alt || '?').slice(0, 1).toUpperCase()}
      </span>
    );
  }
  return (
    <img
      {...imgProps}
      className={className}
      src={src}
      alt={alt}
      onError={() => setFailed(true)}
    />
  );
}
