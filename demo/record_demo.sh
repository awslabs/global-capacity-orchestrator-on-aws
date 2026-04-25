#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Record the GCO live demo as an animated GIF
# ─────────────────────────────────────────────────────────────────────────────
# This script uses asciinema to record a terminal session of live_demo.sh
# running non-interactively (pauses auto-advance), then converts the
# recording to an animated GIF using agg.
#
# Output files (deposited in demo/):
#   demo/live_demo.cast  — asciinema recording (lightweight JSON text)
#   demo/live_demo.gif   — animated GIF for embedding in READMEs
#
# Prerequisites:
#   - asciinema: brew install asciinema  (or pip install asciinema)
#   - agg:       brew install agg        (or cargo install agg)
#
# Usage:
#   bash demo/record_demo.sh
#
# Options (via environment variables):
#   DEMO_COLS=120        Terminal width for recording (default: 120)
#   DEMO_ROWS=35         Terminal height for recording (default: 35)
#   DEMO_SPEED=2         Playback speed multiplier for GIF (default: 2)
#   DEMO_THEME=monokai   agg color theme (default: monokai)
#   DEMO_FONT_FAMILY     agg font fallback chain (default: see lib_demo.sh —
#                        covers Menlo/Monaco + Apple/Noto Color Emoji +
#                        Symbola + DejaVu Sans Mono + Courier New)
#   SKIP_GIF=1           Only produce the .cast file, skip GIF conversion
#   SKIP_SANITIZE=1      Skip AWS-account-ID redaction (debugging only —
#                        default always sanitizes before GIF conversion)
#   SKIP_EMOJI_STRIP=1   Skip emoji substitution (debugging only — default
#                        strips tofu-triggering codepoints before GIF render)
#
# The recorded .cast is post-processed in two passes before the GIF is
# rendered:
#   1. sanitize_cast — every 12-digit sequence becomes 000000000000 so no
#      AWS account numbers leak into committed demo artifacts.
#   2. strip_emoji_from_cast — rewrites the five codepoints agg's text
#      engine can't render with Menlo (ℹ ✅ ✨ 📦 🚀) to safe monochrome
#      equivalents. See lib_demo.sh for the full mapping and rationale.
#
# See demo/LIVE_DEMO.md for full documentation.
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

# ── Configuration ────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Source the shared library for preflight helpers and feature detection.
# shellcheck source=demo/lib_demo.sh
source "${SCRIPT_DIR}/lib_demo.sh"
setup_colors

CAST_FILE="${SCRIPT_DIR}/live_demo.cast"
GIF_FILE="${SCRIPT_DIR}/live_demo.gif"

# Recording dimensions — 120 cols × 37 rows gives a wide, readable terminal.
COLS="${DEMO_COLS:-120}"
ROWS="${DEMO_ROWS:-37}"

# GIF playback speed — 2x is comfortable for watching; 1 is real-time.
SPEED="${DEMO_SPEED:-2}"

# agg theme — controls colors in the GIF output.
# Options: asciinema, dracula, monokai, solarized-dark, solarized-light
THEME="${DEMO_THEME:-monokai}"

# ── Preflight Checks ────────────────────────────────────────────────────────
# Validates everything needed before recording: tools, infrastructure,
# cluster access, and the demo script itself. Uses the same pass/fail
# format as live_demo.sh so the output is familiar.

PREFLIGHT_PASS=0
PREFLIGHT_FAIL=0
PREFLIGHT_WARN=0

preflight_pass() {
    echo "  ${GREEN}${BOLD}✓${RESET} $1"
    PREFLIGHT_PASS=$((PREFLIGHT_PASS + 1))
}

preflight_fail() {
    echo "  ${RED}${BOLD}✗${RESET} $1"
    echo "    ${DIM}Fix: $2${RESET}"
    PREFLIGHT_FAIL=$((PREFLIGHT_FAIL + 1))
}

preflight_warn() {
    echo "  ${YELLOW}${BOLD}!${RESET} $1"
    echo "    ${DIM}$2${RESET}"
    PREFLIGHT_WARN=$((PREFLIGHT_WARN + 1))
}

echo "=== GCO Demo Recorder ==="
echo ""
echo "  ${BOLD}Preflight Check${RESET}"
echo ""

