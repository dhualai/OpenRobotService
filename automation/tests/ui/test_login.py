"""Login page UI tests."""
import pytest
from automation.ui.pages.login_page import LoginPage


pytestmark = pytest.mark.ui


class TestLogin:
    """Login page functionality."""

    def test_login_page_loads(self, page):
        login_page = LoginPage(page)
        login_page.navigate()
        assert "login" in login_page.current_url

    def test_login_empty_fields_stays_on_login(self, page):
        login_page = LoginPage(page)
        login_page.navigate()
        login_page.login("", "")
        assert not login_page.is_logged_in

    def test_login_invalid_credentials(self, page):
        login_page = LoginPage(page)
        login_page.navigate()
        login_page.login("invalid_user", "wrong_pass")
        assert not login_page.is_logged_in
