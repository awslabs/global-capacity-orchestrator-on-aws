#!/usr/bin/env bats
# ─────────────────────────────────────────────────────────────────────────────
# BATS tests for demo/record_destroy.sh
# ─────────────────────────────────────────────────────────────────────────────

SCRIPT="demo/record_destroy.sh"

@test "record_destroy.sh exists and is executable" {
    [ -f "$SCRIPT" ]
    [ -x "$SCRIPT" ]
}

@test "record_destroy.sh passes bash -n syntax check" {
    bash -n "$SCRIPT"
}

@test "record_destroy.sh passes shellcheck" {
    command -v shellcheck &>/dev/null || skip "shellcheck not installed"
    shellcheck -x "$SCRIPT"
}

@test "record_destroy.sh sources lib_demo.sh" {
    grep -q "source.*lib_demo.sh" "$SCRIPT"
}

@test "default speed is 10x for teardown" {
    run bash -c 'SPEED="${DEMO_SPEED:-10}"; echo "$SPEED"'
    [ "$output" = "10" ]
}

@test "default dimensions are 160x40" {
    grep -q 'COLS="${DEMO_COLS:-160}"' "$SCRIPT"
    grep -q 'ROWS="${DEMO_ROWS:-40}"' "$SCRIPT"
}

@test "output files go to demo/ directory" {
    grep -q 'CAST_FILE=.*destroy\.cast' "$SCRIPT"
    grep -q 'GIF_FILE=.*destroy\.gif' "$SCRIPT"
}

@test "runs gco stacks destroy-all -y" {
    grep -q "gco stacks destroy-all -y" "$SCRIPT"
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

@test "supports SKIP_SANITIZE env var" {
    # Documented escape hatch for bypassing account-ID redaction.
    grep -q "SKIP_SANITIZE" "$SCRIPT"
}

@test "supports SKIP_EMOJI_STRIP env var" {
    # Documented escape hatch for bypassing the emoji substitution pass.
    grep -q "SKIP_EMOJI_STRIP" "$SCRIPT"
}

@test "calls sanitize_cast before rendering the GIF" {
    # Ordering matters: the .cast must be redacted before agg reads it, so
    # both the committed cast and the derived gif have the account ID scrubbed.
    local sanitize_line render_line
    sanitize_line=$(grep -n 'sanitize_cast "\$CAST_FILE"' "$SCRIPT" | head -1 | cut -d: -f1)
    render_line=$(grep -n 'render_gif ' "$SCRIPT" | head -1 | cut -d: -f1)
    [ -n "$sanitize_line" ]
    [ -n "$render_line" ]
    [ "$sanitize_line" -lt "$render_line" ]
}

@test "strips tofu-triggering codepoints after sanitize, before render" {
    # Pipeline: sanitize_cast → strip_emoji_from_cast → render_gif.
    local sanitize_line strip_line render_line
    sanitize_line=$(grep -n 'sanitize_cast "\$CAST_FILE"' "$SCRIPT" | head -1 | cut -d: -f1)
    strip_line=$(grep -n 'strip_emoji_from_cast "\$CAST_FILE"' "$SCRIPT" | head -1 | cut -d: -f1)
    render_line=$(grep -n 'render_gif ' "$SCRIPT" | head -1 | cut -d: -f1)
    [ -n "$sanitize_line" ]
    [ -n "$strip_line" ]
    [ -n "$render_line" ]
    [ "$sanitize_line" -lt "$strip_line" ]
    [ "$strip_line" -lt "$render_line" ]
}

@test "calls render_gif with the standard positional args" {
    grep -q 'render_gif .*"\$CAST_FILE" .*"\$GIF_FILE" .*"\$SPEED" .*"\$THEME" .*"\$COLS" .*"\$ROWS"' "$SCRIPT"
}
