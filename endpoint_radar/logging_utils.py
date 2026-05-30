from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from endpoint_radar.utils import DiscoveredURL


async def write_jsonl(log_file: Path, record: dict[str, Any], lock: asyncio.Lock) -> None:
    line = json.dumps(record, sort_keys=True, separators=(",", ":"))
    async with lock:
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def default_log_file() -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path("logs") / f"endpointradar-{timestamp}.jsonl"


def default_discovery_log_file() -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path("logs") / f"endpointradar-discovery-{timestamp}.jsonl"


def write_discovery_jsonl(log_file: Path, target: str, discovered_urls: list[DiscoveredURL]) -> None:
    with log_file.open("a", encoding="utf-8") as handle:
        for discovered in discovered_urls:
            record = {
                "target": target,
                "url": discovered.url,
                "depth": discovered.depth,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
