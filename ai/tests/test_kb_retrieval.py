"""
知识库检索测试 — 所有 sub_domain × collection 的检索验证

需要真实 Qdrant 本地数据。使用 retrieval_service session-scoped fixture。
"""
import pytest
from ai.core.retrieval import RetrievalResult


# ================================================================
# 车端错误码（company/cheduan_errors）
# ================================================================

@pytest.mark.integration
class TestCheduanErrors:
    """车端错误码检索 — 精确匹配 + 语义搜索"""

    @pytest.mark.asyncio
    async def test_cheduan_exact_3digit(self, retrieval_service):
        """查询 "404" → 精确命中 error_code=404"""
        results = await retrieval_service.retrieve_cheduan("404")
        assert len(results) >= 1
        found_404 = any("404" in r.content for r in results)
        assert found_404, f"应该命中 error_code=404，实际: {[r.content[:80] for r in results]}"

    @pytest.mark.asyncio
    async def test_cheduan_exact_4digit(self, retrieval_service):
        """查询 "1301" → 精确命中 error_code=1301"""
        results = await retrieval_service.retrieve_cheduan("1301")
        assert len(results) >= 1
        found = any("1301" in r.content for r in results)
        assert found, f"应该命中 error_code=1301，实际: {[r.content[:80] for r in results]}"

    @pytest.mark.asyncio
    async def test_cheduan_semantic_cn(self, retrieval_service):
        """语义搜索 "激光传感器无数据" → 命中相关错误码"""
        results = await retrieval_service.retrieve_cheduan("激光传感器无数据")
        assert len(results) >= 1
        found_sensor = any(
            "激光" in r.content or "传感器" in r.content or "LiDAR" in r.content
            for r in results
        )
        assert found_sensor, f"应该命中激光/传感器相关，实际: {[r.content[:80] for r in results]}"

    @pytest.mark.asyncio
    async def test_cheduan_keyword_cn(self, retrieval_service):
        """关键字 "急停按钮" → 命中 413"""
        results = await retrieval_service.retrieve_cheduan("急停按钮")
        assert len(results) >= 1
        found = any("413" in r.content or "急停" in r.content for r in results)
        assert found, f"应该命中急停按钮相关，实际: {[r.content[:80] for r in results]}"

    @pytest.mark.asyncio
    async def test_cheduan_nonexistent_code(self, retrieval_service):
        """查询不存在的错误码 "99999" → 不命中 cheduan"""
        results = await retrieval_service.retrieve_cheduan("99999")
        # 精确匹配不应返回结果（99999 不在库中）
        exact_hits = [r for r in results if r.content.strip().startswith("错误码：99999")]
        assert len(exact_hits) == 0, f"不应该命中 error_code=99999"


# ================================================================
# FAQ（team/faq）
# ================================================================

@pytest.mark.integration
class TestFAQRetrieval:
    """FAQ 知识库检索"""

    @pytest.mark.asyncio
    async def test_faq_exact_question(self, retrieval_service):
        """精确问法 "上轨阈值怎么设置" → 命中"""
        results = await retrieval_service.retrieve_faq("上轨阈值怎么设置")
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_faq_vernacular(self, retrieval_service):
        """白话问法 "机器人怎么上轨" → 命中"""
        results = await retrieval_service.retrieve_faq("机器人怎么上轨")
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_faq_operation(self, retrieval_service):
        """操作类问题 "调度系统配置管理" → 命中"""
        results = await retrieval_service.retrieve_faq("调度系统配置管理在哪里")
        assert len(results) >= 1


# ================================================================
# 翻译表（team/translation）
# ================================================================

@pytest.mark.integration
class TestTranslationRetrieval:
    """USP 翻译表检索"""

    @pytest.mark.asyncio
    async def test_translation_cn_to_en(self, retrieval_service):
        """中文查询 "取消订单" → 命中翻译"""
        results = await retrieval_service.retrieve_translation("取消订单")
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_translation_en_to_cn(self, retrieval_service):
        """英文查询 "forkControl" → 命中翻译"""
        results = await retrieval_service.retrieve_translation("forkControl")
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_translation_fuzzy_en(self, retrieval_service):
        """模糊英文 "stacking" → 命中"""
        results = await retrieval_service.retrieve_translation("stacking")
        assert len(results) >= 1


# ================================================================
# USP 实施手册（team/usp_manual）
# ================================================================

@pytest.mark.integration
class TestUSPManualRetrieval:
    """USP 实施手册检索"""

    @pytest.mark.asyncio
    async def test_manual_exact_section(self, retrieval_service):
        """精确章节 "充电策略配置" → 命中"""
        # retrieve_domain with sub_domain filter
        results = await retrieval_service.retrieve_domain(
            "充电策略配置", domain="team", sub_domain="usp_manual"
        )
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_manual_multi_keyword(self, retrieval_service):
        """多关键字 "USP调度系统 配置管理 充电" → 命中"""
        results = await retrieval_service.retrieve_domain(
            "USP调度系统 配置管理 充电", domain="team", sub_domain="usp_manual"
        )
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_manual_edit_map(self, retrieval_service):
        """地图编辑 "USP实施 地图编辑" → 命中"""
        results = await retrieval_service.retrieve_domain(
            "USP实施 地图编辑", domain="team", sub_domain="usp_manual"
        )
        assert len(results) >= 1


