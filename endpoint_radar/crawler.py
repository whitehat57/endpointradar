from __future__ import annotations

from collections import deque
from urllib.parse import urlparse, urlunparse

import httpx

from endpoint_radar.filters import is_js_asset, is_skippable_asset
from endpoint_radar.parsers import discover_from_html, discover_from_js, discover_from_sitemap_text
from endpoint_radar.progress import ProgressReporter
from endpoint_radar.scanner import RateLimiter
from endpoint_radar.utils import DiscoveredURL


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
        if not any(
            kind in content_type.lower()
            for kind in ("text/html", "application/xhtml+xml", "javascript", "xml", "text/plain")
        ):
            return None
        return response.text
    except httpx.HTTPError:
        return None


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
    return discover_from_sitemap_text(text, sitemap_url, target_hostname)


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
