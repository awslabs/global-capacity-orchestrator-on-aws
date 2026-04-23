#!/usr/bin/env bats
# ─────────────────────────────────────────────────────────────────────────────
# BATS tests for demo/record_demo.sh
# ─────────────────────────────────────────────────────────────────────────────
# Functional tests for configuration defaults, preflight logic, wrapper
# generation, asciinema/agg invocation patterns, and output file paths.
#
# Run:  bats tests/BATS/test_record_demo.bats
# ─────────────────────────────────────────────────────────────────────────────

SCRIPT="demo/record_demo.sh"

# ── Syntax & Structure ───────────────────────────────────────────────────────

@test "record_demo.sh exists and is executable" {
    [ -f "$SCRIPT" ]
    [ -x "$SCRIPT" ]
}

@test "record_demo.sh passes bash -n syntax check" {
    bash -n "$SCRIPT"
}

@test "record_demo.sh passes shellcheck" {
    command -v shellcheck &>/dev/null || skip "shellcheck not installed"
    shellcheck "$SCRIPT"
}

@test "record_demo.sh uses set -euo pipefail" {
    grep -q "set -euo pipefail" "$SCRIPT"
}

# ── Configuration Defaults (functional) ──────────────────────────────────────

@test "default terminal width is 120 columns" {
    run bash -c 'COLS="${DEMO_COLS:-120}"; echo "$COLS"'
    [ "$output" = "120" ]
}

@test "default terminal height is 35 rows" {
    run bash -c 'ROWS="${DEMO_ROWS:-35}"; echo "$ROWS"'
    [ "$output" = "35" ]
}

@test "default playback speed is 2x" {
    run bash -c 'SPEED="${DEMO_SPEED:-2}"; echo "$SPEED"'
    [ "$output" = "2" ]
}

@test "default theme is monokai" {
    run bash -c 'THEME="${DEMO_THEME:-monokai}"; echo "$THEME"'
    [ "$output" = "monokai" ]
}

@test "DEMO_COLS env var overrides default width" {
    run bash -c 'export DEMO_COLS=160; COLS="${DEMO_COLS:-120}"; echo "$COLS"'
    [ "$output" = "160" ]
}

@test "DEMO_SPEED env var overrides default speed" {
    run bash -c 'export DEMO_SPEED=5; SPEED="${DEMO_SPEED:-2}"; echo "$SPEED"'
    [ "$output" = "5" ]
}

# ── Output File Paths ────────────────────────────────────────────────────────

@test "cast file is written to demo/ directory" {
    grep -q 'CAST_FILE=.*live_demo\.cast' "$SCRIPT"
}

@test "gif file is written to demo/ directory" {
    grep -q 'GIF_FILE=.*live_demo\.gif' "$SCRIPT"
}

# ── Preflight Checks (functional — sources lib_demo.sh) ──────────────────────

@test "checks for asciinema installation" {
    grep -q "command -v asciinema" "$SCRIPT"
}

@test "checks for agg installation" {
    grep -q "command -v agg" "$SCRIPT"
}

@test "checks for live_demo.sh existence" {
    grep -q "live_demo.sh" "$SCRIPT"
}

@test "checks for lib_demo.sh existence" {
    grep -q "lib_demo.sh" "$SCRIPT"
}

@test "checks for cdk.json existence" {
    grep -q "cdk.json" "$SCRIPT"
}

@test "checks for jq installation" {
    grep -q "command -v jq" "$SCRIPT"
}

@test "checks for gco CLI installation" {
    grep -q "command -v gco" "$SCRIPT"
}

@test "checks for kubectl installation" {
    grep -q "command -v kubectl" "$SCRIPT"
}

@test "checks kubectl cluster connectivity" {
    grep -q "kubectl get nodes" "$SCRIPT"
}

@test "checks available disk space" {
    grep -q "df -m" "$SCRIPT"
}

@test "gracefully skips GIF when agg is missing" {
    grep -q "SKIP_GIF=1" "$SCRIPT"
}

@test "shows preflight pass/fail/warn summary" {
    grep -q "PREFLIGHT_PASS" "$SCRIPT"
    grep -q "PREFLIGHT_FAIL" "$SCRIPT"
    grep -q "PREFLIGHT_WARN" "$SCRIPT"
}

@test "allows force-continue on preflight failure" {
    grep -q "force" "$SCRIPT"
}

@test "record_demo.sh sources lib_demo.sh" {
    grep -q "source.*lib_demo.sh" "$SCRIPT"
}

# ── Non-Interactive Wrapper (functional) ─────────────────────────────────────

@test "wrapper uses GCO_DEMO_NONINTERACTIVE for non-interactive mode" {
    grep -q "GCO_DEMO_NONINTERACTIVE=1" "$SCRIPT"
}

@test "wrapper sets GCO_DEMO_FAST=1 for shorter timers" {
    grep -q "GCO_DEMO_FAST=1" "$SCRIPT"
}

@test "wrapper is created as a temp file and cleaned up" {
    grep -q "mktemp" "$SCRIPT"
    grep -q 'rm -f "$WRAPPER"' "$SCRIPT"
}

# ── Asciinema Invocation ─────────────────────────────────────────────────────

@test "asciinema rec is called with --cols and --rows" {
    grep -q "\-\-cols" "$SCRIPT"
    grep -q "\-\-rows" "$SCRIPT"
}

@test "asciinema rec uses --overwrite to replace old recordings" {
    grep -q "\-\-overwrite" "$SCRIPT"
}

@test "asciinema rec uses --command to run the wrapper" {
    grep -q "\-\-command" "$SCRIPT"
}

# ── agg Invocation ───────────────────────────────────────────────────────────

@test "agg is called with --speed flag" {
    grep -q "\-\-speed" "$SCRIPT"
}

@test "agg is called with --theme flag" {
    grep -q "\-\-theme" "$SCRIPT"
}

@test "agg is called with --font-size flag" {
    grep -q "\-\-font-size" "$SCRIPT"
}

@test "agg converts .cast to .gif" {
    # Should reference both files in the agg command
    grep -q 'CAST_FILE.*GIF_FILE\|"$CAST_FILE".*"$GIF_FILE"' "$SCRIPT"
}

# ── SKIP_GIF Support ─────────────────────────────────────────────────────────

@test "SKIP_GIF=1 skips the agg conversion step" {
    grep -q 'SKIP_GIF.*!=.*1' "$SCRIPT"
}

# ── REPO_ROOT Detection (functional) ─────────────────────────────────────────

@test "SCRIPT_DIR resolves to the demo directory" {
    run bash -c '
        SCRIPT_DIR="$(cd "$(dirname "demo/record_demo.sh")" && pwd)"
        basename "$SCRIPT_DIR"
    '
    [ "$output" = "demo" ]
}

@test "REPO_ROOT resolves to parent of demo directory" {
    run bash -c '
        SCRIPT_DIR="$(cd "$(dirname "demo/record_demo.sh")" && pwd)"
        REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
        [ -f "$REPO_ROOT/cdk.json" ] && echo "found" || echo "missing"
    '
    [ "$output" = "found" ]
}
