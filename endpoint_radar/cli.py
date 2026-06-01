from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Any

import httpx

from endpoint_radar.crawler import crawl
from endpoint_radar.filters import (
    is_dangerous_path,
    is_js_asset,
    is_skippable_asset,
    normalize_target_url,
)
from endpoint_radar.logging_utils import default_discovery_log_file, default_log_file, write_discovery_jsonl
from endpoint_radar.progress import ProgressReporter
from endpoint_radar.reporting import write_csv_summary
from endpoint_radar.scanner import RateLimiter, ScanProgress, aggregate_results, scan_endpoint
from endpoint_radar.utils import DEFAULT_USER_AGENT
from endpoint_radar.waf_detector import WAFDetectionResult, detect_waf


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
    parser.add_argument("--dry-run", action="store_true", help="Run discovery only and skip latency scanning.")
    parser.add_argument("--detect-waf", action="store_true", help="Passively detect WAF/CDN metadata.")
    parser.add_argument("--csv", help="Optional CSV summary path for aggregated scan results.")
    parser.add_argument(
        "--header",
        action="append",
        default=[],
        help='Repeatable custom header, e.g. --header "Authorization: Bearer TOKEN".',
    )
    return parser.parse_args()


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


def print_summary(
    target: str,
    urls_discovered: int,
    urls_tested: int,
    request_attempts: int,
    errors: int,
    log_file: Path,
    top_slowest: list[dict[str, Any]],
    waf_result: WAFDetectionResult | None = None,
    csv_file: Path | None = None,
) -> None:
    print("EndpointRadar scan completed.")
    print()
    print(f"Target            : {target}")
    if waf_result:
        print_waf_summary(waf_result)
    print(f"URLs discovered   : {urls_discovered}")
    print(f"URLs tested       : {urls_tested}")
    print(f"Request attempts  : {request_attempts}")
    print(f"Errors            : {errors}")
    print(f"Log file          : {log_file}")
    if csv_file:
        print(f"CSV summary       : {csv_file}")
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


def print_discovery_summary(
    target: str,
    urls_discovered: int,
    log_file: Path,
    waf_result: WAFDetectionResult | None = None,
    csv_skipped: bool = False,
) -> None:
    print("EndpointRadar discovery completed.")
    print()
    print(f"Target            : {target}")
    if waf_result:
        print_waf_summary(waf_result)
    print(f"URLs discovered   : {urls_discovered}")
    print(f"Log file          : {log_file}")
    if csv_skipped:
        print("CSV summary       : skipped because --dry-run does not perform latency scanning")
    print()
    print("No latency scan was performed because --dry-run is enabled.")


def print_waf_summary(result: WAFDetectionResult) -> None:
    detected = "yes" if result.detected else "unknown"
    vendor = result.vendor if result.vendor else "unknown"
    evidence = ", ".join(result.evidence) if result.evidence else "none"
    print(f"WAF/CDN detected  : {detected}")
    print(f"WAF/CDN vendor    : {vendor}")
    print(f"Category          : {result.category}")
    print(f"Confidence        : {result.confidence}")
    print(f"Evidence          : {evidence}")
    print()


async def run(args: argparse.Namespace) -> None:
    target = normalize_target_url(args.target)
    headers = parse_headers(args.header, args.user_agent)

    if args.depth < 0:
        raise ValueError("--depth must be 0 or greater.")
    if args.max_url < 1:
        raise ValueError("--max-url must be 1 or greater.")
    if args.concurrency < 1:
        raise ValueError("--concurrency must be 1 or greater.")
    if args.timeout <= 0:
        raise ValueError("--timeout must be greater than 0.")
    if args.rate_limit < 0:
        raise ValueError("--rate-limit must be 0 or greater.")
    if not args.dry_run and args.repeat < 1:
        raise ValueError("--repeat must be 1 or greater.")
    if args.dry_run:
        methods: list[str] = []
        post_data = None
    else:
        methods = parse_methods(args.methods)
        if "POST" in methods:
            headers.setdefault("Content-Type", "application/json")
        post_data = args.post_data if args.post_data is not None else ("{}" if "POST" in methods else None)

    log_file = Path(args.log_file) if args.log_file else (
        default_discovery_log_file() if args.dry_run else default_log_file()
    )
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
        waf_result = await detect_waf(client, target) if args.detect_waf else None
        discovered_urls = await crawl(client, target, args.depth, args.max_url, rate_limiter, progress)
        if args.dry_run:
            progress.finish()
            write_discovery_jsonl(log_file, target, discovered_urls)
            print_discovery_summary(target, len(discovered_urls), log_file, waf_result, csv_skipped=bool(args.csv))
            return

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
    csv_file = Path(args.csv) if args.csv else None
    if csv_file:
        write_csv_summary(csv_file, aggregates)
    progress.finish()
    print_summary(
        target=target,
        urls_discovered=len(discovered_urls),
        urls_tested=len(testable_urls),
        request_attempts=len(records),
        errors=errors,
        log_file=log_file,
        top_slowest=[item for item in aggregates if isinstance(item["avg_ms"], int)][:3],
        waf_result=waf_result,
        csv_file=csv_file,
    )


def main() -> int:
    args = parse_args()
    try:
        asyncio.run(run(args))
    except (ValueError, KeyboardInterrupt) as exc:
        print(f"EndpointRadar error: {exc}")
        return 1
    return 0