# ================================================================
# 诊断卡片（team/diagnosis）
# ================================================================

@pytest.mark.integration
class TestDiagnosisRetrieval:
    """诊断卡片检索"""

    @pytest.mark.asyncio
    async def test_diagnosis_car_disappear(self, retrieval_service):
        """监控页面车消失 → 命中诊断卡"""
        results = await retrieval_service.retrieve_troubleshooting("监控页面车消失了")
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_diagnosis_car_slip(self, retrieval_service):
        """车打滑 → 命中诊断卡"""
        results = await retrieval_service.retrieve_troubleshooting("车打滑")
        assert len(results) >= 1


# ================================================================
# 产品目录（company/product_catalog）
# ================================================================

@pytest.mark.integration
class TestProductCatalogRetrieval:
    """产品目录检索"""

    @pytest.mark.asyncio
    async def test_product_exact_model(self, retrieval_service):
        """精确型号 "RPG201" → 命中"""
        results = await retrieval_service.retrieve_domain(
            "RPG201", domain="company", sub_domain="product_catalog"
        )
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_product_semantic(self, retrieval_service):
        """语义搜索 "堆高车" → 命中"""
        results = await retrieval_service.retrieve_domain(
            "堆高车", domain="company", sub_domain="product_catalog"
        )
        assert len(results) >= 1


# ================================================================
# 行业标准（industry/standards）
# ================================================================

@pytest.mark.integration
class TestIndustryStandardsRetrieval:
    """行业标准检索"""

    @pytest.mark.asyncio
    async def test_standards_gb_exact(self, retrieval_service):
        """精确查询 "GB/T 30029" → 命中"""
        results = await retrieval_service.retrieve_domain(
            "GB/T 30029", domain="industry"
        )
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_standards_safety(self, retrieval_service):
        """安全规范 → 命中"""
        results = await retrieval_service.retrieve_domain(
            "安全规范", domain="industry"
        )
        assert len(results) >= 1


# ================================================================
# 通讯协议（company/vda5050_protocol）
# ================================================================

@pytest.mark.integration
class TestVDA5050ProtocolRetrieval:
    """VDA5050 通讯协议检索"""

    @pytest.mark.asyncio
    async def test_protocol_velocity(self, retrieval_service):
        """英文查询 "VDA5050 velocity" → 命中"""
        results = await retrieval_service.retrieve_domain(
            "VDA5050 velocity", domain="company"
        )
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_protocol_heartbeat_cn(self, retrieval_service):
        """中文查询 "VDA5050 心跳" → 命中"""
        results = await retrieval_service.retrieve_domain(
            "VDA5050 心跳", domain="company"
        )
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_protocol_state_report_cn(self, retrieval_service):
        """中文查询 "机器人通讯协议 状态上报" → 命中"""
        results = await retrieval_service.retrieve_domain(
            "机器人通讯协议 状态上报", domain="company"
        )
        assert len(results) >= 1


# ================================================================
# retrieve_domain 通用接口
# ================================================================

@pytest.mark.integration
class TestRetrieveDomain:
    """retrieve_domain 通用接口测试"""

    @pytest.mark.asyncio
    async def test_domain_sub_filter(self, retrieval_service):
        """sub_domain="cheduan_errors" → 只返回 cheduan 结果"""
        results = await retrieval_service.retrieve_domain(
            "404", domain="company", sub_domain="cheduan_errors"
        )
        assert len(results) >= 1
        # 所有结果应来自 cheduan_errors
        for r in results:
            assert "错误码" in r.content or "error_code" in str(r.__dict__)

    @pytest.mark.asyncio
    async def test_domain_no_sub_filter(self, retrieval_service):
        """不设 sub_domain → 返回 company 下所有子域"""
        results = await retrieval_service.retrieve_domain(
            "机器人", domain="company"
        )
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_result_has_required_fields(self, retrieval_service):
        """RetrievalResult 包含必要字段"""
        results = await retrieval_service.retrieve_domain(
            "404", domain="company", sub_domain="cheduan_errors"
        )
        assert len(results) >= 1
        r = results[0]
        assert isinstance(r, RetrievalResult)
        assert hasattr(r, "content")
        assert hasattr(r, "score")
        assert hasattr(r, "title")
        assert 0.0 <= r.score <= 1.0

    @pytest.mark.asyncio
    async def test_retrieve_faq_scores_descending(self, retrieval_service):
        """FAQ 结果按分数降序"""
        results = await retrieval_service.retrieve_faq("机器人上轨", top_k=3)
        if len(results) >= 2:
            for i in range(len(results) - 1):
                assert results[i].score >= results[i + 1].score, \
                    f"结果应按分数降序: [{i}]={results[i].score}, [{i+1}]={results[i+1].score}"


# ================================================================
# 运行入口
# ================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-m", "integration"])
