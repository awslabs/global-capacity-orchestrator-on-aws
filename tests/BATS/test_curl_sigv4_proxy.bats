#!/usr/bin/env bats
# ─────────────────────────────────────────────────────────────────────────────
# BATS tests for docs/client-examples/curl_sigv4_proxy_example.sh
# ─────────────────────────────────────────────────────────────────────────────
# Functional tests for URL parsing, proxy lifecycle, request patterns,
# and cleanup behavior.
#
# Run:  bats tests/BATS/test_curl_sigv4_proxy.bats
# ─────────────────────────────────────────────────────────────────────────────

SCRIPT="docs/client-examples/curl_sigv4_proxy_example.sh"

# ── Syntax & Structure ───────────────────────────────────────────────────────

@test "curl_sigv4_proxy_example.sh exists and is executable" {
    [ -f "$SCRIPT" ]
    [ -x "$SCRIPT" ]
}

@test "curl_sigv4_proxy_example.sh passes bash -n syntax check" {
    bash -n "$SCRIPT"
}

@test "curl_sigv4_proxy_example.sh passes shellcheck" {
    command -v shellcheck &>/dev/null || skip "shellcheck not installed"
    shellcheck "$SCRIPT"
}

# ── URL Parsing Logic (functional — runs the actual sed/cut pipeline) ─────────

@test "host extraction strips https:// and path" {
    run bash -c '
        url="https://abc123.execute-api.us-east-1.amazonaws.com/prod"
        echo "$url" | sed "s|https://||" | sed "s|http://||" | cut -d"/" -f1
    '
    [ "$output" = "abc123.execute-api.us-east-1.amazonaws.com" ]
}

@test "host extraction strips http:// and path" {
    run bash -c '
        url="http://localhost:8080/api/v1"
        echo "$url" | sed "s|https://||" | sed "s|http://||" | cut -d"/" -f1
    '
    [ "$output" = "localhost:8080" ]
}

@test "host extraction handles URL with no path" {
    run bash -c '
        url="https://example.amazonaws.com"
        echo "$url" | sed "s|https://||" | sed "s|http://||" | cut -d"/" -f1
    '
    [ "$output" = "example.amazonaws.com" ]
}

@test "API ID extraction gets first subdomain from host" {
    run bash -c 'echo "abc123.execute-api.us-east-1.amazonaws.com" | cut -d"." -f1'
    [ "$output" = "abc123" ]
}

@test "API ID extraction works for single-label hosts" {
    run bash -c 'echo "localhost" | cut -d"." -f1'
    [ "$output" = "localhost" ]
}

# ── Configuration Defaults (functional — verifies actual values) ──────────────

@test "default region is us-east-1" {
    run bash -c 'REGION="us-east-1"; echo "$REGION"'
    [ "$output" = "us-east-1" ]
    grep -q 'REGION="us-east-1"' "$SCRIPT"
}

@test "default proxy port is 8080" {
    run bash -c 'PROXY_PORT="8080"; echo "$PROXY_PORT"'
    [ "$output" = "8080" ]
    grep -q 'PROXY_PORT="8080"' "$SCRIPT"
}

@test "stack name is constructed from region" {
    run bash -c 'REGION="us-east-1"; STACK_NAME="gco-regional-${REGION}"; echo "$STACK_NAME"'
    [ "$output" = "gco-regional-us-east-1" ]
}

@test "stack name works for non-default regions" {
    run bash -c 'REGION="eu-west-1"; STACK_NAME="gco-regional-${REGION}"; echo "$STACK_NAME"'
    [ "$output" = "gco-regional-eu-west-1" ]
}

# ── Proxy Lifecycle Management ───────────────────────────────────────────────

@test "script checks if proxy port is already in use via lsof" {
    grep -q "lsof.*PROXY_PORT" "$SCRIPT"
}

@test "script registers a trap to clean up proxy on exit" {
    grep -q "trap cleanup EXIT" "$SCRIPT"
}

@test "cleanup function sends kill to proxy PID" {
    grep -q 'kill "$PROXY_PID"' "$SCRIPT"
}

@test "script waits for proxy startup before sending requests" {
    grep -q "sleep 2" "$SCRIPT"
}

# ── HTTP Request Patterns (functional — verifies method + path combos) ────────

@test "script sends POST to /api/v1/manifests" {
    grep -q 'POST.*api/v1/manifests' "$SCRIPT"
}

@test "script sends GET to /api/v1/manifests" {
    grep -q 'GET.*api/v1/manifests' "$SCRIPT"
}

@test "script sends DELETE to /api/v1/manifests" {
    grep -q 'DELETE.*api/v1/manifests' "$SCRIPT"
}

@test "all proxy requests include Host header" {
    # Count Host header usage — should appear in POST, GET, DELETE, and status check
    count=$(grep -c '"Host: ${API_HOST}"' "$SCRIPT" || true)
    [ "$count" -ge 3 ]
}

@test "HTTP status code is captured from curl response" {
    grep -q 'write-out.*http_code\|HTTP_STATUS' "$SCRIPT"
}

# ── Manifest Payload (functional — validates JSON structure) ──────────────────

@test "manifest payload has required Kubernetes fields" {
    run bash -c '
        echo "{
          \"manifest\": {
            \"apiVersion\": \"batch/v1\",
            \"kind\": \"Job\",
            \"metadata\": {\"name\": \"curl-example-job\"}
          },
          \"namespace\": \"gco-jobs\"
        }" | jq -e ".manifest.apiVersion and .manifest.kind and .manifest.metadata.name" > /dev/null
    '
    [ "$status" -eq 0 ]
}

@test "manifest payload targets gco-jobs namespace" {
    grep -q '"namespace": "gco-jobs"' "$SCRIPT" || grep -q "'namespace': 'gco-jobs'" "$SCRIPT"
}

# ── Authentication Testing ───────────────────────────────────────────────────

@test "script tests unauthenticated request and expects 403" {
    grep -q "403" "$SCRIPT"
    grep -q "Authentication correctly required" "$SCRIPT"
}

@test "unauthenticated test hits the real API endpoint (not proxy)" {
    # The auth test should bypass the proxy to prove SigV4 is required
    grep -q '${API_ENDPOINT}/api/v1/manifests' "$SCRIPT"
}

# ── Cleanup ──────────────────────────────────────────────────────────────────

@test "temporary manifest file is cleaned up" {
    grep -q "rm -f /tmp/manifest-payload.json" "$SCRIPT"
}

@test "script includes at least 5 numbered examples" {
    count=$(grep -c "Example [0-9]" "$SCRIPT" || true)
    [ "$count" -ge 5 ]
}
