import argparse
import asyncio
import json

import pytest

from endpoint_radar import cli
from endpoint_radar.cli import parse_args
from endpoint_radar.filters import (
    is_dangerous_path,
    is_same_hostname,
    is_skippable_asset,
    normalize_target_url,
    normalize_url,
)
from endpoint_radar.parsers import discover_from_html
from endpoint_radar.progress import ProgressReporter
from endpoint_radar.scanner import aggregate_results
from endpoint_radar.utils import DiscoveredURL


def test_normalize_target_url_defaults_to_https_and_trailing_slash() -> None:
    assert normalize_target_url("example.com") == "https://example.com/"


def test_normalize_target_url_preserves_http_scheme() -> None:
    assert normalize_target_url("http://example.com/path") == "http://example.com/path"


def test_normalize_target_url_rejects_unsupported_scheme() -> None:
    with pytest.raises(ValueError):
        normalize_target_url("ftp://example.com")


def test_exact_hostname_scope_allows_only_same_hostname() -> None:
    assert is_same_hostname("https://example.com/page", "example.com")
    assert is_same_hostname("https://EXAMPLE.com/page", "example.com")


def test_subdomain_is_not_same_hostname() -> None:
    assert not is_same_hostname("https://www.example.com/page", "example.com")
    assert not is_same_hostname("https://api.example.com/page", "example.com")


def test_discover_from_html_skips_external_links() -> None:
    html = """
    <a href="/internal"></a>
    <a href="https://external.test/page"></a>
    """

    endpoints, js_urls = discover_from_html(html, "https://example.com/", "example.com")

    assert endpoints == {"https://example.com/internal"}
    assert js_urls == set()


def test_discover_from_html_skips_subdomain_links() -> None:
    html = """
    <a href="https://example.com/internal"></a>
    <a href="https://www.example.com/subdomain"></a>
    """

    endpoints, _ = discover_from_html(html, "https://example.com/", "example.com")

    assert endpoints == {"https://example.com/internal"}


def test_static_asset_skipping() -> None:
    assert is_skippable_asset("https://example.com/image.JPG")
    assert is_skippable_asset("https://example.com/fonts/site.woff2")
    assert is_skippable_asset("https://example.com/report.pdf")
    assert not is_skippable_asset("https://example.com/api/products")


def test_dangerous_path_detection() -> None:
    assert is_dangerous_path("https://example.com/logout")
    assert is_dangerous_path("https://example.com/admin/delete/123")
    assert is_dangerous_path("https://example.com/cart/clear")
    assert not is_dangerous_path("https://example.com/products")


def test_url_normalization_removes_fragments_and_sorts_query() -> None:
    assert (
        normalize_url("https://example.com/path?b=2&a=1#section")
        == "https://example.com/path?a=1&b=2"
    )


def test_aggregate_results_preserves_content_type() -> None:
    rows = [
        {
            "url": "https://example.com/api",
            "method": "GET",
            "elapsed_ms": 100,
            "status_code": 200,
            "content_length": 10,
            "content_type": "application/json",
            "error": None,
        },
        {
            "url": "https://example.com/api",
            "method": "GET",
            "elapsed_ms": 200,
            "status_code": 200,
            "content_length": 12,
            "content_type": "application/json; charset=utf-8",
            "error": None,
        },
    ]

    assert aggregate_results(rows)[0]["content_type"] == "application/json; charset=utf-8"


def test_aggregate_results_computes_avg_min_max() -> None:
    rows = [
        {
            "url": "https://example.com/search",
            "method": "GET",
            "elapsed_ms": 90,
            "status_code": 200,
            "content_length": 100,
            "content_type": "text/html",
            "error": None,
        },
        {
            "url": "https://example.com/search",
            "method": "GET",
            "elapsed_ms": 150,
            "status_code": 200,
            "content_length": 110,
            "content_type": "text/html",
            "error": None,
        },
        {
            "url": "https://example.com/search",
            "method": "GET",
            "elapsed_ms": 300,
            "status_code": 200,
            "content_length": 120,
            "content_type": "text/html",
            "error": None,
        },
    ]

    result = aggregate_results(rows)[0]

    assert result["avg_ms"] == 180
    assert result["min_ms"] == 90
    assert result["max_ms"] == 300


