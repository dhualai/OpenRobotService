import re

import pytest

from app.modules.admin.services.knowledge_public_links import is_public_reference, redact_with_public_links


@pytest.mark.parametrize("url", [
    "https://docs.example.com/robot/manual.pdf",
    "http://docs.example.com/guide?page=2&lang=zh",
    "https://mp.weixin.qq.com/s?__biz=example&mid=123&idx=1&sn=public-article",
    "https://docs.example.com/Function_(robot)",
])
def test_public_reference_urls_are_allowed(url):
    assert is_public_reference(url)
    assert redact_with_public_links(url, lambda value: value) == url


@pytest.mark.parametrize("url", [
    "javascript:alert(1)", "data:text/html,test", "/tasks/123", "//docs.example.com/a",
    "http://localhost/a", "http://workstation/a", "http://robot.local/a", "http://docs.internal/a",
    "http://127.0.0.1/a", "http://10.0.0.1/a", "http://172.16.0.1/a", "http://192.168.1.1/a",
    "http://169.254.169.254/a", "http://[::1]/a", "http://[fd00::1]/a", "http://127.1/a",
    "https://user:password@docs.example.com/a", "https://docs.example.com:invalid/a",
    "https://docs.example.com\\@localhost/a", "https://docs.example.com/a\nb",
    "https://docs.example.com/a?access_token=secret", "https://docs.example.com/a?api-key=secret",
    "https://docs.example.com/a?%74oken=secret", "https://docs.example.com/a#access_token=secret",
    "https://docs.example.com/#/guide?session_id=secret",
    "https://docs.example.com/a?page=2&amp;token=secret",
    "https://docs.example.com/a?X-Amz-Signature=secret",
])
def test_private_credentialed_and_unsafe_reference_urls_are_rejected(url):
    assert not is_public_reference(url)


def test_internal_ticket_urls_are_rejected_without_blocking_public_manuals(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "FRONTEND_BASE_URL", "https://service.example.com/p/app")
    monkeypatch.setattr(settings, "ZENTAO_BASE_URL", "https://tickets.example.com")
    for url in [
        "https://service.example.com/p/app/tasks/12", "https://service.example.com/t/app/admin/projects/1",
        "https://service.example.com/p/api/tasks/12", "https://service.example.com/#/tasks/12",
        "https://tickets.example.com/ticket/12",
    ]:
        assert not is_public_reference(url)
    assert is_public_reference("https://service.example.com/manuals/robot.pdf")


def test_markdown_links_and_punctuation_are_preserved_without_leaking_sensitive_destinations():
    text = '手册[使用指南](https://docs.example.com/guide "说明")，参考 https://docs.example.com/Function_(robot)。内部[工单](/tasks/12)，地址 http://192.168.1.1/a。'
    result = redact_with_public_links(text, lambda value: value)
    assert "[使用指南](https://docs.example.com/guide)" in result
    assert "https://docs.example.com/Function_(robot)。内部" in result
    assert "工单[链接已隐藏]" in result
    assert "地址 [链接已隐藏]。" in result
    assert "/tasks/12" not in result
    assert "192.168.1.1" not in result


def test_encoded_sensitive_content_in_urls_is_hidden_instead_of_bypassing_redaction():
    def redact(value):
        value = value.replace("客户甲", "[客户已隐藏]")
        return re.sub(r"13800138000|user@example.com", "[联系方式已隐藏]", value)

    result = redact_with_public_links(
        "https://docs.example.com/13800138000 https://docs.example.com/%E5%AE%A2%E6%88%B7%E7%94%B2 "
        "https://docs.example.com/?q=user%40example.com 联系13800138000 客户甲", redact,
    )
    assert result.count("[链接已隐藏]") == 3
    for sensitive in ("13800138000", "%E5%AE%A2", "user%40example.com", "客户甲"):
        assert sensitive not in result


def test_external_markdown_images_are_references_not_automatic_embeds():
    result = redact_with_public_links("![图片](https://docs.example.com/image.png)", lambda value: value)
    assert result == "[图片](https://docs.example.com/image.png)"
