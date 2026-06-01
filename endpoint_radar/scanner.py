from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from endpoint_radar.logging_utils import write_jsonl
from endpoint_radar.progress import ProgressReporter
from endpoint_radar.utils import DiscoveredURL


class RateLimiter:
    def __init__(self, rate_limit: float) -> None:
        self.rate_limit = rate_limit
        self._lock = asyncio.Lock()
        self._next_request_at = 0.0

    async def wait(self) -> None:
        if self.rate_limit <= 0:
            return
        interval = 1.0 / self.rate_limit
        async with self._lock:
            now = time.monotonic()
            wait_for = max(0.0, self._next_request_at - now)
            self._next_request_at = max(now, self._next_request_at) + interval
        if wait_for:
            await asyncio.sleep(wait_for)


class ScanProgress:
    def __init__(
        self,
        reporter: ProgressReporter,
        total_attempts: int,
        total_endpoints: int,
        endpoint_attempts: dict[str, int],
        rate_limit: float,
    ) -> None:
        self.reporter = reporter
        self.total_attempts = total_attempts
        self.total_endpoints = total_endpoints
        self.endpoint_attempts = endpoint_attempts
        self.rate_limit = rate_limit
        self.attempts_done = 0
        self.endpoints_done = 0
        self.errors = 0
        self._lock = asyncio.Lock()

    async def record_attempt(self, url: str, had_error: bool) -> None:
        async with self._lock:
            self.attempts_done += 1
            if had_error:
                self.errors += 1
            remaining = self.endpoint_attempts.get(url)
            if remaining is not None:
                remaining -= 1
                if remaining <= 0:
                    self.endpoints_done += 1
                    self.endpoint_attempts.pop(url, None)
                else:
                    self.endpoint_attempts[url] = remaining
            self.reporter.update_scan(
                self.attempts_done,
                self.total_attempts,
                self.endpoints_done,
                self.total_endpoints,
                self.errors,
                self.rate_limit,
            )


def content_length(response: httpx.Response) -> int:
    header_value = response.headers.get("content-length")
    if header_value and header_value.isdigit():
        return int(header_value)
    return len(response.content)


async def scan_endpoint(
    client: httpx.AsyncClient,
    target: str,
    endpoint: DiscoveredURL,
    method: str,
    repeat: int,
    post_data: str | None,
    semaphore: asyncio.Semaphore,
    rate_limiter: RateLimiter,
    log_file: Path,
    log_lock: asyncio.Lock,
    scan_progress: ScanProgress | None = None,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for run_index in range(1, repeat + 1):
        record: dict[str, Any] = {
            "target": target,
            "url": endpoint.url,
            "method": method,
            "status_code": None,
            "elapsed_ms": None,
            "content_length": None,
            "content_type": None,
            "depth": endpoint.depth,
            "run_index": run_index,
            "error": None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await rate_limiter.wait()
        async with semaphore:
            started = time.perf_counter()
            try:
                if method == "POST":
                    response = await client.post(endpoint.url, content=post_data if post_data is not None else "{}")
                else:
                    response = await client.get(endpoint.url)
                elapsed_ms = round((time.perf_counter() - started) * 1000)
                record.update(
                    {
                        "status_code": response.status_code,
                        "elapsed_ms": elapsed_ms,
                        "content_length": content_length(response),
                        "content_type": response.headers.get("content-type"),
                    }
                )
            except httpx.HTTPError as exc:
                record["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
                record["error"] = exc.__class__.__name__
        await write_jsonl(log_file, record, log_lock)
        if scan_progress:
            await scan_progress.record_attempt(endpoint.url, bool(record["error"]))
        records.append(record)
    return records


def aggregate_results(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[(record["url"], record["method"])].append(record)

    aggregates: list[dict[str, Any]] = []
    for (url, method), group_records in grouped.items():
        elapsed = [record["elapsed_ms"] for record in group_records if isinstance(record["elapsed_ms"], int)]
        status_codes = [record["status_code"] for record in group_records if record["status_code"] is not None]
        sizes = [record["content_length"] for record in group_records if isinstance(record["content_length"], int)]
        content_types = [
            record["content_type"]
            for record in group_records
            if isinstance(record["content_type"], str) and record["content_type"]
        ]
        aggregates.append(
            {
                "url": url,
                "method": method,
                "avg_ms": round(sum(elapsed) / len(elapsed)) if elapsed else None,
                "min_ms": min(elapsed) if elapsed else None,
                "max_ms": max(elapsed) if elapsed else None,
                "status_codes": status_codes,
                "size": sizes[-1] if sizes else None,
                "content_length": sizes[-1] if sizes else None,
                "content_type": content_types[-1] if content_types else None,
                "error_count": sum(1 for record in group_records if record["error"]),
                "attempt_count": len(group_records),
            }
        )
    return sorted(
        aggregates,
        key=lambda item: item["avg_ms"] if isinstance(item["avg_ms"], int) else -1,
        reverse=True,
    )
