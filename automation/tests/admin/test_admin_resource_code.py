"""后台管理模块 - 资源库用例（resource-manager resources/folders/minio）。"""

import allure
import pytest
from automation.src.assertions import assert_dict_contains_subset, assert_status_code
from automation.src.assertions.report import flush_assert_attachment


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
class TestResourceCrud:
    """资源 CRUD"""

    @allure.story('资源')
    @allure.title('正常：资源列表')
    @pytest.mark.api
    async def test_resources_list(self, mock_api_client, mock_auth_header):
        """正常流程：资源列表"""
        await _api(mock_api_client, 'get', '/api/admin/resource-manager/resources/',
                   headers=mock_auth_header, expected_status=200)

    @allure.story('资源')
    @allure.title('正常：资源详情')
    @pytest.mark.api
    async def test_resources_detail(self, mock_api_client, mock_auth_header):
        """正常流程：资源详情"""
        await _api(mock_api_client, 'get', '/api/admin/resource-manager/resources/1',
                   headers=mock_auth_header, expected_status=200, expected_fields={'id': 1})

    @allure.story('资源')
    @allure.title('正常：更新资源')
    @pytest.mark.api
    async def test_resources_update(self, mock_api_client, mock_auth_header):
        """正常流程：更新资源"""
        await _api(mock_api_client, 'put', '/api/admin/resource-manager/resources/1',
                   headers=mock_auth_header, json={'name': 'renamed'}, expected_status=200)

    @allure.story('资源')
    @allure.title('正常：删除资源')
    @pytest.mark.api
    async def test_resources_delete(self, mock_api_client, mock_auth_header):
        """正常流程：删除资源"""
        await _api(mock_api_client, 'delete', '/api/admin/resource-manager/resources/1',
                   headers=mock_auth_header, expected_status=200)


@allure.feature('后台管理')
class TestResourceQuery:
    """资源查询维度"""

    @allure.story('资源查询')
    @allure.title('正常：最近资源')
    @pytest.mark.api
    async def test_resources_recent(self, mock_api_client, mock_auth_header):
        """正常流程：最近上传资源"""
        await _api(mock_api_client, 'get', '/api/admin/resource-manager/resources/recent',
                   headers=mock_auth_header, expected_status=200)

    @allure.story('资源查询')
    @allure.title('正常：资源统计汇总')
    @pytest.mark.api
    async def test_resources_stats_summary(self, mock_api_client, mock_auth_header):
        """正常流程：资源统计汇总"""
        await _api(mock_api_client, 'get', '/api/admin/resource-manager/resources/stats/summary',
                   headers=mock_auth_header, expected_status=200)

    @allure.story('资源查询')
    @allure.title('正常：资源日统计')
    @pytest.mark.api
    async def test_resources_stats_daily(self, mock_api_client, mock_auth_header):
        """正常流程：资源日统计"""
        await _api(mock_api_client, 'get', '/api/admin/resource-manager/resources/stats/daily',
                   headers=mock_auth_header, expected_status=200)

    @allure.story('资源查询')
    @allure.title('异常：按哈希查询不存在')
    @pytest.mark.api
    async def test_resources_hash_not_found(self, mock_api_client, mock_auth_header):
        """异常流程：按哈希查询不存在"""
        await _api(mock_api_client, 'get', '/api/admin/resource-manager/resources/hash/abc123',
                   headers=mock_auth_header, expected_status=404)

    @allure.story('资源查询')
    @allure.title('正常：按所有者查询')
    @pytest.mark.api
    async def test_resources_owner(self, mock_api_client, mock_auth_header):
        """正常流程：按所有者查询"""
        await _api(mock_api_client, 'get', '/api/admin/resource-manager/resources/owner/user-1',
                   headers=mock_auth_header, expected_status=200)

    @allure.story('资源查询')
    @allure.title('数据校验：按类型查询非法类型')
    @pytest.mark.api
    async def test_resources_type_invalid(self, mock_api_client, mock_auth_header):
        """数据校验：按类型查询非法类型"""
        await _api(mock_api_client, 'get', '/api/admin/resource-manager/resources/type/evil',
                   headers=mock_auth_header, expected_status=422)

    @allure.story('资源查询')
    @allure.title('正常：按类型查询')
    @pytest.mark.api
    async def test_resources_type_ok(self, mock_api_client, mock_auth_header):
        """正常流程：按类型查询"""
        await _api(mock_api_client, 'get', '/api/admin/resource-manager/resources/type/document',
                   headers=mock_auth_header, expected_status=200)

    @allure.story('资源查询')
    @allure.title('正常：按分类查询')
    @pytest.mark.api
    async def test_resources_category(self, mock_api_client, mock_auth_header):
        """正常流程：按分类查询"""
        await _api(mock_api_client, 'get', '/api/admin/resource-manager/resources/category/手册',
                   headers=mock_auth_header, expected_status=200)

    @allure.story('资源查询')
    @allure.title('正常：资源搜索')
    @pytest.mark.api
    async def test_resources_search(self, mock_api_client, mock_auth_header):
        """正常流程：资源搜索"""
        await _api(mock_api_client, 'get', '/api/admin/resource-manager/resources/search/query?q=robot',
                   headers=mock_auth_header, expected_status=200)


