import csv

from endpoint_radar.reporting import CSV_COLUMNS, write_csv_summary


def test_csv_writer_creates_parent_directory_and_headers(tmp_path) -> None:
    csv_file = tmp_path / "reports" / "result.csv"

    write_csv_summary(csv_file, [])

    assert csv_file.exists()
    with csv_file.open(encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        assert next(reader) == CSV_COLUMNS


def test_csv_rows_are_sorted_by_avg_descending_and_rank_starts_at_one(tmp_path) -> None:
    csv_file = tmp_path / "result.csv"
    write_csv_summary(
        csv_file,
        [
            {"url": "https://example.com/fast", "method": "GET", "avg_ms": 100, "error_count": 0},
            {"url": "https://example.com/slow", "method": "GET", "avg_ms": 2500, "error_count": 0},
        ],
    )

    rows = _read_csv(csv_file)

    assert rows[0]["rank"] == "1"
    assert rows[0]["url"] == "https://example.com/slow"
    assert rows[1]["rank"] == "2"
    assert rows[1]["url"] == "https://example.com/fast"


def test_csv_category_and_notes_are_computed(tmp_path) -> None:
    csv_file = tmp_path / "result.csv"
    write_csv_summary(
        csv_file,
        [
            {"url": "very_slow", "method": "GET", "avg_ms": 3000, "error_count": 0},
            {"url": "slow", "method": "GET", "avg_ms": 1000, "error_count": 0},
            {"url": "ok", "method": "GET", "avg_ms": 500, "error_count": 0},
            {"url": "fast", "method": "GET", "avg_ms": 499, "error_count": 0},
            {"url": "error", "method": "GET", "avg_ms": None, "error_count": 3, "attempt_count": 3},
        ],
    )

    by_url = {row["url"]: row for row in _read_csv(csv_file)}

    assert by_url["fast"]["category"] == "fast"
    assert by_url["fast"]["notes"] == "ok"
    assert by_url["ok"]["category"] == "ok"
    assert by_url["slow"]["category"] == "slow"
    assert by_url["slow"]["notes"] == "high latency"
    assert by_url["very_slow"]["category"] == "very_slow"
    assert by_url["very_slow"]["notes"] == "high latency"
    assert by_url["error"]["category"] == "error"
    assert by_url["error"]["notes"] == "all attempts failed"


def test_csv_handles_missing_optional_fields_without_crashing(tmp_path) -> None:
    csv_file = tmp_path / "result.csv"

    write_csv_summary(csv_file, [{"url": "https://example.com", "method": "GET", "error_count": 0}])

    row = _read_csv(csv_file)[0]
    assert row["avg_ms"] == ""
    assert row["min_ms"] == ""
    assert row["max_ms"] == ""
    assert row["status_codes"] == ""
    assert row["content_type"] == ""
    assert row["content_length"] == ""
    assert row["category"] == "error"


def test_csv_writes_status_codes_and_content_fields(tmp_path) -> None:
    csv_file = tmp_path / "result.csv"
    write_csv_summary(
        csv_file,
        [
            {
                "url": "https://example.com",
                "method": "GET",
                "avg_ms": 100,
                "min_ms": 90,
                "max_ms": 110,
                "error_count": 0,
                "status_codes": [200, 200, 500],
                "content_type": "text/html",
                "content_length": 1234,
            }
        ],
    )

    row = _read_csv(csv_file)[0]

    assert row["status_codes"] == "200,200,500"
    assert row["content_type"] == "text/html"
    assert row["content_length"] == "1234"


def _read_csv(path):
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))
