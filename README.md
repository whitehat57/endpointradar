# EndpointRadar

EndpointRadar is a small Python CLI tool for authorized website performance profiling. It crawls one target hostname, discovers internal endpoints, measures latency with configured HTTP methods, saves raw request attempts as JSONL, and prints only a concise summary plus the top 3 slowest endpoints.

## Safety Notice

Use EndpointRadar only on websites you own or have explicit permission to test. This MVP is for endpoint discovery and latency profiling only. It does not exploit, fuzz, brute-force, bypass authentication, bypass WAFs, submit discovered form fields, or run browser automation.

## Installation

Requires Python 3.10+.

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

On macOS or Linux, activate with:

```bash
source .venv/bin/activate
```

## Usage

Basic GET-only scan:

```bash
python endpointradar.py https://example.com
```

GET and explicitly enabled POST scan:

```bash
python endpointradar.py https://example.com --methods GET,POST --post-data '{}'
```

With custom headers:

```bash
python endpointradar.py https://example.com --header "Authorization: Bearer TOKEN" --header "Cookie: session=abc"
```

If the target scheme is missing, EndpointRadar defaults to `https://`.

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
--header "Name: Value"         Repeatable custom HTTP header
```

POST is never used unless `--methods` includes `POST`. If POST is enabled and `--post-data` is omitted, the request body is `{}` and the default `Content-Type` is `application/json`.

## Scope and Discovery

EndpointRadar enforces exact hostname scope. If the target is `https://example.com`, only `example.com` is allowed. `www.example.com`, subdomains, and external hostnames are skipped. If the target is `https://www.example.com`, only `www.example.com` is allowed.

The MVP discovers URLs from:

- The target homepage.
- Internal HTML `<a href="">` links.
- Internal `<form action="">` URLs.
- Internal `<script src="">` JavaScript files.
- Basic string extraction from fetched JavaScript files.
- `/sitemap.xml` when available.

EndpointRadar does not execute JavaScript and does not use browser automation.

## JSONL Logs

Raw logs are written to `logs/endpointradar-YYYYMMDD-HHMMSS.jsonl` by default. The logs directory is created automatically.

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

Errors are written to the JSONL log instead of stopping the whole scan.

## Smoke Test

Run:

```bash
python endpointradar.py https://example.com
```

Expected behavior:

- A JSONL file is created under `logs/`.
- The terminal prints only the completion message, scan summary, and top 3 slowest endpoints.
- It does not print every scanned URL.

You can also verify POST argument handling with:

```bash
python endpointradar.py https://example.com --methods GET,POST --post-data '{}'
```

## Current MVP Limitations

- Exact same-hostname scope only; no subdomain or external crawling.
- No browser automation and no JavaScript execution.
- No authentication automation or login attempts.
- No form field submission.
- No vulnerability scanning, fuzzing, brute-forcing, or exploit checks.
- JavaScript endpoint extraction is basic string matching.
- Static assets are skipped as normal crawl targets; JavaScript files are fetched only to extract endpoint candidates.
