"""后台管理模块 - 附加接口用例（permissions/users 扩展/roles 权限/daily-reports/projects 扩展）。

覆盖 task-29 补齐的 admin 附加接口。
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
class TestPermissions:
    """权限管理"""

    @allure.story('权限')
    @allure.title('正常：权限列表')
    @pytest.mark.api
    async def test_permissions_list(self, mock_api_client, mock_auth_header):
        """正常流程：权限列表"""
        await _api(mock_api_client, 'get', '/api/admin/permissions/', headers=mock_auth_header,
                   expected_status=200)

    @allure.story('权限')
    @allure.title('数据校验：缺 code 创建权限')
    @pytest.mark.api
    async def test_permissions_create_missing_code(self, mock_api_client, mock_auth_header):
        """数据校验：创建权限缺 code"""
        await _api(mock_api_client, 'post', '/api/admin/permissions/', headers=mock_auth_header,
                   json={'name': 'x', 'resource_type': 'base', 'action': 'read'},
                   expected_status=400)

    @allure.story('权限')
    @allure.title('异常：权限 code 重复')
    @pytest.mark.api
    async def test_permissions_create_duplicate(self, mock_api_client, mock_auth_header):
        """异常流程：权限 code 重复"""
        await _api(mock_api_client, 'post', '/api/admin/permissions/', headers=mock_auth_header,
                   json={'code': 'task:read', 'name': 'x', 'resource_type': 'base', 'action': 'read'},
                   expected_status=400)

    @allure.story('权限')
    @allure.title('正常：创建权限')
    @pytest.mark.api
    async def test_permissions_create_ok(self, mock_api_client, mock_auth_header):
        """正常流程：创建权限"""
        await _api(mock_api_client, 'post', '/api/admin/permissions/', headers=mock_auth_header,
                   json={'code': 'report:read', 'name': '报表查看', 'resource_type': 'base', 'action': 'read'},
                   expected_status=200, expected_fields={'code': 'report:read'})

    @allure.story('权限')
    @allure.title('正常：权限详情')
    @pytest.mark.api
    async def test_permissions_detail(self, mock_api_client, mock_auth_header):
        """正常流程：权限详情"""
        await _api(mock_api_client, 'get', '/api/admin/permissions/1', headers=mock_auth_header,
                   expected_status=200, expected_fields={'id': 1})

    @allure.story('权限')
    @allure.title('异常：权限不存在')
    @pytest.mark.api
    async def test_permissions_detail_not_found(self, mock_api_client, mock_auth_header):
        """异常流程：权限不存在"""
        await _api(mock_api_client, 'get', '/api/admin/permissions/999', headers=mock_auth_header,
                   expected_status=404)

    @allure.story('权限')
    @allure.title('正常：更新权限')
    @pytest.mark.api
    async def test_permissions_update(self, mock_api_client, mock_auth_header):
        """正常流程：更新权限"""
        await _api(mock_api_client, 'put', '/api/admin/permissions/1', headers=mock_auth_header,
                   json={'name': '任务查看'}, expected_status=200, expected_fields={'id': 1})

    @allure.story('权限')
    @allure.title('正常：删除权限')
    @pytest.mark.api
    async def test_permissions_delete(self, mock_api_client, mock_auth_header):
        """正常流程：删除权限"""
        await _api(mock_api_client, 'delete', '/api/admin/permissions/1', headers=mock_auth_header,
                   expected_status=200)

    @allure.story('权限')
    @allure.title('权限：非 admin 访问权限 403')
    @pytest.mark.api
    async def test_permissions_forbidden(self, mock_api_client, mock_auth_header):
        """权限：非 admin 访问权限 403"""
        headers = await _auth_for_role(mock_api_client, 'engineer')
        await _api(mock_api_client, 'get', '/api/admin/permissions/', headers=headers,
                   expected_status=403)


@allure.feature('后台管理')
class TestUsersExtra:
    """用户扩展接口"""

    @allure.story('用户扩展')
    @allure.title('正常：用户详情')
    @pytest.mark.api
    async def test_user_detail(self, mock_api_client, mock_auth_header):
        """正常流程：用户详情"""
        await _api(mock_api_client, 'get', '/api/admin/users/testadmin/detail',
                   headers=mock_auth_header, expected_status=200,
                   expected_fields={'username': 'testadmin'})

    @allure.story('用户扩展')
    @allure.title('异常：用户详情不存在')
    @pytest.mark.api
    async def test_user_detail_not_found(self, mock_api_client, mock_auth_header):
        """异常流程：用户详情不存在"""
        await _api(mock_api_client, 'get', '/api/admin/users/nobody/detail',
                   headers=mock_auth_header, expected_status=404)

    @allure.story('用户扩展')
    @allure.title('数据校验：分配角色缺 project_id')
    @pytest.mark.api
    async def test_user_roles_missing_project(self, mock_api_client, mock_auth_header):
        """数据校验：分配角色缺 project_id"""
        await _api(mock_api_client, 'post', '/api/admin/users/testadmin/roles',
                   headers=mock_auth_header, json={'role_ids': [1]}, expected_status=400)

    @allure.story('用户扩展')
    @allure.title('数据校验：分配角色空 role_ids')
    @pytest.mark.api
    async def test_user_roles_empty_roles(self, mock_api_client, mock_auth_header):
        """数据校验：分配角色空 role_ids"""
        await _api(mock_api_client, 'post', '/api/admin/users/testadmin/roles',
                   headers=mock_auth_header, json={'project_id': 1, 'role_ids': []},
                   expected_status=400)

    @allure.story('用户扩展')
    @allure.title('正常：分配角色')
    @pytest.mark.api
    async def test_user_roles_assign(self, mock_api_client, mock_auth_header):
        """正常流程：分配角色"""
        await _api(mock_api_client, 'post', '/api/admin/users/testadmin/roles',
                   headers=mock_auth_header, json={'project_id': 1, 'role_ids': [1, 2]},
                   expected_status=200)

    @allure.story('用户扩展')
    @allure.title('异常：分配角色用户不存在')
    @pytest.mark.api
    async def test_user_roles_user_not_found(self, mock_api_client, mock_auth_header):
        """异常流程：用户不存在"""
        await _api(mock_api_client, 'post', '/api/admin/users/nobody/roles',
                   headers=mock_auth_header, json={'project_id': 1, 'role_ids': [1]},
                   expected_status=404)

    @allure.story('用户扩展')
    @allure.title('正常：移除角色')
    @pytest.mark.api
    async def test_user_roles_remove(self, mock_api_client, mock_auth_header):
        """正常流程：移除角色"""
        await _api(mock_api_client, 'post', '/api/admin/users/testadmin/roles/remove',
                   headers=mock_auth_header, json={'project_id': 1, 'role_ids': [1]},
                   expected_status=200)

    @allure.story('用户扩展')
    @allure.title('数据校验：上报人缺 project_id')
    @pytest.mark.api
    async def test_user_reporters_missing_project(self, mock_api_client, mock_auth_header):
        """数据校验：上报人缺 project_id"""
        await _api(mock_api_client, 'get', '/api/admin/users/testadmin/reporters',
                   headers=mock_auth_header, expected_status=400)

    @allure.story('用户扩展')
    @allure.title('正常：上报人列表')
    @pytest.mark.api
    async def test_user_reporters_ok(self, mock_api_client, mock_auth_header):
        """正常流程：上报人列表"""
        await _api(mock_api_client, 'get', '/api/admin/users/testadmin/reporters?project_id=1',
                   headers=mock_auth_header, expected_status=200)

    @allure.story('用户扩展')
    @allure.title('数据校验：更新 USP 信息缺 name')
    @pytest.mark.api
    async def test_user_uspinfo_missing_name(self, mock_api_client, mock_auth_header):
        """数据校验：更新 USP 信息缺 name"""
        await _api(mock_api_client, 'post', '/api/admin/users/testadmin/uspinfo',
                   headers=mock_auth_header, json={}, expected_status=400)

    @allure.story('用户扩展')
    @allure.title('正常：更新 USP 信息')
    @pytest.mark.api
    async def test_user_uspinfo_ok(self, mock_api_client, mock_auth_header):
        """正常流程：更新 USP 信息"""
        await _api(mock_api_client, 'post', '/api/admin/users/testadmin/uspinfo',
                   headers=mock_auth_header, json={'name': '张三'}, expected_status=200,
                   expected_fields={'username': 'testadmin'})

    @allure.story('用户扩展')
    @allure.title('数据校验：USP 用户名缺 name')
    @pytest.mark.api
    async def test_usp_username_missing_name(self, mock_api_client, mock_auth_header):
        """数据校验：USP 用户名缺 name"""
        await _api(mock_api_client, 'get', '/api/admin/users/usp-username',
                   headers=mock_auth_header, expected_status=400)

    @allure.story('用户扩展')
    @allure.title('正常：USP 用户名查询')
    @pytest.mark.api
    async def test_usp_username_ok(self, mock_api_client, mock_auth_header):
        """正常流程：USP 用户名查询"""
        await _api(mock_api_client, 'get', '/api/admin/users/usp-username?name=张三',
                   headers=mock_auth_header, expected_status=200)

    @allure.story('用户扩展')
    @allure.title('正常：用户选项')
    @pytest.mark.api
    async def test_user_options(self, mock_api_client, mock_auth_header):
        """正常流程：用户选项"""
        await _api(mock_api_client, 'get', '/api/admin/users/options',
                   headers=mock_auth_header, expected_status=200)


@allure.feature('后台管理')
class TestRolesExtra:
    """角色权限"""

    @allure.story('角色权限')
    @allure.title('正常：角色权限列表')
    @pytest.mark.api
    async def test_role_permissions_list(self, mock_api_client, mock_auth_header):
        """正常流程：角色权限列表"""
        await _api(mock_api_client, 'get', '/api/admin/roles/1/permissions',
                   headers=mock_auth_header, expected_status=200)

    @allure.story('角色权限')
    @allure.title('异常：角色权限 404')
    @pytest.mark.api
    async def test_role_permissions_not_found(self, mock_api_client, mock_auth_header):
        """异常流程：角色不存在"""
        await _api(mock_api_client, 'get', '/api/admin/roles/999/permissions',
                   headers=mock_auth_header, expected_status=404)

    @allure.story('角色权限')
    @allure.title('数据校验：授予权限空列表')
    @pytest.mark.api
    async def test_role_permissions_empty(self, mock_api_client, mock_auth_header):
        """数据校验：授予权限空列表"""
        await _api(mock_api_client, 'post', '/api/admin/roles/1/permissions',
                   headers=mock_auth_header, json={'permission_ids': []}, expected_status=400)

    @allure.story('角色权限')
    @allure.title('异常：重复授予权限')
    @pytest.mark.api
    async def test_role_permissions_duplicate(self, mock_api_client, mock_auth_header):
        """异常流程：重复授予权限"""
        await _api(mock_api_client, 'post', '/api/admin/roles/1/permissions',
                   headers=mock_auth_header, json={'permission_ids': ['admin']},
                   expected_status=400)

    @allure.story('角色权限')
    @allure.title('正常：授予权限')
    @pytest.mark.api
    async def test_role_permissions_grant(self, mock_api_client, mock_auth_header):
        """正常流程：授予权限"""
        await _api(mock_api_client, 'post', '/api/admin/roles/2/permissions',
                   headers=mock_auth_header, json={'permission_ids': [1]}, expected_status=200)

    @allure.story('角色权限')
    @allure.title('正常：角色全部权限')
    @pytest.mark.api
    async def test_role_all_permissions(self, mock_api_client, mock_auth_header):
        """正常流程：角色全部权限"""
        await _api(mock_api_client, 'get', '/api/admin/roles/1/all-permissions',
                   headers=mock_auth_header, expected_status=200)

    @allure.story('角色权限')
    @allure.title('正常：角色自动分类')
    @pytest.mark.api
    async def test_role_auto_classify(self, mock_api_client, mock_auth_header):
        """正常流程：角色自动分类"""
        await _api(mock_api_client, 'post', '/api/admin/roles/auto-classify',
                   headers=mock_auth_header, expected_status=200)


@allure.feature('后台管理')
class TestDailyReports:
    """日报"""

    @allure.story('日报')
    @allure.title('正常：日报列表')
    @pytest.mark.api
    async def test_daily_reports_list(self, mock_api_client, mock_auth_header):
        """正常流程：日报列表"""
        await _api(mock_api_client, 'get', '/api/admin/daily-reports/',
                   headers=mock_auth_header, expected_status=200)

    @allure.story('日报')
    @allure.title('正常：日报详情')
    @pytest.mark.api
    async def test_daily_report_detail(self, mock_api_client, mock_auth_header):
        """正常流程：日报详情"""
        await _api(mock_api_client, 'get', '/api/admin/daily-reports/1',
                   headers=mock_auth_header, expected_status=200, expected_fields={'id': 1})

    @allure.story('日报')
    @allure.title('异常：日报不存在')
    @pytest.mark.api
    async def test_daily_report_not_found(self, mock_api_client, mock_auth_header):
        """异常流程：日报不存在"""
        await _api(mock_api_client, 'get', '/api/admin/daily-reports/999',
                   headers=mock_auth_header, expected_status=404)

    @allure.story('日报')
    @allure.title('正常：更新日报')
    @pytest.mark.api
    async def test_daily_report_update(self, mock_api_client, mock_auth_header):
        """正常流程：更新日报"""
        await _api(mock_api_client, 'put', '/api/admin/daily-reports/1',
                   headers=mock_auth_header, json={'report_content': 'updated'},
                   expected_status=200, expected_fields={'id': 1})

    @allure.story('日报')
    @allure.title('正常：删除日报')
    @pytest.mark.api
    async def test_daily_report_delete(self, mock_api_client, mock_auth_header):
        """正常流程：删除日报"""
        await _api(mock_api_client, 'delete', '/api/admin/daily-reports/1',
                   headers=mock_auth_header, expected_status=200)

    @allure.story('日报')
    @allure.title('正常：按日期查询日报')
    @pytest.mark.api
    async def test_daily_report_by_date(self, mock_api_client, mock_auth_header):
        """正常流程：按日期查询日报"""
        await _api(mock_api_client, 'get', '/api/admin/daily-reports/by-date/P001/2026-08-01',
                   headers=mock_auth_header, expected_status=200)

    @allure.story('日报')
    @allure.title('正常：日报搜索')
    @pytest.mark.api
    async def test_daily_report_search(self, mock_api_client, mock_auth_header):
        """正常流程：日报搜索"""
        await _api(mock_api_client, 'get', '/api/admin/daily-reports/search/日报',
                   headers=mock_auth_header, expected_status=200)


@allure.feature('后台管理')
class TestProjectsExtra:
    """项目扩展接口"""

    @allure.story('项目扩展')
    @allure.title('正常：我的项目')
    @pytest.mark.api
    async def test_projects_me(self, mock_api_client, mock_auth_header):
        """正常流程：我的项目"""
        await _api(mock_api_client, 'get', '/api/admin/projects/me',
                   headers=mock_auth_header, expected_status=200)

    @allure.story('项目扩展')
    @allure.title('数据校验：申请许可缺字段')
    @pytest.mark.api
    async def test_projects_license_missing_fields(self, mock_api_client, mock_auth_header):
        """数据校验：申请许可缺字段"""
        await _api(mock_api_client, 'post', '/api/admin/projects/licenses',
                   headers=mock_auth_header, json={'project_code': 'P001'},
                   expected_status=400)

    @allure.story('项目扩展')
    @allure.title('正常：申请项目许可')
    @pytest.mark.api
    async def test_projects_license_ok(self, mock_api_client, mock_auth_header):
        """正常流程：申请项目许可"""
        await _api(mock_api_client, 'post', '/api/admin/projects/licenses',
                   headers=mock_auth_header,
                   json={'project_code': 'P001', 'apply_time': '2026-08-01',
                         'expire_time': '2026-12-31', 'license_code': 'LIC-001',
                         'applicant': 'testadmin', 'applicant_id': 1},
                   expected_status=200)

    @allure.story('项目扩展')
    @allure.title('数据校验：许可 type 非法')
    @pytest.mark.api
    async def test_projects_license_invalid_type(self, mock_api_client, mock_auth_header):
        """数据校验：许可 type 非法"""
        await _api(mock_api_client, 'get', '/api/admin/projects/licenses/P001?type=bad',
                   headers=mock_auth_header, expected_status=400)

    @allure.story('项目扩展')
    @allure.title('正常：项目许可查询')
    @pytest.mark.api
    async def test_projects_license_query(self, mock_api_client, mock_auth_header):
        """正常流程：项目许可查询"""
        await _api(mock_api_client, 'get', '/api/admin/projects/licenses/P001',
                   headers=mock_auth_header, expected_status=200)


@allure.feature('后台管理')
class TestAuthMatrix:
    """认证矩阵"""

    @allure.story('认证')
    @allure.title('权限：非 admin 访问角色 403')
    @pytest.mark.api
    async def test_roles_forbidden(self, mock_api_client, mock_auth_header):
        """权限：非 admin 访问角色 403"""
        headers = await _auth_for_role(mock_api_client, 'engineer')
        await _api(mock_api_client, 'get', '/api/admin/roles/', headers=headers,
                   expected_status=403)

    @allure.story('认证')
    @allure.title('权限：无 token 访问日报 401')
    @pytest.mark.api
    async def test_daily_reports_unauthorized(self, mock_api_client):
        """权限：无 token 访问日报 401"""
        await _api(mock_api_client, 'get', '/api/admin/daily-reports/', expected_status=401)

    @allure.story('认证')
    @allure.title('权限：无 token 访问权限 401')
    @pytest.mark.api
    async def test_permissions_unauthorized(self, mock_api_client):
        """权限：无 token 访问权限 401"""
        await _api(mock_api_client, 'get', '/api/admin/permissions/', expected_status=401)
