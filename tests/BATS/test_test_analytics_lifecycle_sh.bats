#!/usr/bin/env bats
# ─────────────────────────────────────────────────────────────────────────────
# BATS tests for scripts/test_analytics_lifecycle.sh
# ─────────────────────────────────────────────────────────────────────────────
#
# The script is a one-line bash wrapper around test_analytics_lifecycle.py.
# The tests here check the wrapper's contract rather than the Python logic
# underneath (that is covered by tests/test_analytics_lifecycle_script.py):
#
#   - The file exists, is executable, and passes bash -n / shellcheck.
#   - It sets strict mode (-euo pipefail) so pipe failures propagate.
#   - It runs under ``#!/usr/bin/env bash`` so it picks up the same
#     bash that the rest of the repo scripts use.
#   - The exec target is resolved relative to the script's own directory
#     so it works regardless of CWD.
#   - All positional args and environment are forwarded to Python verbatim.
#
# Run:  bats tests/BATS/test_test_analytics_lifecycle_sh.bats
# ─────────────────────────────────────────────────────────────────────────────

SCRIPT="scripts/test_analytics_lifecycle.sh"

# ── Syntax & Structure ───────────────────────────────────────────────────────

@test "test_analytics_lifecycle.sh exists and is executable" {
    [ -f "$SCRIPT" ]
    [ -x "$SCRIPT" ]
}

@test "test_analytics_lifecycle.sh passes bash -n syntax check" {
    bash -n "$SCRIPT"
}

@test "test_analytics_lifecycle.sh passes shellcheck" {
    command -v shellcheck &>/dev/null || skip "shellcheck not installed"
    shellcheck -x "$SCRIPT"
}

@test "uses /usr/bin/env bash shebang for portability" {
    first_line="$(head -1 "$SCRIPT")"
    [ "$first_line" = "#!/usr/bin/env bash" ]
}

@test "enables strict mode (set -euo pipefail)" {
    # Strict mode is the difference between a pipe failure surfacing as
    # exit != 0 and the operator never seeing a traceback. Without
    # pipefail, a Python script that dies mid-way through would be
    # masked by a successful exit from ``tee`` or similar.
    grep -qE '^set -euo pipefail' "$SCRIPT"
}

# ── Dispatch semantics ───────────────────────────────────────────────────────

@test "delegates to test_analytics_lifecycle.py via exec" {
    # ``exec`` (rather than plain invocation) replaces the shell process with
    # Python, so signal handling (SIGINT during a long deploy) works
    # correctly and the wrapper does not leave a stray bash process behind.
    grep -qE '^exec python3 ' "$SCRIPT"
}

@test "resolves the Python script relative to its own directory" {
    # Using ``dirname "$0"`` rather than a hardcoded relative path lets
    # the wrapper be invoked from any CWD. Without it,
    # ``make analytics-iterate`` from the repo root would break if
    # scripts/ were ever reorganised.
    grep -qF 'dirname "$0"' "$SCRIPT"
    grep -qF 'test_analytics_lifecycle.py' "$SCRIPT"
}

@test "forwards positional args to python via \"\$@\"" {
    # ``"$@"`` (double-quoted) preserves word boundaries; ``$@`` (unquoted)
    # would split on whitespace. Silent argument mangling would be very
    # hard to debug, so pin the right form.
    grep -qF '"$@"' "$SCRIPT"
}

@test "target Python script exists next to the wrapper" {
    [ -f "scripts/test_analytics_lifecycle.py" ]
}

# ── End-to-end smoke: dry-run delegation ─────────────────────────────────────

@test "wrapper --help exits 0 and emits Python argparse help" {
    # Round-trip check: the wrapper really does hand control to the Python
    # module's argparse, which prints a usage line starting with ``usage:``.
    # If the wrapper's exec line ever drifts (wrong path, missing quoting,
    # stale python3 reference), this surfaces immediately.
    run bash "$SCRIPT" --help
    [ "$status" -eq 0 ]
    [[ "$output" == *"usage:"* ]]
}

@test "wrapper exits non-zero on invalid phase (propagates Python exit)" {
    # The Python script exits 2 on argparse errors; the wrapper must pass
    # that through rather than swallowing it.
    run bash "$SCRIPT" not-a-real-phase
    [ "$status" -ne 0 ]
}
