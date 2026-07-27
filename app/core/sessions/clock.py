"""Injectable clock so session expiration tests never sleep for real time."""

import time
from typing import Protocol


class Clock(Protocol):
    def now(self) -> float: ...  # unix epoch seconds


class SystemClock:
    def now(self) -> float:
        return time.time()


class FakeClock:
    """Deterministic clock for tests. Starts at a fixed time; advance() moves
    it forward without any real sleeping."""

    def __init__(self, start: float | None = None) -> None:
        self.current = start if start is not None else time.time()

    def now(self) -> float:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current += seconds