@allure.feature('后台管理')
class TestResourceAccess:
    """资源访问控制"""

    @allure.story('资源访问')
    @allure.title('权限：下载不可用资源 403')
    @pytest.mark.api
    async def test_resources_download_unavailable(self, mock_api_client, mock_auth_header):
        """权限：下载不可用资源"""
        await _api(mock_api_client, 'get', '/api/admin/resource-manager/resources/1/download',
                   headers=mock_auth_header, expected_status=403)

    @allure.story('资源访问')
    @allure.title('正常：资源点赞')
    @pytest.mark.api
    async def test_resources_like(self, mock_api_client, mock_auth_header):
        """正常流程：资源点赞"""
        await _api(mock_api_client, 'post', '/api/admin/resource-manager/resources/1/like',
                   headers=mock_auth_header, expected_status=200, expected_fields={'liked': True})

    @allure.story('资源访问')
    @allure.title('异常：下载 URL 不可用')
    @pytest.mark.api
    async def test_resources_download_url_not_found(self, mock_api_client, mock_auth_header):
        """异常流程：下载 URL 不可用"""
        await _api(mock_api_client, 'get', '/api/admin/resource-manager/resources/1/download-url',
                   headers=mock_auth_header, expected_status=404)

    @allure.story('资源访问')
    @allure.title('异常：缩略图 URL 不可用')
    @pytest.mark.api
    async def test_resources_thumbnail_not_found(self, mock_api_client, mock_auth_header):
        """异常流程：缩略图 URL 不可用"""
        await _api(mock_api_client, 'get', '/api/admin/resource-manager/resources/1/thumbnail-url',
                   headers=mock_auth_header, expected_status=404)

    @allure.story('资源访问')
    @allure.title('正常：下载计数')
    @pytest.mark.api
    async def test_resources_download_count(self, mock_api_client, mock_auth_header):
        """正常流程：下载计数"""
        await _api(mock_api_client, 'post', '/api/admin/resource-manager/resources/1/download-count',
                   headers=mock_auth_header, expected_status=200)


@allure.feature('后台管理')
class TestResourceOps:
    """资源运维"""

    @allure.story('资源运维')
    @allure.title('正常：同步构建部署')
    @pytest.mark.api
    async def test_resources_sync_build(self, mock_api_client, mock_auth_header):
        """正常流程：同步构建部署产物"""
        await _api(mock_api_client, 'post', '/api/admin/resource-manager/resources/sync-build-deploy',
                   headers=mock_auth_header, json={'execute_nginx_reload': False}, expected_status=200)

    @allure.story('资源运维')
    @allure.title('正常：同步 OSS')
    @pytest.mark.api
    async def test_resources_sync_oss(self, mock_api_client, mock_auth_header):
        """正常流程：同步到 OSS"""
        await _api(mock_api_client, 'post', '/api/admin/resource-manager/resources/sync-oss',
                   headers=mock_auth_header, expected_status=200)


