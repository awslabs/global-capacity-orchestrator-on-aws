#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Shared library for GCO demo scripts
# ─────────────────────────────────────────────────────────────────────────────
# Sourced by live_demo.sh and record_demo.sh. Also sourced by BATS tests
# so the tests exercise the real functions, not duplicated copies.
#
# Usage:
#   source demo/lib_demo.sh
#
# shellcheck disable=SC2034  # Variables are used by sourcing scripts
# ─────────────────────────────────────────────────────────────────────────────

# ── Colors & Formatting ─────────────────────────────────────────────────────
# Uses tput for portability. Falls back to empty strings when there's no
# terminal or tput isn't available.

setup_colors() {
    if [ -t 1 ] && command -v tput &>/dev/null && [ "${TERM:-dumb}" != "dumb" ]; then
        BOLD=$(tput bold)
        DIM=$(tput dim)
        RESET=$(tput sgr0)
        CYAN=$(tput setaf 6)
        GREEN=$(tput setaf 2)
        YELLOW=$(tput setaf 3)
        MAGENTA=$(tput setaf 5)
        BLUE=$(tput setaf 4)
        WHITE=$(tput setaf 7)
        RED=$(tput setaf 1)
        BG_BLUE=$(tput setab 4)
    else
        BOLD="" DIM="" RESET="" CYAN="" GREEN="" YELLOW=""
        MAGENTA="" BLUE="" WHITE="" RED="" BG_BLUE=""
    fi
}

# ── Pause Durations ─────────────────────────────────────────────────────────
# GCO_DEMO_FAST=1 shortens pauses for rehearsal or recording.

setup_pauses() {
    PAUSE_SHORT="${GCO_DEMO_FAST:+1}"
    PAUSE_SHORT="${PAUSE_SHORT:-3}"
    PAUSE_LONG="${GCO_DEMO_FAST:+2}"
    PAUSE_LONG="${PAUSE_LONG:-5}"
}

# ── Display Helpers ──────────────────────────────────────────────────────────

