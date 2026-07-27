// 项目管理 - 项目导入：仅保留新建入口，具体项目列表已迁移至「跨项目看板/项目管理」列表页
import { Button } from 'tdesign-mobile-react';
import { useNavigate } from 'react-router-dom';

export default function ProjectManage() {
  const navigate = useNavigate();

  return (
    <div style={{ padding: 16 }}>
      <div style={{ display: 'flex', gap: 10 }}>
        <Button theme="primary" block onClick={() => navigate('/admin/project-detail/new')}>
          USP项目
        </Button>
        <Button theme="primary" variant="outline" block onClick={() => navigate('/admin/project-edit')}>
          其他项目
        </Button>
      </div>
    </div>
  );
}
