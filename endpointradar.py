#!/usr/bin/env python3
"""EndpointRadar MVP: same-hostname endpoint discovery and latency profiling."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse
from xml.etree import ElementTree

import httpx
from bs4 import BeautifulSoup


DEFAULT_USER_AGENT = "EndpointRadar/0.1 (+authorized performance testing)"
DEFAULT_STATIC_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".svg",
    ".webp",
    ".ico",
    ".css",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".mp4",
    ".mp3",
    ".avi",
    ".mov",
    ".zip",
    ".rar",
    ".7z",
    ".pdf",
}
JS_EXTENSIONS = {".js", ".mjs"}
DANGEROUS_PATH_KEYWORDS = {
    "/logout",
    "/delete",
    "/remove",
    "/destroy",
    "/drop",
    "/payment",
    "/checkout",
    "/order",
    "/purchase",
    "/cart/clear",
    "/admin/delete",
}
JS_ENDPOINT_RE = re.compile(
    r"""(?P<quote>["'`])(?P<url>(?:https?://[^"'`\s<>]+|/[^"'`\s<>]+))(?P=quote)""",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DiscoveredURL:
    url: str
    depth: int


@dataclass
class RateLimiter:
    rate_limit: float

    def __post_init__(self) -> None:
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


def format_rate_limit(rate_limit: float) -> str:
    if isinstance(rate_limit, int):
        return str(rate_limit)
    return str(int(rate_limit)) if rate_limit.is_integer() else str(rate_limit)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Discover same-hostname endpoints and profile HTTP latency."
    )
    parser.add_argument("target", help="Target URL. Defaults to https:// when scheme is missing.")
    parser.add_argument("--methods", default="GET", help='Comma-separated methods, e.g. "GET" or "GET,POST".')
    parser.add_argument("--depth", type=int, default=2, help="Maximum crawl depth.")
    parser.add_argument("--max-url", type=int, default=250, help="Maximum discovered URLs to test.")
    parser.add_argument("--concurrency", type=int, default=10, help="Maximum concurrent HTTP requests.")
    parser.add_argument("--rate-limit", type=float, default=5, help="Global maximum requests per second. Use 0 to disable.")
    parser.add_argument("--timeout", type=float, default=15, help="HTTP timeout in seconds.")
    parser.add_argument("--repeat", type=int, default=3, help="Request attempts per endpoint-method pair.")
    parser.add_argument("--log-file", help="Optional JSONL log file path.")
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT, help="Custom User-Agent header.")
    parser.add_argument("--post-data", help="POST request body. Defaults to {} only when POST is enabled.")
    parser.add_argument("--no-progress", action="store_true", help="Suppress runtime progress output.")
    parser.add_argument(
        "--header",
        action="append",
        default=[],
        help='Repeatable custom header, e.g. --header "Authorization: Bearer TOKEN".',
    )
    return parser.parse_args()


def normalize_target_url(raw_target: str) -> str:
    target = raw_target.strip()
    if not target:
        raise ValueError("Target URL cannot be empty.")
    if "://" not in target:
        target = f"https://{target}"
    parsed = urlparse(target)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Target URL scheme must be http or https.")
    if not parsed.hostname:
        raise ValueError("Target URL must include a hostname.")
    return normalize_url(target)


def parse_methods(raw_methods: str) -> list[str]:
    methods = [method.strip().upper() for method in raw_methods.split(",") if method.strip()]
    allowed = {"GET", "POST"}
    if not methods:
        raise ValueError("At least one HTTP method is required.")
    invalid = [method for method in methods if method not in allowed]
    if invalid:
        raise ValueError(f"Unsupported method(s): {', '.join(invalid)}. Supported methods: GET, POST.")
    return list(dict.fromkeys(methods))


def parse_headers(raw_headers: list[str], user_agent: str) -> dict[str, str]:
    headers = {"User-Agent": user_agent}
    for raw_header in raw_headers:
        if ":" not in raw_header:
            raise ValueError(f"Invalid header {raw_header!r}. Expected 'Name: Value'.")
        name, value = raw_header.split(":", 1)
        name = name.strip()
        if not name:
            raise ValueError(f"Invalid header {raw_header!r}. Header name cannot be empty.")
        headers[name] = value.strip()
    return headers


def is_same_hostname(url: str, target_hostname: str) -> bool:
    parsed = urlparse(url)
    return bool(parsed.hostname) and parsed.hostname.lower() == target_hostname.lower()


def _path_extension(url: str) -> str:
    return Path(urlparse(url).path.lower()).suffix


def is_skippable_asset(url: str) -> bool:
    return _path_extension(url) in DEFAULT_STATIC_EXTENSIONS


def is_js_asset(url: str) -> bool:
    return _path_extension(url) in JS_EXTENSIONS


def is_dangerous_path(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(keyword in path for keyword in DANGEROUS_PATH_KEYWORDS)


def normalize_url(raw_url: str, base_url: str | None = None) -> str:
    joined = urljoin(base_url, raw_url) if base_url else raw_url
    parsed = urlparse(joined)
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path or "/"
    query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
    query = urlencode(sorted(query_pairs), doseq=True)
    normalized = parsed._replace(scheme=scheme, netloc=netloc, path=path, params="", query=query, fragment="")
    return urlunparse(normalized)


async def fetch_text(
    client: httpx.AsyncClient,
    url: str,
    rate_limiter: RateLimiter,
) -> str | None:
    await rate_limiter.wait()
    try:
        response = await client.get(url)
        content_type = response.headers.get("content-type", "")
        if response.status_code >= 400:
            return None
        if not any(kind in content_type.lower() for kind in ("text/html", "application/xhtml+xml", "javascript", "xml", "text/plain")):
            return None
        return response.text
    except httpx.HTTPError:
        return None


def _add_candidate(
    candidates: set[str],
    raw_url: str | None,
    base_url: str,
    target_hostname: str,
    allow_assets: bool = False,
) -> None:
    if not raw_url:
        return
    normalized = normalize_url(raw_url, base_url)
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"}:
        return
    if not is_same_hostname(normalized, target_hostname):
        return
    if not allow_assets and is_skippable_asset(normalized):
        return
    candidates.add(normalized)


def discover_from_html(html: str, base_url: str, target_hostname: str) -> tuple[set[str], set[str]]:
    soup = BeautifulSoup(html, "html.parser")
    endpoints: set[str] = set()
    js_urls: set[str] = set()

    for tag in soup.find_all("a", href=True):
        _add_candidate(endpoints, tag.get("href"), base_url, target_hostname)
    for tag in soup.find_all("form", action=True):
        _add_candidate(endpoints, tag.get("action"), base_url, target_hostname)
    for tag in soup.find_all("script", src=True):
        src = tag.get("src")
        normalized = normalize_url(src, base_url) if src else ""
        if is_same_hostname(normalized, target_hostname) and is_js_asset(normalized):
            js_urls.add(normalized)

    return endpoints, js_urls


def discover_from_js(js_text: str, base_url: str, target_hostname: str) -> set[str]:
    endpoints: set[str] = set()
    for match in JS_ENDPOINT_RE.finditer(js_text):
        _add_candidate(endpoints, match.group("url"), base_url, target_hostname)
    return endpoints


async def discover_from_sitemap(
    client: httpx.AsyncClient,
    target_url: str,
    target_hostname: str,
    rate_limiter: RateLimiter,
) -> set[str]:
    parsed = urlparse(target_url)
    sitemap_url = urlunparse((parsed.scheme, parsed.netloc, "/sitemap.xml", "", "", ""))
    text = await fetch_text(client, sitemap_url, rate_limiter)
    if not text:
        return set()

    endpoints: set[str] = set()
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        return endpoints

    for element in root.iter():
        if element.tag.lower().endswith("loc") and element.text:
            _add_candidate(endpoints, element.text.strip(), sitemap_url, target_hostname)
    return endpoints


async def crawl(
    client: httpx.AsyncClient,
    target_url: str,
    max_depth: int,
    max_url: int,
    rate_limiter: RateLimiter,
    progress: ProgressReporter | None = None,
) -> list[DiscoveredURL]:
    target_hostname = urlparse(target_url).hostname or ""
    discovered: dict[str, int] = {target_url: 0}
    queue: deque[DiscoveredURL] = deque([DiscoveredURL(target_url, 0)])
    visited = 0
    discovery_errors = 0
    if progress:
        progress.update_discovery(visited, len(queue), len(discovered), max_url, discovery_errors, force=True)

    if max_depth >= 1:
        sitemap_urls = await discover_from_sitemap(client, target_url, target_hostname, rate_limiter)
        for url in sorted(sitemap_urls):
            if len(discovered) >= max_url:
                break
            if url in discovered:
                continue
            discovered[url] = 1
            queue.append(DiscoveredURL(url, 1))
        if progress:
            progress.update_discovery(visited, len(queue), len(discovered), max_url, discovery_errors)

    fetched_js: set[str] = set()
    while queue and len(discovered) <= max_url:
        current = queue.popleft()
        if current.depth >= max_depth or is_skippable_asset(current.url) or is_js_asset(current.url):
            continue

        visited += 1
        html = await fetch_text(client, current.url, rate_limiter)
        if not html:
            discovery_errors += 1
            if progress:
                progress.update_discovery(visited, len(queue), len(discovered), max_url, discovery_errors)
            continue

        endpoints, js_urls = discover_from_html(html, current.url, target_hostname)
        for js_url in sorted(js_urls):
            if js_url in fetched_js:
                continue
            fetched_js.add(js_url)
            js_text = await fetch_text(client, js_url, rate_limiter)
            if js_text:
                endpoints.update(discover_from_js(js_text, js_url, target_hostname))

        for url in sorted(endpoints):
            if len(discovered) >= max_url:
                break
            if url in discovered:
                continue
            next_depth = current.depth + 1
            if next_depth > max_depth:
                continue
            discovered[url] = next_depth
            queue.append(DiscoveredURL(url, next_depth))
        if progress:
            progress.update_discovery(visited, len(queue), len(discovered), max_url, discovery_errors)

    if progress:
        progress.update_discovery(visited, len(queue), len(discovered), max_url, discovery_errors, force=True)
    return [DiscoveredURL(url, depth) for url, depth in discovered.items()]


def content_length(response: httpx.Response) -> int:
    header_value = response.headers.get("content-length")
    if header_value and header_value.isdigit():
        return int(header_value)
    return len(response.content)


async def write_jsonl(log_file: Path, record: dict[str, Any], lock: asyncio.Lock) -> None:
    line = json.dumps(record, sort_keys=True, separators=(",", ":"))
    async with lock:
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


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
        if not elapsed:
            continue
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
                "avg_ms": round(sum(elapsed) / len(elapsed)),
                "min_ms": min(elapsed),
                "max_ms": max(elapsed),
                "status_codes": status_codes,
                "size": sizes[-1] if sizes else None,
                "content_type": content_types[-1] if content_types else None,
                "error_count": sum(1 for record in group_records if record["error"]),
            }
        )
    return sorted(aggregates, key=lambda item: item["avg_ms"], reverse=True)


def print_summary(
    target: str,
    urls_discovered: int,
    urls_tested: int,
    request_attempts: int,
    errors: int,
    log_file: Path,
    top_slowest: list[dict[str, Any]],
) -> None:
    print("EndpointRadar scan completed.")
    print()
    print(f"Target            : {target}")
    print(f"URLs discovered   : {urls_discovered}")
    print(f"URLs tested       : {urls_tested}")
    print(f"Request attempts  : {request_attempts}")
    print(f"Errors            : {errors}")
    print(f"Log file          : {log_file}")
    print()
    print("Top 3 Slowest Endpoints:")
    print()

    for index, item in enumerate(top_slowest[:3], start=1):
        status = ", ".join(str(code) for code in item["status_codes"]) if item["status_codes"] else "n/a"
        size = f"{item['size']} bytes" if item["size"] is not None else "n/a"
        print(f"{index}. {item['url']}")
        print(f"   Method : {item['method']}")
        print(f"   Avg    : {item['avg_ms']} ms")
        print(f"   Min    : {item['min_ms']} ms")
        print(f"   Max    : {item['max_ms']} ms")
        print(f"   Status : {status}")
        print(f"   Size   : {size}")
        print()


def default_log_file() -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path("logs") / f"endpointradar-{timestamp}.jsonl"


async def run(args: argparse.Namespace) -> None:
    target = normalize_target_url(args.target)
    methods = parse_methods(args.methods)
    headers = parse_headers(args.header, args.user_agent)
    if "POST" in methods:
        headers.setdefault("Content-Type", "application/json")
    post_data = args.post_data if args.post_data is not None else ("{}" if "POST" in methods else None)

    if args.depth < 0:
        raise ValueError("--depth must be 0 or greater.")
    if args.max_url < 1:
        raise ValueError("--max-url must be 1 or greater.")
    if args.concurrency < 1:
        raise ValueError("--concurrency must be 1 or greater.")
    if args.timeout <= 0:
        raise ValueError("--timeout must be greater than 0.")
    if args.repeat < 1:
        raise ValueError("--repeat must be 1 or greater.")
    if args.rate_limit < 0:
        raise ValueError("--rate-limit must be 0 or greater.")

    log_file = Path(args.log_file) if args.log_file else default_log_file()
    log_file.parent.mkdir(parents=True, exist_ok=True)

    timeout = httpx.Timeout(args.timeout)
    limits = httpx.Limits(max_connections=args.concurrency, max_keepalive_connections=args.concurrency)
    rate_limiter = RateLimiter(args.rate_limit)
    semaphore = asyncio.Semaphore(args.concurrency)
    log_lock = asyncio.Lock()
    progress = ProgressReporter(enabled=not args.no_progress)

    # Redirects are intentionally not followed because a same-hostname URL can
    # redirect to an external hostname, which would expand the requested scope.
    async with httpx.AsyncClient(headers=headers, timeout=timeout, limits=limits, follow_redirects=False) as client:
        discovered_urls = await crawl(client, target, args.depth, args.max_url, rate_limiter, progress)
        testable_urls = [
            discovered
            for discovered in discovered_urls
            if not is_skippable_asset(discovered.url)
            and not is_js_asset(discovered.url)
            and not is_dangerous_path(discovered.url)
        ]

        total_attempts = len(testable_urls) * len(methods) * args.repeat
        scan_progress = ScanProgress(
            reporter=progress,
            total_attempts=total_attempts,
            total_endpoints=len(testable_urls),
            endpoint_attempts={endpoint.url: len(methods) * args.repeat for endpoint in testable_urls},
            rate_limit=args.rate_limit,
        )
        progress.update_scan(0, total_attempts, 0, len(testable_urls), 0, args.rate_limit, force=True)
        tasks = [
            scan_endpoint(
                client,
                target,
                endpoint,
                method,
                args.repeat,
                post_data,
                semaphore,
                rate_limiter,
                log_file,
                log_lock,
                scan_progress,
            )
            for endpoint in testable_urls
            for method in methods
        ]
        nested_records = await asyncio.gather(*tasks)
        progress.update_scan(
            scan_progress.attempts_done,
            total_attempts,
            scan_progress.endpoints_done,
            len(testable_urls),
            scan_progress.errors,
            args.rate_limit,
            force=True,
        )

    records = [record for group in nested_records for record in group]
    errors = sum(1 for record in records if record["error"])
    aggregates = aggregate_results(records)
    progress.finish()
    print_summary(
        target=target,
        urls_discovered=len(discovered_urls),
        urls_tested=len(testable_urls),
        request_attempts=len(records),
        errors=errors,
        log_file=log_file,
        top_slowest=aggregates[:3],
    )


def main() -> int:
    args = parse_args()
    try:
        asyncio.run(run(args))
    except (ValueError, KeyboardInterrupt) as exc:
        print(f"EndpointRadar error: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
