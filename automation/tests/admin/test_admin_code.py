"""后台管理模块 - 代码驱动用例（自由函数模式）。

与 test_admin.py（Excel 数据驱动）并行，代码驱动迁移版。
"""

import allure
import pytest
from automation.src.assertions import assert_dict_contains_subset, assert_status_code
from automation.src.assertions.report import flush_assert_attachment

_ROLE_CREDENTIALS = {'customer': ('customer', 'cust123'), 'engineer': ('engineer', 'eng123')}


async def _auth_for_role(client, role: str) -> dict:
    username, password = _ROLE_CREDENTIALS[role]
    r = await client.post('/api/auth/login', json={'username': username, 'password': password})
    return {'Authorization': f"Bearer {r.json()['access_token']}"}


async def _api(client, method: str, path: str, step: str = '', headers=None,
               expected_status: int | None = None, expected_fields: dict | None = None, **kwargs):
    """Send a request wrapped in an Allure step block; assertions run inside the step."""
    with allure.step(step or f'{method.upper()} {path}'):
        r = await client.request(method, path, headers=headers, **kwargs)
        if expected_status is not None:
            assert_status_code(r, expected_status)
        if expected_fields:
            assert_dict_contains_subset(r.json(), expected_fields)
        flush_assert_attachment()
        return r


@allure.feature('后台管理')
class TestTickets:
    """工单总览"""

    @allure.story('工单总览')
    @allure.title('正常：工单列表')
    @pytest.mark.api
    async def test_tickets_list(self, mock_api_client, mock_auth_header):
        """正常流程：工单列表"""
        await _api(mock_api_client, 'get', '/api/admin/tickets', headers=mock_auth_header,
                   expected_status=200)

    @allure.story('工单总览')
    @allure.title('正常：工单统计')
    @pytest.mark.api
    async def test_tickets_stats(self, mock_api_client, mock_auth_header):
        """正常流程：工单统计"""
        await _api(mock_api_client, 'get', '/api/admin/tickets/stats', headers=mock_auth_header,
                   expected_status=200)

    @allure.story('工单总览')
    @allure.title('正常：工单统计（重复）')
    @pytest.mark.api
    async def test_tickets_stats_again(self, mock_api_client, mock_auth_header):
        """正常流程：工单统计"""
        await _api(mock_api_client, 'get', '/api/admin/tickets/stats', headers=mock_auth_header,
                   expected_status=200)


@allure.feature('后台管理')
class TestProjects:
    """项目管理"""

    @allure.story('项目')
    @allure.title('正常：项目列表')
    @pytest.mark.api
    async def test_projects_list(self, mock_api_client, mock_auth_header):
        """正常流程：项目列表"""
        await _api(mock_api_client, 'get', '/api/admin/projects', headers=mock_auth_header,
                   expected_status=200)

    @allure.story('项目')
    @allure.title('正常：创建项目')
    @pytest.mark.api
    async def test_projects_create(self, mock_api_client, mock_auth_header):
        """正常流程：创建项目"""
        await _api(mock_api_client, 'post', '/api/admin/projects', headers=mock_auth_header,
                   json={'name': 'Test Project', 'project_code': 'P002'}, expected_status=200,
                   expected_fields={'name': 'Test Project'})

    @allure.story('项目')
    @allure.title('异常：项目详情不存在')
    @pytest.mark.api
    async def test_projects_detail_not_found(self, mock_api_client, mock_auth_header):
        """异常流程：项目详情不存在"""
        await _api(mock_api_client, 'get', '/api/admin/projects/999', headers=mock_auth_header,
                   expected_status=404)

    @allure.story('项目')
    @allure.title('正常：更新项目')
    @pytest.mark.api
    async def test_projects_update(self, mock_api_client, mock_auth_header):
        """正常流程：更新项目"""
        await _api(mock_api_client, 'put', '/api/admin/projects/1', headers=mock_auth_header,
                   json={'name': 'Updated Project'}, expected_status=200,
                   expected_fields={'id': 1, 'name': 'Updated Project'})

    @allure.story('项目')
    @allure.title('异常：项目不存在')
    @pytest.mark.api
    async def test_projects_update_not_found(self, mock_api_client, mock_auth_header):
        """异常流程：项目不存在"""
        await _api(mock_api_client, 'put', '/api/admin/projects/999', headers=mock_auth_header,
                   json={'name': 'x'}, expected_status=404)

    @allure.story('项目')
    @allure.title('正常：删除项目')
    @pytest.mark.api
    async def test_projects_delete(self, mock_api_client, mock_auth_header):
        """正常流程：删除项目"""
        await _api(mock_api_client, 'delete', '/api/admin/projects/1', headers=mock_auth_header,
                   expected_status=204)

    @allure.story('项目')
    @allure.title('异常：删除项目不存在')
    @pytest.mark.api
    async def test_projects_delete_not_found(self, mock_api_client, mock_auth_header):
        """异常流程：项目不存在"""
        await _api(mock_api_client, 'delete', '/api/admin/projects/999', headers=mock_auth_header,
                   expected_status=404)


