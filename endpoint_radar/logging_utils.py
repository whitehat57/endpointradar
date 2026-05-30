from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any


async def write_jsonl(log_file: Path, record: dict[str, Any], lock: asyncio.Lock) -> None:
    line = json.dumps(record, sort_keys=True, separators=(",", ":"))
    async with lock:
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def default_log_file() -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path("logs") / f"endpointradar-{timestamp}.jsonl"
