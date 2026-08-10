import time
from contextlib import contextmanager
from functools import wraps
from typing import Any, Callable, Generator, Optional


def format_duration(seconds: float) -> str:
    """Format a duration in seconds to a human-readable string.

    Args:
        seconds: Duration in seconds.

    Returns:
        Formatted string like "1.23s", "45ms", "2m 3.45s".
    """
    if seconds < 0.001:
        return f"{seconds * 1_000_000:.0f}us"
    if seconds < 1.0:
        return f"{seconds * 1000:.1f}ms"
    if seconds < 60.0:
        return f"{seconds:.2f}s"

    minutes = int(seconds // 60)
    secs = seconds % 60
    if minutes < 60:
        return f"{minutes}m {secs:.2f}s"

    hours = int(minutes // 60)
    minutes_remain = minutes % 60
    return f"{hours}h {minutes_remain}m {secs:.1f}s"


class Timer:
    """Context manager for measuring elapsed time of code blocks.

    Usage:
        with Timer() as t:
            do_something()
        print(f"Took {t.elapsed:.2f}s")

        t2 = Timer()
        t2.start()
        do_something()
        t2.stop()
        print(t2)
    """

    def __init__(self):
        self._start: Optional[float] = None
        self._end: Optional[float] = None

    def start(self) -> None:
        """Start the timer."""
        self._start = time.perf_counter()
        self._end = None

    def stop(self) -> float:
        """Stop the timer and return elapsed seconds."""
        if self._start is None:
            raise RuntimeError("Timer was not started")
        self._end = time.perf_counter()
        return self.elapsed

    def reset(self) -> None:
        """Reset the timer to initial state."""
        self._start = None
        self._end = None

    @property
    def elapsed(self) -> float:
        """Get elapsed time in seconds (without stopping the timer)."""
        if self._start is None:
            raise RuntimeError("Timer was not started")
        end = self._end or time.perf_counter()
        return end - self._start

    def __enter__(self) -> "Timer":
        self.start()
        return self

    def __exit__(self, *args: Any) -> None:
        self.stop()

    def __str__(self) -> str:
        try:
            return format_duration(self.elapsed)
        except RuntimeError:
            return "Timer not started"

    def __repr__(self) -> str:
        try:
            return f"Timer({self.elapsed:.3f}s)"
        except RuntimeError:
            return "Timer(not started)"


@contextmanager
def measure(name: str = "") -> Generator[Timer, None, None]:
    """Context manager that logs elapsed time via print.

    Args:
        name: Optional label for the measurement.

    Usage:
        with measure("database query"):
            run_query()
    """
    timer = Timer()
    try:
        yield timer
    finally:
        timer.stop()
        label = f" [{name}]" if name else ""
        print(f"Elapsed: {timer}{label}")