@allure.feature('后台管理')
class TestResourceFolders:
    """资源文件夹"""

    @allure.story('文件夹')
    @allure.title('正常：文件夹列表')
    @pytest.mark.api
    async def test_folders_list(self, mock_api_client, mock_auth_header):
        """正常流程：文件夹列表"""
        await _api(mock_api_client, 'get', '/api/admin/resource-manager/resource-folders/',
                   headers=mock_auth_header, expected_status=200)

    @allure.story('文件夹')
    @allure.title('正常：根文件夹')
    @pytest.mark.api
    async def test_folders_root(self, mock_api_client, mock_auth_header):
        """正常流程：根文件夹"""
        await _api(mock_api_client, 'get', '/api/admin/resource-manager/resource-folders/root',
                   headers=mock_auth_header, expected_status=200)

    @allure.story('文件夹')
    @allure.title('正常：根文件夹子级')
    @pytest.mark.api
    async def test_folders_root_children(self, mock_api_client, mock_auth_header):
        """正常流程：根文件夹子级"""
        await _api(mock_api_client, 'get', '/api/admin/resource-manager/resource-folders/root/children',
                   headers=mock_auth_header, expected_status=200)

    @allure.story('文件夹')
    @allure.title('数据校验：创建文件夹缺名称')
    @pytest.mark.api
    async def test_folders_create_missing_name(self, mock_api_client, mock_auth_header):
        """数据校验：创建文件夹缺 folder_name"""
        await _api(mock_api_client, 'post', '/api/admin/resource-manager/resource-folders/',
                   headers=mock_auth_header, json={}, expected_status=400)

    @allure.story('文件夹')
    @allure.title('正常：创建文件夹')
    @pytest.mark.api
    async def test_folders_create_ok(self, mock_api_client, mock_auth_header):
        """正常流程：创建文件夹"""
        await _api(mock_api_client, 'post', '/api/admin/resource-manager/resource-folders/',
                   headers=mock_auth_header, json={'folder_name': '操作手册'},
                   expected_status=201, expected_fields={'folder_name': '操作手册'})

    @allure.story('文件夹')
    @allure.title('正常：文件夹详情')
    @pytest.mark.api
    async def test_folders_detail(self, mock_api_client, mock_auth_header):
        """正常流程：文件夹详情"""
        await _api(mock_api_client, 'get', '/api/admin/resource-manager/resource-folders/1',
                   headers=mock_auth_header, expected_status=200, expected_fields={'id': 1})

    @allure.story('文件夹')
    @allure.title('正常：更新文件夹')
    @pytest.mark.api
    async def test_folders_update(self, mock_api_client, mock_auth_header):
        """正常流程：更新文件夹"""
        await _api(mock_api_client, 'put', '/api/admin/resource-manager/resource-folders/1',
                   headers=mock_auth_header, json={'folder_name': '改名'}, expected_status=200)

    @allure.story('文件夹')
    @allure.title('正常：删除文件夹')
    @pytest.mark.api
    async def test_folders_delete(self, mock_api_client, mock_auth_header):
        """正常流程：删除文件夹"""
        await _api(mock_api_client, 'delete', '/api/admin/resource-manager/resource-folders/1',
                   headers=mock_auth_header, expected_status=200)


@allure.feature('后台管理')
class TestMinio:
    """MinIO 对象存储"""

    @allure.story('MinIO')
    @allure.title('数据校验：预签名 URL 缺参数')
    @pytest.mark.api
    async def test_presigned_missing_params(self, mock_api_client, mock_auth_header):
        """数据校验：预签名 URL 缺参数"""
        await _api(mock_api_client, 'get', '/api/admin/resource-manager/minio/presigned-url',
                   headers=mock_auth_header, expected_status=422)

    @allure.story('MinIO')
    @allure.title('正常：生成预签名 URL')
    @pytest.mark.api
    async def test_presigned_ok(self, mock_api_client, mock_auth_header):
        """正常流程：生成预签名 URL"""
        await _api(mock_api_client, 'get',
                   '/api/admin/resource-manager/minio/presigned-url?bucket_name=docs&object_name=manual.pdf',
                   headers=mock_auth_header, expected_status=200,
                   expected_fields={'expires_minutes': 5})