@allure.feature('后台管理')
class TestRisks:
    """风险管理"""

    @allure.story('风险')
    @allure.title('正常：风险列表')
    @pytest.mark.api
    async def test_risks_list(self, mock_api_client, mock_auth_header):
        """正常流程：风险列表"""
        await _api(mock_api_client, 'get', '/api/admin/projects/risks', headers=mock_auth_header,
                   expected_status=200)

    @allure.story('风险')
    @allure.title('正常：创建风险')
    @pytest.mark.api
    async def test_risks_create(self, mock_api_client, mock_auth_header):
        """正常流程：创建风险"""
        await _api(mock_api_client, 'post', '/api/admin/projects/risks', headers=mock_auth_header,
                   json={'name': 'Risk A', 'level': 'high'}, expected_status=200,
                   expected_fields={'name': 'Risk A'})

    @allure.story('风险')
    @allure.title('数据校验：缺 name')
    @pytest.mark.api
    async def test_risks_create_missing_name(self, mock_api_client, mock_auth_header):
        """数据校验：缺 name"""
        await _api(mock_api_client, 'post', '/api/admin/projects/risks', headers=mock_auth_header,
                   expected_status=422)

    @allure.story('风险')
    @allure.title('正常：更新风险')
    @pytest.mark.api
    async def test_risks_update(self, mock_api_client, mock_auth_header):
        """正常流程：更新风险"""
        await _api(mock_api_client, 'put', '/api/admin/projects/risks/R1', headers=mock_auth_header,
                   json={'level': 'low'}, expected_status=200,
                   expected_fields={'risk_code': 'R1', 'level': 'low'})

    @allure.story('风险')
    @allure.title('异常：风险不存在')
    @pytest.mark.api
    async def test_risks_update_not_found(self, mock_api_client, mock_auth_header):
        """异常流程：风险不存在"""
        await _api(mock_api_client, 'put', '/api/admin/projects/risks/R999', headers=mock_auth_header,
                   json={'level': 'low'}, expected_status=404)

    @allure.story('风险')
    @allure.title('正常：删除风险')
    @pytest.mark.api
    async def test_risks_delete(self, mock_api_client, mock_auth_header):
        """正常流程：删除风险"""
        await _api(mock_api_client, 'delete', '/api/admin/projects/risks/R1', headers=mock_auth_header,
                   expected_status=204)

    @allure.story('风险')
    @allure.title('异常：删除风险不存在')
    @pytest.mark.api
    async def test_risks_delete_not_found(self, mock_api_client, mock_auth_header):
        """异常流程：风险不存在"""
        await _api(mock_api_client, 'delete', '/api/admin/projects/risks/R999', headers=mock_auth_header,
                   expected_status=404)


@allure.feature('后台管理')
class TestDashboard:
    """看板"""

    @allure.story('看板')
    @allure.title('正常：看板汇总')
    @pytest.mark.api
    async def test_dashboard_summary(self, mock_api_client, mock_auth_header):
        """正常流程：看板汇总"""
        await _api(mock_api_client, 'get', '/api/admin/dashboard/tickets/summary', headers=mock_auth_header,
                   expected_status=200)


