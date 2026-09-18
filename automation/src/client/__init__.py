"""Client package for the OpenRobot test runtime."""

from automation.src.client.openrobot_client import (
    ALL_PROFILES,
    SCENARIOS,
    OpenRobotTestClient,
    Scenario,
)

__all__ = ["ALL_PROFILES", "SCENARIOS", "OpenRobotTestClient", "Scenario"]