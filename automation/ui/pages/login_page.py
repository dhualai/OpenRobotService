"""Login page object."""
from playwright.sync_api import Page

FRONTEND_URL = "http://localhost:5173"


class LoginPage:
    """Admin login page (/login)."""

    def __init__(self, page: Page):
        self.page = page

    def navigate(self):
        self.page.goto(f"{FRONTEND_URL}/login", wait_until="networkidle")

    def login(self, username: str, password: str):
        self.page.fill("input[type=text]", username)
        self.page.fill("input[type=password]", password)
        self.page.click("button[type=submit]")
        self.page.wait_for_load_state("networkidle")

    @property
    def is_logged_in(self) -> bool:
        return "/login" not in self.page.url

    @property
    def current_url(self) -> str:
        return self.page.url
