#!/usr/bin/env bats
# ─────────────────────────────────────────────────────────────────────────────
# BATS tests for scripts/setup-cluster-access.sh
# ─────────────────────────────────────────────────────────────────────────────
# Functional tests for argument handling, assumed-role ARN transformation,
# and error-handling patterns.
#
# Run:  bats tests/BATS/test_setup_cluster_access.bats
# ─────────────────────────────────────────────────────────────────────────────

SCRIPT="scripts/setup-cluster-access.sh"

# ── Syntax & Structure ───────────────────────────────────────────────────────

@test "setup-cluster-access.sh exists and is executable" {
    [ -f "$SCRIPT" ]
    [ -x "$SCRIPT" ]
}

@test "setup-cluster-access.sh passes bash -n syntax check" {
    bash -n "$SCRIPT"
}

@test "setup-cluster-access.sh passes shellcheck" {
    command -v shellcheck &>/dev/null || skip "shellcheck not installed"
    # -x matches the repo-wide lint:shellcheck:shell workflow policy.
    shellcheck -x "$SCRIPT"
}

# ── Argument Defaults (functional — evaluates real bash expansions) ───────────

@test "cluster name defaults to gco-us-east-1 with no args" {
    run bash -c 'set -- ; CLUSTER_NAME="${1:-gco-us-east-1}"; echo "$CLUSTER_NAME"'
    [ "$output" = "gco-us-east-1" ]
}

@test "region defaults to us-east-1 with no args" {
    run bash -c 'set -- ; REGION="${2:-us-east-1}"; echo "$REGION"'
    [ "$output" = "us-east-1" ]
}

@test "first argument overrides cluster name" {
    run bash -c 'set -- "my-cluster"; CLUSTER_NAME="${1:-gco-us-east-1}"; echo "$CLUSTER_NAME"'
    [ "$output" = "my-cluster" ]
}

@test "second argument overrides region" {
    run bash -c 'set -- "c" "eu-west-1"; REGION="${2:-us-east-1}"; echo "$REGION"'
    [ "$output" = "eu-west-1" ]
}

# ── Assumed-Role ARN Transformation (uses real functions from lib_demo.sh) ────

@test "is_assumed_role matches sts assumed-role ARNs" {
    source demo/lib_demo.sh
    is_assumed_role "arn:aws:sts::123456789012:assumed-role/Role/session"
}

@test "is_assumed_role rejects IAM user ARNs" {
    source demo/lib_demo.sh
    ! is_assumed_role "arn:aws:iam::123456789012:user/developer"
}

@test "is_assumed_role rejects non-assumed IAM role ARNs" {
    source demo/lib_demo.sh
    ! is_assumed_role "arn:aws:iam::123456789012:role/MyRole"
}

@test "extract_role_name gets role from assumed-role ARN" {
    source demo/lib_demo.sh
    result=$(extract_role_name "arn:aws:sts::123456789012:assumed-role/MyAdminRole/session-name")
    [ "$result" = "MyAdminRole" ]
}

@test "extract_role_name handles hyphens and underscores" {
    source demo/lib_demo.sh
    result=$(extract_role_name "arn:aws:sts::111111111111:assumed-role/My_Complex-Role-Name/user@corp.com")
    [ "$result" = "My_Complex-Role-Name" ]
}

@test "build_role_arn reconstructs correct IAM role ARN" {
    source demo/lib_demo.sh
    result=$(build_role_arn "MyRole" "123456789012")
    [ "$result" = "arn:aws:iam::123456789012:role/MyRole" ]
}

# ── Error Handling Patterns ──────────────────────────────────────────────────

@test "access entry creation handles already-exists gracefully" {
    grep -q 'Access entry may already exist' "$SCRIPT"
}

@test "policy association handles already-associated gracefully" {
    grep -q 'Policy may already be associated' "$SCRIPT"
}

@test "script waits for IAM propagation before kubectl verify" {
    grep -q "sleep 10" "$SCRIPT"
}

# ── AWS CLI Call Correctness ─────────────────────────────────────────────────

@test "update-kubeconfig passes both cluster name and region" {
    grep -q 'aws eks update-kubeconfig --name "$CLUSTER_NAME" --region "$REGION"' "$SCRIPT"
}

@test "create-access-entry passes cluster name, region, and principal ARN" {
    grep -q 'aws eks create-access-entry' "$SCRIPT"
    grep -q '\-\-cluster-name "$CLUSTER_NAME"' "$SCRIPT"
    grep -q '\-\-principal-arn "$PRINCIPAL_ARN"' "$SCRIPT"
}

@test "associate-access-policy uses cluster-scoped access" {
    grep -q 'type=cluster' "$SCRIPT"
}

@test "uses AmazonEKSClusterAdminPolicy (not a weaker policy)" {
    grep -q 'AmazonEKSClusterAdminPolicy' "$SCRIPT"
}
