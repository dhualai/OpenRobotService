"""Environment profiles and capacity control for test runs."""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple


@dataclass(frozen=True)
class EnvironmentProfile:
    name: str
    api_base_url: str
    ai_base_url: Optional[str] = None
    tags: Tuple[str, ...] = field(default_factory=tuple)
    max_parallel: int = 1
    description: str = ""


def default_profiles() -> Dict[str, EnvironmentProfile]:
    return {
        "local": EnvironmentProfile(
            name="local",
            api_base_url=os.getenv("LOCAL_API_BASE_URL", "http://localhost:8400"),
            ai_base_url=os.getenv("LOCAL_AI_BASE_URL", "http://localhost:8401"),
            tags=("local", "mock", "fast"),
            max_parallel=4,
            description="Local mock/API and AI development environment",
        ),
        "test": EnvironmentProfile(
            name="test",
            api_base_url=os.getenv("TEST_API_BASE_URL", "http://127.0.0.1:9400"),
            ai_base_url=os.getenv("TEST_AI_BASE_URL", "http://127.0.0.1:9401"),
            tags=("test", "real", "pro"),
            max_parallel=1,
            description="Shared test environment reached through SSH tunnel",
        ),
        "nightly": EnvironmentProfile(
            name="nightly",
            api_base_url=os.getenv("NIGHTLY_API_BASE_URL", "http://127.0.0.1:9400"),
            ai_base_url=os.getenv("NIGHTLY_AI_BASE_URL", "http://127.0.0.1:9401"),
            tags=("test", "real", "nightly"),
            max_parallel=1,
            description="Nightly full regression environment",
        ),
    }


class EnvironmentPool:
    """Registry and per-environment concurrency guard."""

    def __init__(self, profiles: Optional[Dict[str, EnvironmentProfile]] = None):
        self._profiles = dict(profiles or default_profiles())
        self._semaphores = {
            name: threading.BoundedSemaphore(max(1, profile.max_parallel))
            for name, profile in self._profiles.items()
        }
        self._lock = threading.RLock()

    def register(self, profile: EnvironmentProfile) -> None:
        with self._lock:
            self._profiles[profile.name] = profile
            self._semaphores[profile.name] = threading.BoundedSemaphore(max(1, profile.max_parallel))

    def get(self, name: str) -> EnvironmentProfile:
        try:
            return self._profiles[name]
        except KeyError as exc:
            raise ValueError(f"Unknown environment: {name}. Known: {', '.join(sorted(self._profiles))}") from exc

    def list(self, tags: Iterable[str] = ()) -> List[EnvironmentProfile]:
        required = {tag.lower() for tag in tags}
        return [
            profile
            for profile in self._profiles.values()
            if required.issubset({tag.lower() for tag in profile.tags})
        ]

    def select(
        self,
        required_tags: Iterable[str] = (),
        preferred: Optional[str] = None,
    ) -> EnvironmentProfile:
        if preferred:
            profile = self.get(preferred)
            required = {tag.lower() for tag in required_tags}
            if not required.issubset({tag.lower() for tag in profile.tags}):
                raise ValueError(
                    f"Environment {preferred!r} does not satisfy tags {sorted(required)}"
                )
            return profile
        candidates = self.list(required_tags)
        if not candidates:
            raise ValueError(f"No environment satisfies tags: {sorted(required_tags)}")
        return sorted(candidates, key=lambda item: (item.max_parallel, item.name), reverse=True)[0]

    @contextmanager
    def acquire(self, name: str, timeout: Optional[float] = None):
        semaphore = self._semaphores.get(name)
        if semaphore is None:
            self.get(name)
            raise AssertionError("environment semaphore missing")
        acquired = semaphore.acquire(timeout=timeout) if timeout is not None else semaphore.acquire()
        if not acquired:
            raise TimeoutError(f"Timed out waiting for environment slot: {name}")
        try:
            yield self.get(name)
        finally:
            semaphore.release()