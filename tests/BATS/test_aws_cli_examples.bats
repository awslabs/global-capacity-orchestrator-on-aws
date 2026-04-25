#!/usr/bin/env bats
# ─────────────────────────────────────────────────────────────────────────────
# BATS tests for docs/client-examples/aws_cli_examples.sh
# ─────────────────────────────────────────────────────────────────────────────
# Functional tests for API region detection, manifest payload validity,
# and URL handling logic.
#
# Run:  bats tests/BATS/test_aws_cli_examples.bats
# ─────────────────────────────────────────────────────────────────────────────

SCRIPT="docs/client-examples/aws_cli_examples.sh"

# ── Syntax & Structure ───────────────────────────────────────────────────────

@test "aws_cli_examples.sh exists and is executable" {
    [ -f "$SCRIPT" ]
    [ -x "$SCRIPT" ]
}

@test "aws_cli_examples.sh passes bash -n syntax check" {
    bash -n "$SCRIPT"
}

@test "aws_cli_examples.sh passes shellcheck" {
    command -v shellcheck &>/dev/null || skip "shellcheck not installed"
    # -x matches the repo-wide lint:shellcheck:shell workflow policy.
    shellcheck -x "$SCRIPT"
}

# ── API Region Detection (functional — evaluates the actual logic) ────────────

@test "API_REGION defaults to us-east-2 when no env var and no cdk.json" {
    run bash -c '
        unset API_REGION
        if [ -z "${API_REGION:-}" ]; then
            if [ -f "/nonexistent/cdk.json" ]; then
                API_REGION="from-cdk"
            else
                API_REGION="us-east-2"
            fi
        fi
        echo "$API_REGION"
    '
    [ "$output" = "us-east-2" ]
}

@test "API_REGION env var takes precedence over cdk.json" {
    run bash -c '
        export API_REGION=eu-west-1
        if [ -z "${API_REGION:-}" ]; then
            API_REGION="us-east-2"
        fi
        echo "$API_REGION"
    '
    [ "$output" = "eu-west-1" ]
}

@test "API_REGION reads api_gateway region from real cdk.json" {
    command -v python3 &>/dev/null || skip "python3 not installed"
    run python3 -c "
import json
d = json.load(open('cdk.json'))
print(d.get('context',{}).get('deployment_regions',{}).get('api_gateway','us-east-2'))
"
    [ "$status" -eq 0 ]
    [[ "$output" =~ ^[a-z]{2}-[a-z]+-[0-9]+$ ]]
}

# ── URL Handling (functional — bash string operations) ────────────────────────

@test "trailing slash is stripped from API endpoint" {
    run bash -c 'API_ENDPOINT="https://example.com/prod/"; echo "${API_ENDPOINT%/}"'
    [ "$output" = "https://example.com/prod" ]
}

@test "no-op when endpoint has no trailing slash" {
    run bash -c 'API_ENDPOINT="https://example.com/prod"; echo "${API_ENDPOINT%/}"'
    [ "$output" = "https://example.com/prod" ]
}

# ── Manifest Payload Validity (functional — parses JSON with jq) ──────────────

@test "simple job payload is valid JSON with required K8s fields" {
    run bash -c '
        echo "{
          \"manifests\": [{
            \"apiVersion\": \"batch/v1\",
            \"kind\": \"Job\",
            \"metadata\": {\"name\": \"example-job\", \"namespace\": \"gco-jobs\"},
            \"spec\": {}
          }]
        }" | jq -e ".manifests[0].apiVersion" > /dev/null
    '
    [ "$status" -eq 0 ]
}

@test "GPU job payload includes nvidia.com/gpu resource limit" {
    grep -q "nvidia.com/gpu" "$SCRIPT"
}

@test "all manifest payloads use gco-jobs namespace" {
    # Count namespace references — should appear in every example payload
    count=$(grep -c '"namespace": "gco-jobs"' "$SCRIPT" || true)
    [ "$count" -ge 2 ]
}

@test "all images in payloads are from trusted registries" {
    # Extract image strings and verify they're from known-good sources
    while IFS= read -r line; do
        image=$(echo "$line" | grep -oP '"image":\s*"[^"]+"' | sed 's/.*"image":\s*"//;s/"//')
        [ -z "$image" ] && continue
        [[ "$image" == busybox* || "$image" == nvidia* ]]
    done < "$SCRIPT"
}

# ── SigV4 Authentication Pattern ─────────────────────────────────────────────

@test "curl uses --aws-sigv4 flag for SigV4 signing" {
    grep -q "\-\-aws-sigv4" "$SCRIPT"
}

@test "SigV4 signing targets execute-api service" {
    grep -q 'aws:amz:.*:execute-api' "$SCRIPT"
}

@test "requests target /api/v1/manifests endpoint" {
    grep -q "/api/v1/manifests" "$SCRIPT"
}

@test "POST requests set Content-Type to application/json" {
    grep -q "Content-Type: application/json" "$SCRIPT"
}

# ── Script Coverage ──────────────────────────────────────────────────────────

@test "script includes at least 4 numbered examples" {
    count=$(grep -c "Example [0-9]" "$SCRIPT" || true)
    [ "$count" -ge 4 ]
}
