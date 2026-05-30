import pytest

from endpointradar import (
    aggregate_results,
    discover_from_html,
    is_dangerous_path,
    is_same_hostname,
    is_skippable_asset,
    normalize_target_url,
    normalize_url,
)


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
