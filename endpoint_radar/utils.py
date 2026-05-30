from __future__ import annotations

from dataclasses import dataclass


DEFAULT_USER_AGENT = "EndpointRadar/0.1 (+authorized performance testing)"


@dataclass(frozen=True)
class DiscoveredURL:
    url: str
    depth: int
