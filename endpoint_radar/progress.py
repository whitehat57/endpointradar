from __future__ import annotations

import sys
import time
from typing import TextIO


def format_rate_limit(rate_limit: float) -> str:
    if isinstance(rate_limit, int):
        return str(rate_limit)
    return str(int(rate_limit)) if rate_limit.is_integer() else str(rate_limit)


class ProgressReporter:
    def __init__(self, enabled: bool = True, stream: TextIO = sys.stderr, interval_seconds: float = 0.5) -> None:
        self.enabled = enabled
        self.stream = stream
        self.interval_seconds = interval_seconds
        self.interactive = enabled and stream.isatty()
        self._last_update = 0.0
        self._current_length = 0

    def update_discovery(
        self,
        visited: int,
        queued: int,
        discovered: int,
        max_url: int,
        errors: int,
        force: bool = False,
    ) -> None:
        self._write(
            f"[discovery] visited={visited} queued={queued} discovered={discovered} max_url={max_url} errors={errors}",
            force,
        )

    def update_scan(
        self,
        attempts_done: int,
        total_attempts: int,
        endpoints_done: int,
        total_endpoints: int,
        errors: int,
        rate_limit: float,
        force: bool = False,
    ) -> None:
        self._write(
            "[scan] "
            f"attempts={attempts_done}/{total_attempts} "
            f"endpoints={endpoints_done}/{total_endpoints} "
            f"errors={errors} "
            f"rate_limit={format_rate_limit(rate_limit)}/s",
            force,
        )

    def finish(self) -> None:
        if not self.interactive or not self._current_length:
            return
        self.stream.write("\r" + (" " * self._current_length) + "\r")
        self.stream.flush()
        self._current_length = 0

    def _write(self, message: str, force: bool = False) -> None:
        if not self.interactive:
            return
        now = time.monotonic()
        if not force and now - self._last_update < self.interval_seconds:
            return
        padding = max(0, self._current_length - len(message))
        self.stream.write("\r" + message + (" " * padding))
        self.stream.flush()
        self._last_update = now
        self._current_length = len(message)