@allure.feature('后台管理')
class TestUsers:
    """用户管理"""

    @allure.story('用户')
    @allure.title('正常：用户列表')
    @pytest.mark.api
    async def test_users_list(self, mock_api_client, mock_auth_header):
        """正常流程：用户列表"""
        await _api(mock_api_client, 'get', '/api/admin/users/', headers=mock_auth_header,
                   expected_status=200)

    @allure.story('用户')
    @allure.title('Redis：用户列表缓存')
    @pytest.mark.api
    async def test_users_list_cache(self, mock_api_client, mock_auth_header):
        """Redis：用户列表缓存"""
        await _api(mock_api_client, 'get', '/api/admin/users', headers=mock_auth_header,
                   expected_status=200)

    @allure.story('用户')
    @allure.title('数据库：删除用户级联事务')
    @pytest.mark.api
    async def test_users_delete_cascade(self, mock_api_client, mock_auth_header):
        """数据库：删除用户级联事务"""
        await _api(mock_api_client, 'delete', '/api/admin/users/testadmin', headers=mock_auth_header,
                   expected_status=204)

    @allure.story('用户')
    @allure.title('权限：非 admin 访问 403')
    @pytest.mark.api
    async def test_users_forbidden(self, mock_api_client, mock_auth_header):
        """权限：非 admin 访问 403"""
        headers = await _auth_for_role(mock_api_client, 'engineer')
        await _api(mock_api_client, 'get', '/api/admin/users', headers=headers,
                   expected_status=403)

    @allure.story('用户')
    @allure.title('权限：无 token 访问 401')
    @pytest.mark.api
    async def test_users_unauthorized(self, mock_api_client):
        """权限：无 token 访问 401"""
        await _api(mock_api_client, 'get', '/api/admin/users', expected_status=401)

    @allure.story('用户')
    @allure.title('正常：创建用户')
    @pytest.mark.api
    async def test_users_create(self, mock_api_client, mock_auth_header):
        """正常流程：创建用户"""
        await _api(mock_api_client, 'post', '/api/admin/users', headers=mock_auth_header,
                   json={'username': 'newuser', 'password': 'pass', 'name': 'New User', 'role': 'engineer'},
                   expected_status=201, expected_fields={'username': 'newuser'})

    @allure.story('用户')
    @allure.title('数据校验：缺 username')
    @pytest.mark.api
    async def test_users_create_missing_username(self, mock_api_client, mock_auth_header):
        """数据校验：缺 username"""
        await _api(mock_api_client, 'post', '/api/admin/users', headers=mock_auth_header,
                   expected_status=422)

    @allure.story('用户')
    @allure.title('异常：用户名已存在')
    @pytest.mark.api
    async def test_users_create_duplicate(self, mock_api_client, mock_auth_header):
        """异常流程：用户名已存在"""
        await _api(mock_api_client, 'post', '/api/admin/users', headers=mock_auth_header,
                   json={'username': 'testadmin'}, expected_status=409)

    @allure.story('用户')
    @allure.title('正常：更新用户')
    @pytest.mark.api
    async def test_users_update(self, mock_api_client, mock_auth_header):
        """正常流程：更新用户"""
        await _api(mock_api_client, 'put', '/api/admin/users/testadmin', headers=mock_auth_header,
                   json={'name': 'Admin Renamed'}, expected_status=200,
                   expected_fields={'username': 'testadmin'})

    @allure.story('用户')
    @allure.title('异常：用户不存在')
    @pytest.mark.api
    async def test_users_update_not_found(self, mock_api_client, mock_auth_header):
        """异常流程：用户不存在"""
        await _api(mock_api_client, 'put', '/api/admin/users/nobody', headers=mock_auth_header,
                   json={'name': 'x'}, expected_status=404)

    @allure.story('用户')
    @allure.title('权限：未认证创建用户')
    @pytest.mark.api
    async def test_users_create_unauthorized(self, mock_api_client):
        """权限：未认证创建用户"""
        await _api(mock_api_client, 'post', '/api/admin/users', json={'username': 'x'},
                   expected_status=401)


@allure.feature('后台管理')
class TestRoles:
    """角色管理"""

    @allure.story('角色')
    @allure.title('正常：角色列表')
    @pytest.mark.api
    async def test_roles_list(self, mock_api_client, mock_auth_header):
        """正常流程：角色列表"""
        await _api(mock_api_client, 'get', '/api/admin/roles/', headers=mock_auth_header,
                   expected_status=200)

    @allure.story('角色')
    @allure.title('Redis：角色缓存失效')
    @pytest.mark.api
    async def test_roles_list_cache(self, mock_api_client, mock_auth_header):
        """Redis：角色缓存失效"""
        await _api(mock_api_client, 'get', '/api/admin/roles', headers=mock_auth_header,
                   expected_status=200)

    @allure.story('角色')
    @allure.title('正常：删除角色')
    @pytest.mark.api
    async def test_roles_delete(self, mock_api_client, mock_auth_header):
        """正常流程：删除自定义角色"""
        r = await _api(mock_api_client, 'post', '/api/admin/roles', headers=mock_auth_header,
                       json={'name': 'temp-role'}, expected_status=201)
        rid = r.json()['id']
        await _api(mock_api_client, 'delete', f'/api/admin/roles/{rid}', headers=mock_auth_header,
                   expected_status=204)

    @allure.story('角色')
    @allure.title('正常：创建角色')
    @pytest.mark.api
    async def test_roles_create(self, mock_api_client, mock_auth_header):
        """正常流程：创建角色"""
        await _api(mock_api_client, 'post', '/api/admin/roles', headers=mock_auth_header,
                   json={'name': 'viewer'}, expected_status=201,
                   expected_fields={'name': 'viewer'})

    @allure.story('角色')
    @allure.title('数据校验：缺 name')
    @pytest.mark.api
    async def test_roles_create_missing_name(self, mock_api_client, mock_auth_header):
        """数据校验：缺 name"""
        await _api(mock_api_client, 'post', '/api/admin/roles', headers=mock_auth_header,
                   expected_status=422)

    @allure.story('角色')
    @allure.title('异常：角色已存在')
    @pytest.mark.api
    async def test_roles_create_duplicate(self, mock_api_client, mock_auth_header):
        """异常流程：角色已存在"""
        await _api(mock_api_client, 'post', '/api/admin/roles', headers=mock_auth_header,
                   json={'name': 'admin'}, expected_status=409)

    @allure.story('角色')
    @allure.title('正常：更新角色')
    @pytest.mark.api
    async def test_roles_update(self, mock_api_client, mock_auth_header):
        """正常流程：更新角色"""
        await _api(mock_api_client, 'put', '/api/admin/roles/1', headers=mock_auth_header,
                   json={'name': 'superadmin'}, expected_status=200,
                   expected_fields={'name': 'superadmin'})

    @allure.story('角色')
    @allure.title('异常：角色不存在')
    @pytest.mark.api
    async def test_roles_update_not_found(self, mock_api_client, mock_auth_header):
        """异常流程：角色不存在"""
        await _api(mock_api_client, 'put', '/api/admin/roles/999', headers=mock_auth_header,
                   json={'name': 'x'}, expected_status=404)


