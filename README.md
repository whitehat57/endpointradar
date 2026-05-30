# EndpointRadar

EndpointRadar is a small Python CLI tool for authorized website endpoint discovery and latency profiling.

It crawls one exact target hostname, discovers internal endpoints, optionally measures latency with configured HTTP methods, writes JSONL logs, and keeps terminal output concise.

## Safety Notice

Use EndpointRadar only on websites you own or have explicit permission to test.

EndpointRadar is for discovery and performance profiling only. It does not exploit, fuzz, brute-force, bypass authentication, bypass WAFs, submit discovered form fields, execute JavaScript, or use browser automation.

## Features

- Exact same-hostname scope only.
- No subdomain or external crawling.
- Async HTTP requests with `httpx`.
- HTML, JavaScript URL string, and sitemap discovery.
- GET scanning by default.
- Optional POST scanning only when explicitly enabled.
- JSONL logs for scan attempts.
- JSONL logs for discovery-only dry runs.
- Minimal stderr progress output during longer runs.
- Clean stdout summary output.

## Installation

Requires Python 3.10+.

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

On macOS or Linux:

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

For tests:

```bash
pip install -r requirements-dev.txt
```

## Usage

Basic GET-only scan:

```bash
python endpointradar.py https://example.com
```

If the scheme is omitted, EndpointRadar defaults to `https://`:

```bash
python endpointradar.py example.com
```

GET and explicitly enabled POST scan:

```bash
python endpointradar.py https://example.com --methods GET,POST --post-data '{}'
```

Discovery-only dry run:

```bash
python endpointradar.py https://example.com --dry-run
```

Suppress runtime progress:

```bash
python endpointradar.py https://example.com --no-progress
```

With custom headers:

```bash
python endpointradar.py https://example.com --header "Authorization: Bearer TOKEN" --header "Cookie: session=abc"
```

## CLI Options

```text
positional target              Target URL
--methods GET                  Comma-separated methods: GET, POST, or GET,POST
--depth 2                      Maximum crawl depth
--max-url 250                  Maximum discovered URLs to test
--concurrency 10               Maximum concurrent HTTP requests
--rate-limit 5                 Global maximum requests per second; 0 disables the limit
--timeout 15                   HTTP timeout in seconds
--repeat 3                     Request attempts per endpoint-method pair
--log-file PATH                Optional JSONL log file path
--user-agent VALUE             Custom User-Agent
--post-data VALUE              POST request body
--no-progress                  Suppress runtime progress output
--dry-run                      Run discovery only and skip latency scanning
--header "Name: Value"         Repeatable custom HTTP header
```

Default User-Agent:

```text
EndpointRadar/0.1 (+authorized performance testing)
```

POST is never used unless `--methods` includes `POST`. If POST is enabled and `--post-data` is omitted, the request body is `{}` and the default `Content-Type` is `application/json`.

## Scope Rules

EndpointRadar enforces exact hostname scope.

If the target is:

```text
https://example.com
```

only `example.com` is allowed. `www.example.com`, subdomains, sibling domains, and external hostnames are skipped.

If the target is:

```text
https://www.example.com
```

only `www.example.com` is allowed.

EndpointRadar does not auto-expand scope to root domains or subdomains.

## Discovery Sources

EndpointRadar discovers URLs from:

- The target homepage.
- Internal HTML `<a href="">` links.
- Internal `<form action="">` URLs.
- Internal `<script src="">` JavaScript files.
- Basic endpoint-like strings found inside fetched JavaScript files.
- `/sitemap.xml` when available.

JavaScript files are fetched only for endpoint extraction. EndpointRadar does not execute JavaScript.

## Scan Logs

Normal scans write raw request attempts to:

```text
logs/endpointradar-YYYYMMDD-HHMMSS.jsonl
```

Each request attempt is one JSON object line with:

- `target`
- `url`
- `method`
- `status_code`
- `elapsed_ms`
- `content_length`
- `content_type`
- `depth`
- `run_index`
- `error`
- `timestamp`

Errors are logged as JSONL records instead of stopping the whole scan.

## Dry-Run Logs

When `--dry-run` is enabled, EndpointRadar performs discovery only and writes discovered URLs to:

```text
logs/endpointradar-discovery-YYYYMMDD-HHMMSS.jsonl
```

Each discovered URL is one JSON object line with:

- `target`
- `url`
- `depth`
- `timestamp`

Dry-run mode does not run latency scans, does not use `--repeat`, does not rank slow endpoints, and does not print discovered URLs individually.

## Terminal Output

EndpointRadar keeps stdout minimal.

Normal scans print:

- Scan completed message.
- Target.
- URLs discovered.
- URLs tested.
- Total request attempts.
- Errors.
- Log file path.
- Top 3 slowest endpoints by average latency.

Dry runs print:

- Discovery completed message.
- Target.
- URLs discovered.
- Log file path.
- A note that no latency scan was performed.

Runtime progress is written to stderr. In interactive terminals it uses a single updating line. With non-interactive stderr, dynamic carriage-return progress output is suppressed.

## Project Structure

```text
endpointradar.py              CLI entrypoint
endpoint_radar/cli.py         argparse and orchestration
endpoint_radar/crawler.py     discovery and crawl flow
endpoint_radar/scanner.py     request execution, rate limiting, aggregation
endpoint_radar/parsers.py     HTML, JavaScript, and sitemap parsing
endpoint_radar/filters.py     scope checks, URL normalization, safety filters
endpoint_radar/logging_utils.py
                              JSONL writing and default log paths
endpoint_radar/progress.py    runtime progress output
endpoint_radar/utils.py       shared constants and data structures
tests/test_core.py            pytest coverage for core behavior
```

## Development

Run syntax checks:

```bash
python -m py_compile endpointradar.py endpoint_radar\__init__.py endpoint_radar\cli.py endpoint_radar\crawler.py endpoint_radar\scanner.py endpoint_radar\parsers.py endpoint_radar\filters.py endpoint_radar\logging_utils.py endpoint_radar\progress.py endpoint_radar\utils.py tests\test_core.py
```

Run tests:

```bash
python -m pytest
```

GitHub Actions runs the syntax check and pytest on Python 3.10, 3.11, and 3.12 for pushes and pull requests.

## Current MVP Limitations

- Exact same-hostname scope only.
- No subdomain scanning.
- No external crawling.
- No browser automation.
- No JavaScript execution.
- No authentication automation or login attempts.
- No form field submission.
- No vulnerability scanning, fuzzing, brute-forcing, or exploit checks.
- No CSV export, dashboard, database storage, or AI analysis.
- JavaScript endpoint extraction is basic string matching.
- Static assets are skipped as normal crawl targets; JavaScript files are fetched only to extract endpoint candidates.
