from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse


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
