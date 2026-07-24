// 项目管理（二级页面）—— 上下并列展示「项目导入」与「项目授权」两部分
// 原「项目管理」页面（项目增删改查）已更名为 ProjectImport，作为本页上半部分；
// 「项目授权」（ProjectAuth）作为本页下半部分，两者原样复用，不改动各自内部逻辑。
// 注意：ProjectImport / ProjectAuth 各自内部已带 padding:16 的根容器，
// 本页外层不再重复加 padding，避免双重内边距。
import { useEffect, useRef } from 'react';
import ProjectImport from './ProjectImport';
import ProjectAuth from './ProjectAuth';

export default function ProjectManage() {
  const rootRef = useRef<HTMLDivElement>(null);

  // 进入页面时滚动到最上端（AdminLayout 的滚动容器是外层 overflow:auto 的 div，非 window）
  useEffect(() => {
    let el: HTMLElement | null = rootRef.current;
    while (el) {
      const style = window.getComputedStyle(el);
      if (style.overflowY === 'auto' || style.overflowY === 'scroll') {
        el.scrollTop = 0;
        break;
      }
      el = el.parentElement;
    }
    window.scrollTo(0, 0);
  }, []);

  return (
    <div ref={rootRef}>
      {/* ============ 上：项目导入 ============ */}
      <SectionHeader emoji="📁" title="项目导入" />
      <ProjectImport />

      {/* ============ 下：项目授权 ============ */}
      <SectionHeader emoji="🔐" title="项目授权" />
      <ProjectAuth />
    </div>
  );
}

function SectionHeader({ emoji, title }: { emoji: string; title: string }) {
  return (
    <div className="project-manage-section-title">
      <span>{emoji} {title}</span>
    </div>
  );
}