banner() {
    # Use terminal width if available, otherwise default to 72.
    # This ensures the banner fills the recording frame nicely.
    local width
    width=$(tput cols 2>/dev/null || echo "72")
    # Cap at 120 to avoid absurdly wide banners on ultrawide terminals
    if [ "$width" -gt 120 ]; then width=120; fi
    local text="$1"
    local text_len=${#text}
    local pad_left=$(( (width - text_len) / 2 ))
    local pad_right=$(( width - text_len - pad_left ))
    echo ""
    printf "%s%s%s%*s%s\n" "$BG_BLUE" "$WHITE" "$BOLD" "$width" "" "$RESET"
    printf "%s%s%s%*s%s%*s%s\n" "$BG_BLUE" "$WHITE" "$BOLD" "$pad_left" "" "$text" "$pad_right" "" "$RESET"
    printf "%s%s%s%*s%s\n" "$BG_BLUE" "$WHITE" "$BOLD" "$width" "" "$RESET"
    echo ""
}

section_header() {
    local num="$1"
    local title="$2"
    local color="${3:-$CYAN}"
    # Build a divider line that fills the terminal width (capped at 120)
    local width
    width=$(tput cols 2>/dev/null || echo "72")
    if [ "$width" -gt 120 ]; then width=120; fi
    local divider
    divider=$(printf '%*s' "$width" '' | tr ' ' '━')
    echo ""
    echo "${color}${BOLD}${divider}${RESET}"
    echo "${color}${BOLD}  [$num]  $title${RESET}"
    echo "${color}${BOLD}${divider}${RESET}"
    echo ""
}

narrate()   { echo "  ${DIM}$1${RESET}"; }
highlight() { echo "  ${YELLOW}${BOLD}▸ $1${RESET}"; }
success()   { echo "  ${GREEN}${BOLD}✓ $1${RESET}"; }
warn()      { echo "  ${RED}${BOLD}⚠ $1${RESET}"; }
spacer()    { echo ""; }

feature_status() {
    local value="$1"
    if [ "$value" = "true" ]; then
        echo "${GREEN}enabled${RESET}"
    else
        echo "${DIM}disabled${RESET}"
    fi
}

run_cmd() {
    echo ""
    echo "  ${MAGENTA}\$ ${WHITE}${BOLD}$1${RESET}"
    echo "  ${DIM}────────────────────────────────────────────────────────────${RESET}"
    eval "$1" 2>&1 | sed 's/^/  /'
    local exit_code=${PIPESTATUS[0]}
    echo "  ${DIM}────────────────────────────────────────────────────────────${RESET}"
    if [ "$exit_code" -ne 0 ]; then
        warn "Command exited with code $exit_code"
    fi
    return "$exit_code"
}

pause_for_audience() {
    if [ "${GCO_DEMO_NONINTERACTIVE:-}" = "1" ]; then
        sleep 1
        return
    fi
    echo ""
    echo "  ${DIM}Press Enter to continue...${RESET}"
    read -r
}

countdown() {
    local msg="$1"
    local secs="$2"
    for i in $(seq "$secs" -1 1); do
        printf "\r  %s%s %d...%s" "$DIM" "$msg" "$i" "$RESET"
        sleep 1
    done
    printf "\r  %s%-60s%s\n" "$DIM" "$msg done." "$RESET"
}

# wait_for_job <job-name> <namespace> [timeout_seconds]
#
# Waits for a Kubernetes Job to reach the ``complete`` condition, showing a
# live-updating progress indicator until it succeeds, fails, or times out.
# Designed for the live demo where we want the audience to actually see the
# job's final logs — a fixed-duration ``sleep`` used to fall short when
# image pulls or node provisioning pushed completion past the window.
#
# Arguments:
#   job-name         Name of the ``batch/v1`` Job resource.
#   namespace        Namespace containing the job.
#   timeout_seconds  Optional wall-clock budget (default: 240). This is a
#                    deadline, not a target — if the job finishes sooner
#                    we return immediately. The budget deliberately does
#                    not shrink in ``GCO_DEMO_FAST=1`` mode: that flag is
#                    for narration pauses, not real work.
#
# The helper *always* returns 0, even on timeout or failure. Callers are
# running under ``set -euo pipefail`` and we don't want a slow job to kill
# the entire recording mid-demo — the next ``kubectl logs`` / ``kubectl
# get`` call will surface the state naturally. On timeout we print the pod
# status so the next narration makes sense instead of showing a blank log
# block.
wait_for_job() {
    local job="$1"
    local ns="$2"
    local budget="${3:-240}"
    # NOTE: GCO_DEMO_FAST is for narration pauses, not for real work. Jobs
    # still need as long as they need. If the caller explicitly passes a
    # smaller budget via $3, that wins.

    local start=$SECONDS
    local deadline=$((start + budget))

    # First tick: the Job resource itself may not have appeared in the API
    # yet (submit-direct returns before the apply is persisted across the
    # control plane on a cold cluster). Spin briefly until it does.
    while [ "$SECONDS" -lt "$deadline" ]; do
        if kubectl get "job/${job}" -n "$ns" >/dev/null 2>&1; then
            break
        fi
        printf "\r  %sWaiting for job/%s to register...%s" "$DIM" "$job" "$RESET"
        sleep 1
    done

    # Use kubectl's own wait primitive for the remainder of the budget. It
    # returns immediately once the condition is met, so this is both faster
    # than polling and more accurate than a fixed sleep.
    local remaining=$((deadline - SECONDS))
    if [ "$remaining" -lt 5 ]; then remaining=5; fi

    printf "\r  %sWaiting for job/%s to complete (up to %ds)...%s\n" \
        "$DIM" "$job" "$remaining" "$RESET"

    if kubectl wait --for=condition=complete "job/${job}" \
            -n "$ns" --timeout="${remaining}s" >/dev/null 2>&1; then
        local elapsed=$((SECONDS - start))
        printf "  %s${GREEN}${BOLD}✓${RESET} %sjob/%s completed in %ds%s\n" \
            "" "$DIM" "$job" "$elapsed" "$RESET"
        return 0
    fi

    # Timed out or job failed — show what the pod is doing so the audience
    # sees meaningful context before we hit ``kubectl logs`` on a non-ready
    # pod. We always return 0 so ``set -e`` callers don't die on a slow job.
    printf "  %s${YELLOW}${BOLD}!${RESET} %sjob/%s still running after %ds — showing latest pod status%s\n" \
        "" "$DIM" "$job" "$budget" "$RESET"
    kubectl get pods -n "$ns" \
        -l "job-name=${job}" --no-headers 2>/dev/null | sed 's/^/    /' || true
    return 0
}

# ── Feature Detection ────────────────────────────────────────────────────────
# Reads cdk.json and sets global variables for each feature flag.
# Requires jq and CDK_JSON to be set.

detect_features() {
    local cdk="${1:-cdk.json}"
    VOLCANO_ENABLED=$(jq -r '.context.helm.volcano.enabled // false' "$cdk")
    KUEUE_ENABLED=$(jq -r '.context.helm.kueue.enabled // false' "$cdk")
    YUNIKORN_ENABLED=$(jq -r '.context.helm.yunikorn.enabled // false' "$cdk")
    SLURM_ENABLED=$(jq -r '.context.helm.slurm.enabled // false' "$cdk")
    FSX_ENABLED=$(jq -r '.context.fsx_lustre.enabled // false' "$cdk")
    VALKEY_ENABLED=$(jq -r '.context.valkey.enabled // false' "$cdk")
}

detect_region() {
    local cdk="${1:-cdk.json}"
    REGION="${GCO_DEMO_REGION:-$(jq -r '.context.deployment_regions.regional[0] // "us-east-1"' "$cdk")}"
}

detect_endpoint_access() {
    local cdk="${1:-cdk.json}"
    ENDPOINT_ACCESS=$(jq -r '.context.eks_cluster.endpoint_access // "PRIVATE"' "$cdk")
}

# ── Section Counter ──────────────────────────────────────────────────────────

# Section counter — can't use $(next_section) because command substitution
# runs in a subshell and the counter increment is lost. Instead we increment
# inline and use the variable directly.
SECTION=0

# ── ARN Helpers (shared with setup-cluster-access.sh) ────────────────────────

# Checks if an ARN is an assumed-role ARN.
is_assumed_role() {
    [[ "$1" == *":assumed-role/"* ]]
}

# Extracts the role name from an assumed-role ARN.
# Input:  arn:aws:sts::123456789012:assumed-role/MyRole/session-name
# Output: MyRole
extract_role_name() {
    echo "$1" | sed 's/.*:assumed-role\/\([^\/]*\)\/.*/\1/'
}

# Reconstructs an IAM role ARN from an assumed-role ARN and account ID.
# Input:  role_name, account_id
# Output: arn:aws:iam::123456789012:role/MyRole
build_role_arn() {
    local role_name="$1"
    local account_id="$2"
    echo "arn:aws:iam::${account_id}:role/${role_name}"
}

# ── Recording Helpers ────────────────────────────────────────────────────────

# Default font family used when rendering .cast files to GIFs with agg.
#
# agg's text renderer (resvg/usvg) is first-family-wins — it does not do
# per-glyph fallback down the family list like a GUI text engine would. So
# Menlo is kept first because it covers the characters our scripts emit
# (box-drawing, arrows, geometric shapes, and the dingbats ✓ ✗ ⚠ ▸). Any
# codepoint Menlo doesn't have (typically color-emoji pictographs from CDK
# output like ✨ and ✅, or the information symbol ℹ) would otherwise fall
# through to ``.LastResort`` and render as a tofu box.
#
# We fix that upstream instead of with more font fallbacks: every cast file
# runs through ``strip_emoji_from_cast`` before ``render_gif``, which maps
# the known tofu-triggering codepoints to safe monochrome equivalents.
# After that pass, Menlo covers every character in the cast and agg never
# needs to fall back.
#
# Override via the DEMO_FONT_FAMILY environment variable if you need to
# skip this substitution and use a font that has real coverage of those
# codepoints (e.g. a full Unicode monospace font).
DEMO_FONT_FAMILY_DEFAULT="Menlo,Monaco,Courier New"

# sanitize_cast <cast_file>
#
# Redacts AWS account numbers from an asciinema .cast recording by replacing
# any 12-digit sequence with 000000000000. Operates in place.
#
# This runs automatically from every record_*.sh script so recorded demos
# never leak account IDs, even when the AWS outputs (ARNs, ECR URIs, SQS
# URLs, CloudFormation stack ARNs) would otherwise embed them.
#
# The 12-digit-sequence heuristic is intentionally broad: it also redacts
# any other 12-digit numbers in the output (which in practice only come up
# as account IDs inside ARNs and URIs — timestamps, job counts, and resource
# limits never hit 12 contiguous digits).
#
# Use SKIP_SANITIZE=1 to bypass (useful for debugging a recording that's not
# playing back correctly).
sanitize_cast() {
    local cast_file="$1"
    if [ "${SKIP_SANITIZE:-}" = "1" ]; then
        return
    fi
    if [ ! -f "$cast_file" ]; then
        return
    fi
    # GNU sed (-i'') and BSD sed (-i '') differ — detect and use the right form.
    if sed --version >/dev/null 2>&1; then
        # GNU sed (Linux, Homebrew gsed)
        sed -i -E 's/[0-9]{12}/000000000000/g' "$cast_file"
    else
        # BSD sed (macOS default)
        sed -i '' -E 's/[0-9]{12}/000000000000/g' "$cast_file"
    fi
}

# strip_emoji_from_cast <cast_file>
#
# Rewrites tofu-triggering Unicode codepoints in a .cast file to ASCII or
# to monochrome glyphs Menlo can render, so agg never falls back to
# ``.LastResort`` during GIF conversion.
#
# Background: agg uses resvg/usvg, a pure-vector text renderer. When the
# first font in the family list can't render a glyph, usvg falls back to
# ``.LastResort`` (the system tofu font) rather than iterating the family
# list. Color emoji fonts like Apple Color Emoji don't help because they're
# bitmap (sbix/COLR) fonts, which usvg cannot use.
#
# This helper runs in-place with Python 3 for portable Unicode handling.
# The substitutions:
#   ℹ (INFORMATION SOURCE, U+2139)   → i       Menlo has no glyph
#   ✅ (WHITE HEAVY CHECK MARK, U+2705) → ✓     Menlo has ✓, not ✅
#   ✨ (SPARKLES, U+2728)            → *       Menlo has no glyph
#   📦 (PACKAGE, U+1F4E6)            → [pkg]   Menlo has no glyph
#   🚀 (ROCKET, U+1F680)             → >>      Menlo has no glyph
#
# Use SKIP_EMOJI_STRIP=1 to bypass (useful when you're confident your font
# chain renders everything correctly and don't want the substitutions).
strip_emoji_from_cast() {
    local cast_file="$1"
    if [ "${SKIP_EMOJI_STRIP:-}" = "1" ]; then
        return
    fi
    if [ ! -f "$cast_file" ]; then
        return
    fi
    # Python handles Unicode character substitution cleanly across GNU and
    # BSD sed variants, and lets us express the character set as a readable
    # translation table rather than cramming UTF-8 byte sequences into a
    # fragile sed one-liner.
    python3 - "$cast_file" <<'PYEOF'
import sys
from pathlib import Path

# Single-character substitutions (str.translate with the ord key).
SINGLE = {
    0x2139: "i",        # ℹ INFORMATION SOURCE → i
    0x2705: "\u2713",   # ✅ WHITE HEAVY CHECK MARK → ✓ (monochrome check, in Menlo)
    0x2728: "*",        # ✨ SPARKLES → *
}

# Multi-character substitutions applied after the translate pass.
MULTI = {
    "\U0001F4E6": "[pkg]",   # 📦 PACKAGE
    "\U0001F680": ">>",      # 🚀 ROCKET
}

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
text = text.translate(SINGLE)
for src, dst in MULTI.items():
    text = text.replace(src, dst)
path.write_text(text, encoding="utf-8")
PYEOF
}

# render_gif <cast_file> <gif_file> <speed> <theme> <cols> <rows>
#
# Converts an asciinema .cast file to an animated GIF using agg with the
# shared font-family fallback chain. Centralised here so all three
# record scripts render consistent-looking output.
#
# The DEMO_FONT_FAMILY env var overrides the default fallback list.
render_gif() {
    local cast_file="$1"
    local gif_file="$2"
    local speed="$3"
    local theme="$4"
    local cols="$5"
    local rows="$6"
    local font_family="${DEMO_FONT_FAMILY:-$DEMO_FONT_FAMILY_DEFAULT}"

    agg \
        --speed "$speed" \
        --theme "$theme" \
        --font-family "$font_family" \
        --font-size 14 \
        --cols "$cols" \
        --rows "$rows" \
        "$cast_file" \
        "$gif_file"
}