@allure.feature('后台管理')
class TestDailyReports:
    """日报"""

    @allure.story('日报')
    @allure.title('正常：生成日报')
    @pytest.mark.api
    async def test_daily_report(self, mock_api_client, mock_auth_header):
        """正常流程：生成日报"""
        await _api(mock_api_client, 'post', '/api/admin/daily-reports', headers=mock_auth_header,
                   json={'type': 'daily'}, expected_status=200,
                   expected_fields={'status': 'generated'})

    @allure.story('日报')
    @allure.title('正常：生成周报')
    @pytest.mark.api
    async def test_weekly_report(self, mock_api_client, mock_auth_header):
        """正常流程：生成周报"""
        await _api(mock_api_client, 'post', '/api/admin/daily-reports', headers=mock_auth_header,
                   json={'type': 'weekly'}, expected_status=200,
                   expected_fields={'type': 'weekly'})

    @allure.story('日报')
    @allure.title('正常：生成日报（重复）')
    @pytest.mark.api
    async def test_daily_report_again(self, mock_api_client, mock_auth_header):
        """正常流程：生成日报"""
        await _api(mock_api_client, 'post', '/api/admin/daily-reports', headers=mock_auth_header,
                   json={'type': 'daily'}, expected_status=200,
                   expected_fields={'status': 'generated'})


@allure.feature('后台管理')
class TestExport:
    """数据导出"""

    @allure.story('导出')
    @allure.title('正常：导出数据')
    @pytest.mark.api
    async def test_export(self, mock_api_client, mock_auth_header):
        """正常流程：导出数据"""
        await _api(mock_api_client, 'post', '/api/admin/export/project/P001', headers=mock_auth_header,
                   json={'format': 'xlsx'}, expected_status=200,
                   expected_fields={'status': 'processing'})

    @allure.story('导出')
    @allure.title('正常：导出报表')
    @pytest.mark.api
    async def test_export_report(self, mock_api_client, mock_auth_header):
        """正常流程：导出报表"""
        await _api(mock_api_client, 'post', '/api/admin/export/project/P001', headers=mock_auth_header,
                   json={'format': 'xlsx'}, expected_status=200,
                   expected_fields={'status': 'processing'})


@allure.feature('后台管理')
class TestResources:
    """资源管理"""

    @allure.story('资源')
    @allure.title('正常：资源列表')
    @pytest.mark.api
    async def test_resources_list(self, mock_api_client, mock_auth_header):
        """正常流程：资源列表"""
        await _api(mock_api_client, 'get', '/api/admin/resource-manager/resources', headers=mock_auth_header,
                   expected_status=200)

    @allure.story('资源')
    @allure.title('正常：创建资源')
    @pytest.mark.api
    async def test_resources_create(self, mock_api_client, mock_auth_header):
        """正常流程：创建资源"""
        await _api(mock_api_client, 'post', '/api/admin/resource-manager/resources', headers=mock_auth_header,
                   json={'name': 'file.docx', 'type': 'file'}, expected_status=200,
                   expected_fields={'name': 'file.docx'})

    @allure.story('资源')
    @allure.title('正常：资源详情')
    @pytest.mark.api
    async def test_resources_detail(self, mock_api_client, mock_auth_header):
        """正常流程：资源详情"""
        await _api(mock_api_client, 'get', '/api/admin/resource-manager/resources/1', headers=mock_auth_header,
                   expected_status=200, expected_fields={'id': 1})
