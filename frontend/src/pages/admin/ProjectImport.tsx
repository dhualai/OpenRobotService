// 项目管理 - 项目导入：仅保留新建入口，具体项目列表已迁移至「跨项目看板/项目管理」列表页
// 样式参考 macaron projects.auth 页：两栏按钮，USP项目蓝底白字 / 其他项目蓝描边。
import { useNavigate } from 'react-router-dom';

export default function ProjectManage() {
  const navigate = useNavigate();

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
      <button
        type="button"
        className="mac-btn mac-btn--primary"
        onClick={() => navigate('/admin/project-detail/new')}
      >
        USP项目
      </button>
      <button
        type="button"
        className="mac-btn mac-btn--blue-outline"
        onClick={() => navigate('/admin/project-edit')}
      >
        其他项目
      </button>
    </div>
  );
}
