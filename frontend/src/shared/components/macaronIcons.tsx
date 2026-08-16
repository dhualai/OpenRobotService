// 马卡龙后台管理页共用图标：与 macaron-minimal-ui 原型的 lucide 图标同款路径的
// 18px 线条 SVG（stroke=currentColor，颜色由外层 CSS 控制）。
import type { ReactNode } from 'react';

function Icon({ children, size = 18 }: { children: ReactNode; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {children}
    </svg>
  );
}

export const MacChevronRight = ({ size }: { size?: number }) => (
  <Icon size={size}>
    <path d="m9 18 6-6-6-6" />
  </Icon>
);

export const MacChevronDown = ({ size }: { size?: number }) => (
  <Icon size={size}>
    <path d="m6 9 6 6 6-6" />
  </Icon>
);

export const MacCheck = ({ size }: { size?: number }) => (
  <Icon size={size}>
    <path d="M20 6 9 17l-5-5" />
  </Icon>
);

export const MacSearch = ({ size }: { size?: number }) => (
  <Icon size={size}>
    <circle cx="11" cy="11" r="8" />
    <path d="m21 21-4.3-4.3" />
  </Icon>
);

export const MacPlus = ({ size }: { size?: number }) => (
  <Icon size={size}>
    <path d="M5 12h14" />
    <path d="M12 5v14" />
  </Icon>
);

/* lucide users */
export const MacUsers = ({ size }: { size?: number }) => (
  <Icon size={size}>
    <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
    <circle cx="9" cy="7" r="4" />
    <path d="M22 21v-2a4 4 0 0 0-3-3.87" />
    <path d="M16 3.13a4 4 0 0 1 0 7.75" />
  </Icon>
);

/* lucide tags */
export const MacTags = ({ size }: { size?: number }) => (
  <Icon size={size}>
    <path d="m15 5 6.3 6.3a2.4 2.4 0 0 1 0 3.4L17 19" />
    <path d="M9.586 5.586A2 2 0 0 0 8.172 5H3a1 1 0 0 0-1 1v5.172a2 2 0 0 0 .586 1.414L8.29 18.29a2.426 2.426 0 0 0 3.42 0l3.58-3.58a2.426 2.426 0 0 0 0-3.42z" />
    <circle cx="6.5" cy="9.5" r=".5" fill="currentColor" />
  </Icon>
);

/* lucide key-round */
export const MacKeyRound = ({ size }: { size?: number }) => (
  <Icon size={size}>
    <path d="M2.586 17.414A2 2 0 0 0 2 18.828V21a1 1 0 0 0 1 1h3a1 1 0 0 0 1-1v-1a1 1 0 0 1 1-1h1a1 1 0 0 0 1-1v-1a1 1 0 0 1 1-1h.172a2 2 0 0 0 1.414-.586l.814-.814a6.5 6.5 0 1 0-4-4z" />
    <circle cx="16.5" cy="7.5" r=".5" fill="currentColor" />
  </Icon>
);

/* lucide user-cog */
export const MacUserCog = ({ size }: { size?: number }) => (
  <Icon size={size}>
    <circle cx="18" cy="15" r="3" />
    <circle cx="9" cy="7" r="4" />
    <path d="M10 15H6a4 4 0 0 0-4 4v2" />
    <path d="m21.7 16.4-.9-.3" />
    <path d="m15.2 13.9-.9-.3" />
    <path d="m16.6 18.7.3-.9" />
    <path d="m19.1 12.2.3-.9" />
    <path d="m19.6 18.7-.4-1" />
    <path d="m16.8 12.3-.4-1" />
    <path d="m14.3 16.6 1-.4" />
    <path d="m20.7 13.8 1-.4" />
  </Icon>
);

/* lucide shuffle */
export const MacShuffle = ({ size }: { size?: number }) => (
  <Icon size={size}>
    <path d="m18 14 4 4-4 4" />
    <path d="m18 2 4 4-4 4" />
    <path d="M2 18h1.973a4 4 0 0 0 3.3-1.7l5.454-8.6a4 4 0 0 1 3.3-1.7H22" />
    <path d="M2 6h1.972a4 4 0 0 1 3.6 2.2" />
    <path d="M22 18h-6.041a4 4 0 0 1-3.3-1.8l-.359-.45" />
  </Icon>
);

