import { Button, Toast } from 'tdesign-mobile-react';
import { setupWechatShare } from '@/shared/utils/wechatJsSdk';
import { WECHAT_CONFIG } from '@/config/wechat';

interface WechatShareButtonProps {
  /** 分享卡片标题（建议 ≤30 字） */
  title: string;
  /** 分享卡片描述/摘要（建议 ≤50 字） */
  desc: string;
  /** 缩略图 URL（公网可访问）。留空则回退到 WECHAT_CONFIG.shareImgUrl，再否则用微信默认图 */
  imgUrl?: string;
  /** 点击卡片跳转地址。留空则取当前页面 URL（需在公众号 JS 接口安全域名内） */
  link?: string;
  /** 展示形态：button=文字按钮（默认，用于操作区）；icon=仅图标（用于导航栏右上角） */
  variant?: 'button' | 'icon';
  /** 按钮文案（仅 button 形态使用） */
  label?: string;
}

/** 转发（分享）图标：三个节点的连接图形，内联 SVG 不依赖图标库 */
const ShareIcon = () => (
  <svg
    width="20"
    height="20"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
  >
    <circle cx="18" cy="5" r="3" />
    <circle cx="6" cy="12" r="3" />
    <circle cx="18" cy="19" r="3" />
    <line x1="8.59" y1="13.51" x2="15.42" y2="17.49" />
    <line x1="15.41" y1="6.51" x2="8.59" y2="10.49" />
  </svg>
);

/**
 * 微信分享按钮（辅推方案：JS-SDK 自定义分享卡片）。
 * 点击后通过 JS-SDK 预置「转发到微信群/好友/朋友圈」的卡片元信息，并 Toast 提示用户
 * 在微信内点右上角「…」实际转发。JS-SDK 分享是「配置卡片」而非「自动发送」，故需用户手动触发。
 * 不在微信内或未启用 JS-SDK 时给出友好提示，不报错。
 */
export default function WechatShareButton({
  title,
  desc,
  imgUrl,
  link,
  variant = 'button',
  label = '转发到微信群',
}: WechatShareButtonProps) {
  const handleShare = async () => {
    const shareLink = link || window.location.href.split('#')[0];
    const ok = await setupWechatShare({
      title,
      desc,
      link: shareLink,
      imgUrl: imgUrl || WECHAT_CONFIG.shareImgUrl,
    });
    if (ok) {
      Toast({ message: '已就绪，请点击右上角「…」转发到微信群', theme: 'success' });
    } else {
      Toast({ message: '请在微信中打开本页面后转发', theme: 'warning' });
    }
  };

  if (variant === 'icon') {
    return (
      <span
        className="wechat-share-icon"
        role="button"
        tabIndex={0}
        aria-label={label}
        onClick={handleShare}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            handleShare();
          }
        }}
      >
        <ShareIcon />
      </span>
    );
  }

  return (
    <Button size="small" variant="outline" theme="default" onClick={handleShare}>
      {label}
    </Button>
  );
}
