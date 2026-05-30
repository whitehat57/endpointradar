from __future__ import annotations

import re
from urllib.parse import urlparse
from xml.etree import ElementTree

from bs4 import BeautifulSoup

from endpoint_radar.filters import is_js_asset, is_same_hostname, is_skippable_asset, normalize_url


JS_ENDPOINT_RE = re.compile(
    r"""(?P<quote>["'`])(?P<url>(?:https?://[^"'`\s<>]+|/[^"'`\s<>]+))(?P=quote)""",
    re.IGNORECASE,
)


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


def discover_from_sitemap_text(text: str, sitemap_url: str, target_hostname: str) -> set[str]:
    endpoints: set[str] = set()
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        return endpoints

    for element in root.iter():
        if element.tag.lower().endswith("loc") and element.text:
            _add_candidate(endpoints, element.text.strip(), sitemap_url, target_hostname)
    return endpoints
