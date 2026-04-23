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