/* lucide scroll-text */
export const MacScrollText = ({ size }: { size?: number }) => (
  <Icon size={size}>
    <path d="M15 12h-5" />
    <path d="M15 8h-5" />
    <path d="M19 17V5a2 2 0 0 0-2-2H4" />
    <path d="M8 21h12a2 2 0 0 0 2-2v-1a1 1 0 0 0-1-1H11a1 1 0 0 0-1 1v1a2 2 0 1 1-4 0V5a2 2 0 1 0-4 0v2a1 1 0 0 0 1 1h3" />
  </Icon>
);

/* lucide folder-closed */
export const MacFolderClosed = ({ size }: { size?: number }) => (
  <Icon size={size}>
    <path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z" />
    <path d="M2 10h20" />
  </Icon>
);

/* lucide wallet */
export const MacWallet = ({ size }: { size?: number }) => (
  <Icon size={size}>
    <path d="M19 7V4a1 1 0 0 0-1-1H5a2 2 0 0 0 0 4h15a1 1 0 0 1 1 1v4h-3a2 2 0 0 0 0 4h3a1 1 0 0 0 1-1v-2a1 1 0 0 0-1-1" />
    <path d="M3 5v14a2 2 0 0 0 2 2h15a1 1 0 0 0 1-1v-4" />
  </Icon>
);

/* lucide calendar-days */
export const MacCalendarDays = ({ size }: { size?: number }) => (
  <Icon size={size}>
    <path d="M8 2v4" />
    <path d="M16 2v4" />
    <rect width="18" height="18" x="3" y="4" rx="2" />
    <path d="M3 10h18" />
    <path d="M8 14h.01" />
    <path d="M12 14h.01" />
    <path d="M16 14h.01" />
    <path d="M8 18h.01" />
    <path d="M12 18h.01" />
    <path d="M16 18h.01" />
  </Icon>
);

/* lucide user-round */
export const MacUserRound = ({ size }: { size?: number }) => (
  <Icon size={size}>
    <circle cx="12" cy="8" r="5" />
    <path d="M20 21a8 8 0 0 0-16 0" />
  </Icon>
);

/* lucide chevron-left */
export const MacChevronLeft = ({ size }: { size?: number }) => (
  <Icon size={size}>
    <path d="m15 18-6-6 6-6" />
  </Icon>
);

/* lucide arrow-down */
export const MacArrowDown = ({ size }: { size?: number }) => (
  <Icon size={size}>
    <path d="M12 5v14" />
    <path d="m19 12-7 7-7-7" />
  </Icon>
);

/* lucide building-2 */
export const MacBuilding2 = ({ size }: { size?: number }) => (
  <Icon size={size}>
    <path d="M6 22V4a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v18Z" />
    <path d="M6 12H4a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h2" />
    <path d="M18 9h2a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-2" />
    <path d="M10 6h4" />
    <path d="M10 10h4" />
    <path d="M10 14h4" />
    <path d="M10 18h4" />
  </Icon>
);

/* lucide clipboard-list */
export const MacClipboardList = ({ size }: { size?: number }) => (
  <Icon size={size}>
    <rect width="8" height="4" x="8" y="2" rx="1" ry="1" />
    <path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2" />
    <path d="M12 11h4" />
    <path d="M12 16h4" />
    <path d="M8 11h.01" />
    <path d="M8 16h.01" />
  </Icon>
);

/* lucide x */
export const MacX = ({ size }: { size?: number }) => (
  <Icon size={size}>
    <path d="M18 6 6 18" />
    <path d="m6 6 12 12" />
  </Icon>
);

/* lucide refresh-cw */
export const MacRefreshCw = ({ size }: { size?: number }) => (
  <Icon size={size}>
    <path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8" />
    <path d="M21 3v5h-5" />
    <path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16" />
    <path d="M8 16H3v5" />
  </Icon>
);
