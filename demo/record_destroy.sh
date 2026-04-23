#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Record a GCO teardown as an animated GIF
# ─────────────────────────────────────────────────────────────────────────────
# Records `gco stacks destroy-all -y` from a clean state using asciinema,
# then converts to an animated GIF using agg.
#
# Output files (deposited in demo/):
#   demo/destroy.cast  — asciinema recording
#   demo/destroy.gif   — animated GIF for embedding in READMEs
#
# Prerequisites:
#   - asciinema: brew install asciinema
#   - agg:       brew install agg
#   - GCO CLI installed
#   - AWS credentials configured
#
# Usage:
#   bash demo/record_destroy.sh
#
# Options (via environment variables):
#   DEMO_COLS=120        Terminal width (default: 120)
#   DEMO_ROWS=37         Terminal height (default: 37)
#   DEMO_SPEED=10        Playback speed for GIF (default: 10 — deploy is long)
#   DEMO_THEME=monokai   agg color theme (default: monokai)
#   SKIP_GIF=1           Only produce the .cast file
#
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

# ── Configuration ────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# shellcheck source=demo/lib_demo.sh
source "${SCRIPT_DIR}/lib_demo.sh"
setup_colors

CAST_FILE="${SCRIPT_DIR}/destroy.cast"
GIF_FILE="${SCRIPT_DIR}/destroy.gif"

# Terminal dimensions (same as record_demo.sh)
COLS="${DEMO_COLS:-120}"
ROWS="${DEMO_ROWS:-37}"

# Deploy takes 20-30 minutes — 10x speed makes the GIF watchable (~2-3 min)
SPEED="${DEMO_SPEED:-10}"
THEME="${DEMO_THEME:-monokai}"

# ── Preflight ────────────────────────────────────────────────────────────────

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

echo "=== GCO Destroy Recorder ==="
echo ""
echo "  ${BOLD}Preflight Check${RESET}"
echo ""

# Check tools
if command -v asciinema &>/dev/null; then
    preflight_pass "asciinema installed"
else
    preflight_fail "asciinema not installed" "brew install asciinema"
fi

if [ "${SKIP_GIF:-}" != "1" ]; then
    if command -v agg &>/dev/null; then
        preflight_pass "agg installed"
    else
        preflight_warn "agg not installed — will produce .cast only" \
            "brew install agg"
        SKIP_GIF=1
    fi
fi

if command -v gco &>/dev/null; then
    preflight_pass "GCO CLI installed ($(gco --version 2>&1 | head -1))"
else
    preflight_fail "GCO CLI not installed" "pipx install -e ."
fi

if [ -f "${REPO_ROOT}/cdk.json" ]; then
    preflight_pass "cdk.json found"
else
    preflight_fail "cdk.json not found" "Run from repo root"
fi

# Check AWS credentials
if aws sts get-caller-identity &>/dev/null; then
    preflight_pass "AWS credentials configured"
else
    preflight_fail "AWS credentials not configured" "aws configure or aws sso login"
fi

# Check disk space
AVAILABLE_MB=$(df -m "${SCRIPT_DIR}" 2>/dev/null | awk 'NR==2{print $4}' || echo "0")
if [ "$AVAILABLE_MB" -gt 100 ]; then
    preflight_pass "Disk space: ${AVAILABLE_MB} MB available"
else
    preflight_warn "Low disk space: ${AVAILABLE_MB} MB" "Free up space"
fi

echo ""
echo "  ${DIM}──────────────────────────────────────────────────────────────${RESET}"
echo "  ${BOLD}Results:${RESET}  ${GREEN}${PREFLIGHT_PASS} passed${RESET}  ${RED}${PREFLIGHT_FAIL} failed${RESET}  ${YELLOW}${PREFLIGHT_WARN} warnings${RESET}"
echo "  ${DIM}──────────────────────────────────────────────────────────────${RESET}"

if [ "$PREFLIGHT_FAIL" -gt 0 ]; then
    echo ""
    echo "  ${RED}${BOLD}Fix the issues above before recording.${RESET}"
    exit 1
fi

# ── Record ───────────────────────────────────────────────────────────────────

echo ""
echo "Recording deploy (${COLS}x${ROWS})..."
echo "Output: ${CAST_FILE}"
echo ""
echo "  ${YELLOW}${BOLD}This will run gco stacks destroy-all -y${RESET}"
echo "  ${DIM}The destroy takes 10-20 minutes. The recording captures everything.${RESET}"
echo ""

rm -f "$CAST_FILE"

# Create a wrapper script so asciinema runs a single command without
# needing --env or shell features like && in --command.
WRAPPER=$(mktemp)
cat > "$WRAPPER" <<WRAPPER_SCRIPT
#!/usr/bin/env bash
set -euo pipefail
cd "$REPO_ROOT"
export COLUMNS=$COLS
gco stacks destroy-all -y
WRAPPER_SCRIPT
chmod +x "$WRAPPER"

export REPO_ROOT
asciinema rec \
    --cols "$COLS" \
    --rows "$ROWS" \
    --overwrite \
    --command "bash --norc --noprofile $WRAPPER" \
    "$CAST_FILE"

rm -f "$WRAPPER"

echo ""
echo "✓ Recording saved: ${CAST_FILE}"
echo "  Size: $(du -h "$CAST_FILE" | cut -f1)"

# ── Convert to GIF ──────────────────────────────────────────────────────────

if [ "${SKIP_GIF:-}" != "1" ]; then
    echo ""
    echo "Converting to GIF (speed=${SPEED}x, theme=${THEME})..."

    agg \
        --speed "$SPEED" \
        --theme "$THEME" \
        --font-family "Menlo,Monaco,Courier New" \
        --font-size 14 \
        --cols "$COLS" \
        --rows "$ROWS" \
        "$CAST_FILE" \
        "$GIF_FILE"

    echo "✓ GIF saved: ${GIF_FILE}"
    echo "  Size: $(du -h "$GIF_FILE" | cut -f1)"
fi

# ── Summary ──────────────────────────────────────────────────────────────────

echo ""
echo "=== Done ==="
echo ""
echo "Files:"
echo "  ${CAST_FILE}"
[ "${SKIP_GIF:-}" != "1" ] && echo "  ${GIF_FILE}"
echo ""
echo "To replay:       asciinema play ${CAST_FILE}"
echo "To re-gen GIF:   agg --speed ${SPEED} --theme ${THEME} ${CAST_FILE} ${GIF_FILE}"
echo ""
echo "Embed in README:"
echo '  ![GCO Deploy](demo/destroy.gif)'