# 1. asciinema installed (required — no recording without it)
if command -v asciinema &>/dev/null; then
    preflight_pass "asciinema installed ($(asciinema --version 2>&1 | head -1))"
else
    preflight_fail "asciinema not installed" \
        "brew install asciinema  (macOS) or  pip install asciinema  (Linux)"
fi

# 2. agg installed (optional — needed for GIF, gracefully skipped)
if [ "${SKIP_GIF:-}" != "1" ]; then
    if command -v agg &>/dev/null; then
        preflight_pass "agg installed ($(agg --version 2>&1 | head -1))"
    else
        preflight_warn "agg not installed — will produce .cast only (no GIF)" \
            "brew install agg  (macOS) or  cargo install agg  (Rust)"
        SKIP_GIF=1
    fi
else
    preflight_warn "GIF conversion skipped (SKIP_GIF=1)" \
        "Unset SKIP_GIF to generate the animated GIF."
fi

# 3. live_demo.sh exists
if [ -f "${SCRIPT_DIR}/live_demo.sh" ]; then
    preflight_pass "live_demo.sh found"
else
    preflight_fail "live_demo.sh not found" \
        "Ensure demo/live_demo.sh exists in the repo"
fi

# 4. lib_demo.sh exists
if [ -f "${SCRIPT_DIR}/lib_demo.sh" ]; then
    preflight_pass "lib_demo.sh found"
else
    preflight_fail "lib_demo.sh not found" \
        "Ensure demo/lib_demo.sh exists in the repo"
fi

# 5. cdk.json exists (needed by live_demo.sh for feature detection)
if [ -f "${REPO_ROOT}/cdk.json" ]; then
    preflight_pass "cdk.json found"
else
    preflight_fail "cdk.json not found" \
        "Run this script from the repo root"
fi

# 6. jq installed (needed by live_demo.sh for feature detection)
if command -v jq &>/dev/null; then
    preflight_pass "jq installed ($(jq --version 2>&1))"
else
    preflight_fail "jq not installed" \
        "brew install jq  (macOS) or  apt install jq  (Linux)"
fi

# 7. gco CLI installed (needed by live_demo.sh for cost/job commands)
if command -v gco &>/dev/null; then
    preflight_pass "GCO CLI installed ($(gco --version 2>&1 | head -1))"
else
    preflight_fail "GCO CLI not installed" \
        "pipx install -e .  (from repo root)"
fi

# 8. kubectl installed (needed by live_demo.sh for scheduler/storage demos)
if command -v kubectl &>/dev/null; then
    preflight_pass "kubectl installed"
else
    preflight_fail "kubectl not installed" \
        "https://kubernetes.io/docs/tasks/tools/"
fi

# 9. kubectl can reach the cluster
KUBECTL_TEST=$(kubectl get nodes --request-timeout=5s 2>&1 || true)
if echo "$KUBECTL_TEST" | grep -qiE "NAME|Ready|no resources found"; then
    preflight_pass "kubectl connected to cluster"
else
    preflight_warn "kubectl cannot reach the cluster" \
        "The recording will capture error output. Run ./scripts/setup-cluster-access.sh first."
fi

# 10. Disk space for output files
AVAILABLE_MB=$(df -m "${SCRIPT_DIR}" 2>/dev/null | awk 'NR==2{print $4}' || echo "0")
if [ "$AVAILABLE_MB" -gt 100 ]; then
    preflight_pass "Disk space: ${AVAILABLE_MB} MB available"
else
    preflight_warn "Low disk space: ${AVAILABLE_MB} MB available" \
        "GIF files can be 10-50 MB. Free up space if the conversion fails."
fi

# Summary
echo ""
echo "  ${DIM}──────────────────────────────────────────────────────────────${RESET}"
echo "  ${BOLD}Results:${RESET}  ${GREEN}${PREFLIGHT_PASS} passed${RESET}  ${RED}${PREFLIGHT_FAIL} failed${RESET}  ${YELLOW}${PREFLIGHT_WARN} warnings${RESET}"
echo "  ${DIM}──────────────────────────────────────────────────────────────${RESET}"

