// 项目管理（二级页面）—— 「项目导入」与「项目授权」两部分，以下拉折叠面板形式呈现
// 原「项目管理」页面（项目增删改查）已更名为 ProjectImport，作为本页第一个面板；
// 「项目授权」（ProjectAuth）作为本页第二个面板，两者原样复用，不改动各自内部逻辑。
import { useEffect, useRef } from 'react';
import { Collapse, CollapsePanel } from 'tdesign-mobile-react';
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
      <Collapse defaultValue={['import']}>
        <CollapsePanel value="import" header="📁 项目导入">
          <ProjectImport />
        </CollapsePanel>
        <CollapsePanel value="auth" header="🔐 项目授权">
          <ProjectAuth />
        </CollapsePanel>
      </Collapse>
    </div>
  );
}
