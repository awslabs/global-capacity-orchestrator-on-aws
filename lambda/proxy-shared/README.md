# Proxy Shared

Shared utility library used by the `api-gateway-proxy` and `regional-api-proxy` Lambda functions. Not deployed as a standalone Lambda.

## Contents

- `proxy_utils.py` — Thread-safe secret caching and HTTP forwarding with retry logic
- `__init__.py` — Package docstring

## Provided Utilities

### `get_secret_token()`
Retrieves the auth token from Secrets Manager with thread-safe TTL-based caching (default 5 minutes). Ensures rotated secrets are picked up without a cold start.

### `forward_request(target_url, http_method, headers, body, timeout)`
Forwards an HTTP request with exponential backoff retry for transient failures (502, 503, 504, 429). Uses a connection pool shared across invocations.

### `build_target_url(endpoint, path, query_params)`
Constructs the target URL from endpoint, path, and query parameters.

## Usage

The `proxy_utils.py` file is copied into each proxy Lambda's deployment package at build time. The proxy Lambdas import directly from `proxy_utils`.
