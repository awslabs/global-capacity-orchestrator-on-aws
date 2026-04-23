#!/usr/bin/env bats
# ─────────────────────────────────────────────────────────────────────────────
# BATS tests for demo/record_deploy.sh
# ─────────────────────────────────────────────────────────────────────────────

SCRIPT="demo/record_deploy.sh"

@test "record_deploy.sh exists and is executable" {
    [ -f "$SCRIPT" ]
    [ -x "$SCRIPT" ]
}

@test "record_deploy.sh passes bash -n syntax check" {
    bash -n "$SCRIPT"
}

@test "record_deploy.sh passes shellcheck" {
    command -v shellcheck &>/dev/null || skip "shellcheck not installed"
    shellcheck -x "$SCRIPT"
}

@test "record_deploy.sh sources lib_demo.sh" {
    grep -q "source.*lib_demo.sh" "$SCRIPT"
}

@test "default speed is 10x for long deploy" {
    run bash -c 'SPEED="${DEMO_SPEED:-10}"; echo "$SPEED"'
    [ "$output" = "10" ]
}

@test "default dimensions are 120x37" {
    grep -q 'COLS="${DEMO_COLS:-120}"' "$SCRIPT"
    grep -q 'ROWS="${DEMO_ROWS:-37}"' "$SCRIPT"
}

@test "output files go to demo/ directory" {
    grep -q 'CAST_FILE=.*deploy\.cast' "$SCRIPT"
    grep -q 'GIF_FILE=.*deploy\.gif' "$SCRIPT"
}

@test "runs gco stacks deploy-all -y" {
    grep -q "gco stacks deploy-all -y" "$SCRIPT"
}

@test "checks for asciinema installation" {
    grep -q "command -v asciinema" "$SCRIPT"
}

@test "checks for AWS credentials" {
    grep -q "aws sts get-caller-identity" "$SCRIPT"
}

@test "checks for GCO CLI" {
    grep -q "command -v gco" "$SCRIPT"
}

@test "creates a temp wrapper script for asciinema" {
    grep -q "mktemp" "$SCRIPT"
    grep -q 'rm -f "$WRAPPER"' "$SCRIPT"
}

@test "supports SKIP_GIF env var" {
    grep -q "SKIP_GIF" "$SCRIPT"
}
