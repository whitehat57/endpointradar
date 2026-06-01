from __future__ import annotations

import csv
from pathlib import Path
from typing import Any


CSV_COLUMNS = [
    "rank",
    "url",
    "method",
    "avg_ms",
    "min_ms",
    "max_ms",
    "error_count",
    "status_codes",
    "content_type",
    "content_length",
    "category",
    "notes",
]


def write_csv_summary(path: str | Path, aggregated_results: list[dict[str, Any]]) -> None:
    csv_path = Path(path)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(
        aggregated_results,
        key=lambda item: item["avg_ms"] if isinstance(item.get("avg_ms"), int) else -1,
        reverse=True,
    )

    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for rank, item in enumerate(rows, start=1):
            category = csv_category(item)
            writer.writerow(
                {
                    "rank": rank,
                    "url": item.get("url", ""),
                    "method": item.get("method", ""),
                    "avg_ms": _value_or_empty(item.get("avg_ms")),
                    "min_ms": _value_or_empty(item.get("min_ms")),
                    "max_ms": _value_or_empty(item.get("max_ms")),
                    "error_count": item.get("error_count", 0),
                    "status_codes": ",".join(str(code) for code in item.get("status_codes", [])),
                    "content_type": item.get("content_type") or "",
                    "content_length": _value_or_empty(item.get("content_length", item.get("size"))),
                    "category": category,
                    "notes": csv_notes(category),
                }
            )


def csv_category(item: dict[str, Any]) -> str:
    avg_ms = item.get("avg_ms")
    error_count = int(item.get("error_count", 0) or 0)
    attempt_count = item.get("attempt_count")
    if not isinstance(avg_ms, int) or (isinstance(attempt_count, int) and attempt_count > 0 and error_count >= attempt_count):
        return "error"
    if avg_ms < 500:
        return "fast"
    if avg_ms < 1000:
        return "ok"
    if avg_ms < 3000:
        return "slow"
    return "very_slow"


def csv_notes(category: str) -> str:
    if category == "error":
        return "all attempts failed"
    if category in {"slow", "very_slow"}:
        return "high latency"
    return "ok"


def _value_or_empty(value: Any) -> Any:
    return "" if value is None else value