def test_parse_args_supports_no_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["endpointradar.py", "https://example.com", "--no-progress"])

    args = parse_args()

    assert args.no_progress is True


def test_parse_args_supports_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["endpointradar.py", "https://example.com", "--dry-run"])

    args = parse_args()

    assert args.dry_run is True


def test_progress_reporter_disabled_writes_nothing() -> None:
    class FakeTTY:
        def __init__(self) -> None:
            self.output = ""

        def isatty(self) -> bool:
            return True

        def write(self, value: str) -> int:
            self.output += value
            return len(value)

        def flush(self) -> None:
            return None

    stream = FakeTTY()
    reporter = ProgressReporter(enabled=False, stream=stream)

    reporter.update_discovery(visited=1, queued=2, discovered=3, max_url=250, errors=0, force=True)
    reporter.update_scan(
        attempts_done=1,
        total_attempts=3,
        endpoints_done=1,
        total_endpoints=3,
        errors=0,
        rate_limit=5,
        force=True,
    )
    reporter.finish()

    assert stream.output == ""


def test_write_discovery_jsonl_writes_expected_fields(tmp_path) -> None:
    from endpoint_radar.logging_utils import write_discovery_jsonl

    log_file = tmp_path / "discovery.jsonl"
    discovered_urls = [
        DiscoveredURL("https://example.com/", 0),
        DiscoveredURL("https://example.com/about", 1),
    ]

    write_discovery_jsonl(log_file, "https://example.com/", discovered_urls)

    lines = log_file.read_text(encoding="utf-8").splitlines()
    records = [json.loads(line) for line in lines]
    assert len(records) == 2
    assert records[0]["target"] == "https://example.com/"
    assert records[0]["url"] == "https://example.com/"
    assert records[0]["depth"] == 0
    assert "timestamp" in records[0]
    assert set(records[0]) == {"target", "url", "depth", "timestamp"}


def test_dry_run_skips_scanning_and_prints_discovery_summary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    discovered_urls = [
        DiscoveredURL("https://example.com/", 0),
        DiscoveredURL("https://example.com/about", 1),
    ]

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

    async def fake_crawl(client, target, depth, max_url, rate_limiter, progress):
        return discovered_urls

    async def fail_scan(*args, **kwargs):
        raise AssertionError("scan_endpoint should not be called in dry-run mode")

    monkeypatch.setattr(cli.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(cli, "crawl", fake_crawl)
    monkeypatch.setattr(cli, "scan_endpoint", fail_scan)

    log_file = tmp_path / "dry-run.jsonl"
    args = argparse.Namespace(
        target="example.com",
        methods="INVALID",
        depth=2,
        max_url=250,
        concurrency=10,
        rate_limit=5,
        timeout=15,
        repeat=0,
        log_file=str(log_file),
        user_agent="EndpointRadar/0.1 (+authorized performance testing)",
        post_data=None,
        no_progress=True,
        dry_run=True,
        header=[],
    )

    asyncio.run(cli.run(args))

    output = capsys.readouterr().out
    assert "EndpointRadar discovery completed." in output
    assert "URLs discovered   : 2" in output
    assert "No latency scan was performed because --dry-run is enabled." in output
    assert "Top 3 Slowest Endpoints:" not in output
    assert len(log_file.read_text(encoding="utf-8").splitlines()) == 2


def test_progress_reporter_non_tty_writes_nothing() -> None:
    class FakeNonTTY:
        def __init__(self) -> None:
            self.output = ""

        def isatty(self) -> bool:
            return False

        def write(self, value: str) -> int:
            self.output += value
            return len(value)

        def flush(self) -> None:
            return None

    stream = FakeNonTTY()
    reporter = ProgressReporter(enabled=True, stream=stream)

    reporter.update_discovery(visited=1, queued=2, discovered=3, max_url=250, errors=0, force=True)
    reporter.update_scan(
        attempts_done=1,
        total_attempts=3,
        endpoints_done=1,
        total_endpoints=3,
        errors=0,
        rate_limit=5,
        force=True,
    )
    reporter.finish()

    assert stream.output == ""
