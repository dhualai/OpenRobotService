// 项目管理（二级页面）—— 「项目导入」与「项目授权」两部分，直接摊开展示，不做折叠
// 原「项目管理」页面（项目增删改查）已更名为 ProjectImport，作为本页第一部分；
// 「项目授权」（ProjectAuth）作为本页第二部分，两者原样复用，不改动各自内部逻辑。
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
      <div style={{ padding: '16px 16px 0', fontSize: 15, fontWeight: 600 }}>📁 项目导入</div>
      <ProjectImport />
      <div style={{ padding: '16px 16px 0', fontSize: 15, fontWeight: 600 }}>🔐 项目授权</div>
      <ProjectAuth />
    </div>
  );
}
