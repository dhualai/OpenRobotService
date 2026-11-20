"""Concurrent scenario scheduling on top of the environment pool."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence

from automation.src.runtime.environments import EnvironmentPool, EnvironmentProfile

if TYPE_CHECKING:
    from automation.src.client import OpenRobotTestClient


@dataclass
class ScheduledTask:
    scenario: str
    profile: str = "fast"
    env: Optional[str] = None
    variables: Dict[str, Any] = field(default_factory=dict)
    tags: tuple[str, ...] = ()
    timeout: float = 1800.0


class RunScheduler:
    """Schedule runs with per-environment concurrency limits."""

    def __init__(
        self,
        client: Optional[OpenRobotTestClient] = None,
        pool: Optional[EnvironmentPool] = None,
    ):
        if client is None:
            from automation.src.client import OpenRobotTestClient

            client = OpenRobotTestClient()
        self.client = client
        self.pool = pool or EnvironmentPool()

    def run(self, task: ScheduledTask) -> Dict[str, Any]:
        environment = self.pool.select(required_tags=task.tags, preferred=task.env)
        with self.pool.acquire(environment.name):
            return self.client.run(
                scenario=task.scenario,
                profile=task.profile,
                env=environment.name,
                variables=task.variables,
                timeout=task.timeout,
                env_overrides=self._overrides(environment),
            )

    def run_many(
        self,
        tasks: Sequence[ScheduledTask],
        max_workers: int = 4,
    ) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
            futures = {executor.submit(self.run, task): task for task in tasks}
            for future in as_completed(futures):
                task = futures[future]
                try:
                    results.append(future.result())
                except Exception as exc:  # noqa: BLE001 - aggregate task failure
                    results.append(
                        {
                            "scenario": task.scenario,
                            "status": "failed",
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
        return results

    @staticmethod
    def _overrides(environment: EnvironmentProfile) -> Dict[str, str]:
        overrides = {
            "OPENROBOT_API_BASE_URL": environment.api_base_url,
            "REAL_API_BASE_URL": environment.api_base_url,
        }
        if environment.ai_base_url:
            overrides["AI_EVAL_BASE_URL"] = environment.ai_base_url
        return overrides