if [ "$PREFLIGHT_FAIL" -gt 0 ]; then
    echo ""
    echo "  ${RED}${BOLD}$PREFLIGHT_FAIL check(s) failed. Fix the issues above before recording.${RESET}"
    echo ""
    echo "  ${DIM}Press Enter to exit, or type 'force' to continue anyway:${RESET}"
    read -r force_input
    if [ "$force_input" != "force" ]; then
        exit 1
    fi
    echo "  ${YELLOW}${BOLD}⚠ Continuing despite failures — recording may contain errors.${RESET}"
fi

echo ""

# ── Create the Non-Interactive Wrapper ───────────────────────────────────────
# live_demo.sh uses "read -r" for pauses. We feed it newlines via a pipe
# so it advances automatically. We also set GCO_DEMO_FAST=1 for shorter
# countdown timers, and pre-answer "n" to the cleanup prompt at the end.

WRAPPER=$(mktemp)
cat > "$WRAPPER" <<'WRAPPER_SCRIPT'
#!/usr/bin/env bash
# Non-interactive wrapper: runs live_demo.sh without any stdin piping.
# GCO_DEMO_NONINTERACTIVE=1 makes pause_for_audience() skip read -r.
# --norc --noprofile prevents .bashrc/.bash_profile from interfering
# with the script under asciinema's PTY.
set -euo pipefail

cd "$REPO_ROOT"
export COLUMNS=120
export GCO_DEMO_FAST=1
export GCO_DEMO_NONINTERACTIVE=1
bash --norc --noprofile demo/live_demo.sh
WRAPPER_SCRIPT
chmod +x "$WRAPPER"

# ── Record ───────────────────────────────────────────────────────────────────

echo "Recording demo (${COLS}x${ROWS})..."
echo "Output: ${CAST_FILE}"
echo ""

# Remove old recording if it exists
rm -f "$CAST_FILE"

# Record the session.
# --cols/--rows set the virtual terminal size.
# --overwrite replaces any existing .cast file.
# --command runs our wrapper script instead of an interactive shell.
# REPO_ROOT is exported so the wrapper can cd into it.
export REPO_ROOT
export COLS
asciinema rec \
    --cols "$COLS" \
    --rows "$ROWS" \
    --overwrite \
    --command "bash --norc --noprofile $WRAPPER" \
    "$CAST_FILE"

# Clean up the temp wrapper
rm -f "$WRAPPER"

echo ""
echo "✓ Recording saved: ${CAST_FILE}"
CAST_SIZE=$(du -h "$CAST_FILE" | cut -f1); echo "  Size: $CAST_SIZE"

# ── Sanitize ────────────────────────────────────────────────────────────────
# Redact any AWS account numbers before anyone can view the cast or the GIF
# derived from it. See sanitize_cast() in lib_demo.sh for details.

sanitize_cast "$CAST_FILE"
echo "✓ Cast sanitized (AWS account IDs → 000000000000)"

# ── Strip tofu-triggering codepoints ────────────────────────────────────────
# Rewrite the handful of Unicode characters Menlo can't render so agg never
# falls back to the system's LastResort tofu font. See strip_emoji_from_cast()
# in lib_demo.sh for the substitution table.

strip_emoji_from_cast "$CAST_FILE"
echo "✓ Tofu-triggering codepoints stripped (ℹ→i, ✅→✓, ✨→*, 📦→[pkg], 🚀→>>)"

# ── Convert to GIF ──────────────────────────────────────────────────────────

if [ "${SKIP_GIF:-}" != "1" ]; then
    echo ""
    echo "Converting to GIF (speed=${SPEED}x, theme=${THEME})..."

    render_gif "$CAST_FILE" "$GIF_FILE" "$SPEED" "$THEME" "$COLS" "$ROWS"

    echo "✓ GIF saved: ${GIF_FILE}"
    GIF_SIZE=$(du -h "$GIF_FILE" | cut -f1); echo "  Size: $GIF_SIZE"
fi

# ── Summary ──────────────────────────────────────────────────────────────────

echo ""
echo "=== Done ==="
echo ""
echo "Files:"
echo "  ${CAST_FILE}"
[ "${SKIP_GIF:-}" != "1" ] && echo "  ${GIF_FILE}"
echo ""
echo "To replay in terminal:  asciinema play ${CAST_FILE}"
echo "To re-generate GIF:     re-run $0 with the existing cast (skips recording if cast is newer)"
echo ""
echo "Embed in README:"
echo '  ![GCO Live Demo](demo/live_demo.gif)'
