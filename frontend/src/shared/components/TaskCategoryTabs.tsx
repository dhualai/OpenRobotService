import { useLocation, useNavigate } from 'react-router-dom';
import { useTaskCounts } from './TaskCountsContext';
import './task-category-tabs.css';

export default function TaskCategoryTabs() {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const { ticketCount, learningCount } = useTaskCounts();
  const learning = pathname.startsWith('/tasks/learning') && !pathname.includes('/reviews/');
  const tabs = [
    { key: 'tickets', label: '工单任务', path: '/tasks', active: !learning, count: ticketCount },
    { key: 'learning', label: '学习任务', path: '/tasks/learning', active: learning, count: learningCount },
  ];
  return <div className="tasks-top-tabs" role="tablist" aria-label="系统任务切换">
    {tabs.map((tab) => <button key={tab.key} role="tab" aria-selected={tab.active}
      className={`tasks-top-tabs__item ${tab.active ? 'is-active' : ''}`} onClick={() => navigate(tab.path)}>
      <span className="task-category-label">{tab.label}
        {tab.count != null && tab.count > 0 && <span className="task-category-badge"
          data-testid={`task-category-count-${tab.key}`} aria-label={`${tab.label}数量${tab.count}`}>
          {tab.count > 999 ? '999+' : tab.count}
        </span>}
      </span>
    </button>)}
  </div>;
}
