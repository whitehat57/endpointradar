from __future__ import annotations

from dataclasses import dataclass
from http.cookies import SimpleCookie
from typing import Iterable, Mapping

import httpx


@dataclass(frozen=True)
class WAFDetectionResult:
    detected: bool
    vendor: str | None
    category: str
    confidence: str
    evidence: list[str]


CONFIDENCE_RANK = {"low": 1, "medium": 2, "high": 3}
BODY_HINTS = (
    "access denied",
    "attention required",
    "request blocked",
    "security check",
    "checking your browser",
    "captcha",
    "forbidden",
)


def analyze_waf_metadata(
    headers: Mapping[str, str],
    cookie_names: Iterable[str] = (),
    status_code: int | None = None,
    body: str = "",
) -> WAFDetectionResult:
    normalized_headers = {name.lower(): value.lower() for name, value in headers.items()}
    normalized_cookies = [name.lower() for name in cookie_names]
    candidates: list[WAFDetectionResult] = []

    cloudflare = _evidence(
        normalized_headers,
        exists={"cf-ray": "header: cf-ray", "cf-cache-status": "header: cf-cache-status"},
        contains={"server": ("cloudflare", "header: server=cloudflare")},
    )
    if cloudflare:
        candidates.append(WAFDetectionResult(True, "Cloudflare", "cdn_or_waf", "high", cloudflare))

    cloudfront = _evidence(
        normalized_headers,
        exists={"x-amz-cf-id": "header: x-amz-cf-id", "x-amz-cf-pop": "header: x-amz-cf-pop"},
        contains={
            "server": ("cloudfront", "header: server=cloudfront"),
            "via": ("cloudfront", "header: via contains cloudfront"),
        },
    )
    if cloudfront:
        candidates.append(WAFDetectionResult(True, "AWS CloudFront", "cdn", "medium", cloudfront))

    sucuri = _evidence(
        normalized_headers,
        exists={"x-sucuri-id": "header: x-sucuri-id", "x-sucuri-cache": "header: x-sucuri-cache"},
        contains={"server": ("sucuri", "header: server=sucuri")},
    )
    if sucuri:
        candidates.append(WAFDetectionResult(True, "Sucuri", "waf", "high", sucuri))

    akamai = _evidence(
        normalized_headers,
        exists={"akamai-grn": "header: akamai-grn", "x-akamai-transformed": "header: x-akamai-transformed"},
        contains={
            "server": ("akamai", "header: server=akamai"),
            "via": ("akamai", "header: via contains akamai"),
        },
    )
    if akamai:
        candidates.append(WAFDetectionResult(True, "Akamai", "cdn_or_waf", "medium", akamai))

    imperva = _evidence(normalized_headers, exists={"x-iinfo": "header: x-iinfo"})
    imperva.extend(_cookie_evidence(normalized_cookies, "incap_ses", "cookie: incap_ses"))
    imperva.extend(_cookie_evidence(normalized_cookies, "visid_incap", "cookie: visid_incap"))
    if imperva:
        candidates.append(WAFDetectionResult(True, "Imperva / Incapsula", "waf", "medium", imperva))

    fastly = _evidence(
        normalized_headers,
        exists={"fastly-debug-digest": "header: fastly-debug-digest"},
        contains={"x-served-by": ("cache", "header: x-served-by contains cache")},
    )
    if "x-cache" in normalized_headers and "varnish" in normalized_headers.get("via", ""):
        fastly.append("header: via contains varnish")
    if fastly:
        candidates.append(WAFDetectionResult(True, "Fastly", "cdn", "medium", fastly))

    generic = [
        evidence
        for header, evidence in (
            ("via", "header: via"),
            ("x-cache", "header: x-cache"),
            ("x-varnish", "header: x-varnish"),
        )
        if header in normalized_headers
    ]
    if generic:
        candidates.append(WAFDetectionResult(True, None, "reverse_proxy", "low", generic))

    if _has_body_hint(body):
        candidates.append(
            WAFDetectionResult(True, None, "waf", "low", ["body: generic block/challenge hint"])
        )

    if not candidates:
        return WAFDetectionResult(False, None, "unknown", "low", [])
    return max(candidates, key=lambda item: (CONFIDENCE_RANK[item.confidence], len(item.evidence)))


async def detect_waf(client: httpx.AsyncClient, target_url: str) -> WAFDetectionResult:
    try:
        response = await client.get(target_url)
    except httpx.HTTPError as exc:
        return WAFDetectionResult(False, None, "unknown", "low", [f"error: {exc.__class__.__name__}"])

    return analyze_waf_metadata(
        headers=response.headers,
        cookie_names=response.cookies.keys(),
        status_code=response.status_code,
        body=response.text[:4096],
    )


def cookie_names_from_header(cookie_header: str) -> list[str]:
    cookie = SimpleCookie()
    cookie.load(cookie_header)
    return list(cookie.keys())


def _evidence(
    headers: Mapping[str, str],
    exists: Mapping[str, str] | None = None,
    contains: Mapping[str, tuple[str, str]] | None = None,
) -> list[str]:
    found: list[str] = []
    for header, evidence in (exists or {}).items():
        if header in headers:
            found.append(evidence)
    for header, (needle, evidence) in (contains or {}).items():
        if needle in headers.get(header, ""):
            found.append(evidence)
    return found


def _cookie_evidence(cookie_names: Iterable[str], needle: str, evidence: str) -> list[str]:
    return [evidence] if any(needle in cookie_name for cookie_name in cookie_names) else []


def _has_body_hint(body: str) -> bool:
    snippet = body[:4096].lower()
    return any(hint in snippet for hint in BODY_HINTS)
