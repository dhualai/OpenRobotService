"""Local UI regression runtime for the real test environment."""

from .capture import NetworkCapture
from .cleanup import CleanupManager
from .config import UiRegressionConfig
from .gateway import create_app
from .page_actions import UiPageActions
from .tunnels import UiTunnelManager

__all__ = [
    "CleanupManager",
    "NetworkCapture",
    "UiPageActions",
    "UiRegressionConfig",
    "UiTunnelManager",
    "create_app",
]
