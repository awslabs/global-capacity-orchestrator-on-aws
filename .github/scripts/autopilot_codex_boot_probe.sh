#!/usr/bin/env bash
# =============================================================================
# autopilot_codex_boot_probe.sh — boot the real `gco autopilot` Codex session
# =============================================================================
#
# Drives `gco autopilot --engine codex` end-to-end the way a first-time user
# does, and verifies the session boots to the last point reachable without
# real AWS credentials. Used by integration:autopilot:codex-boot
# (integration-tests.yml). The Claude Code twin lives in
# autopilot_boot_probe.sh; the phases are parallel on purpose so the two
# probes stay comparable engine to engine.
#
# What runs for real (nothing about autopilot is mocked):
#
#   1. `gco autopilot --engine codex --print-config` resolves the session
#      plan from this checkout (the in-tree gco MCP server plus the curated
#      companion registry, rendered as Codex TOML).
#   2. Every [mcp_servers.*] entry in the generated TOML is pre-warmed by
#      running its exact launch recipe (uvx/npx resolve, install, boot, exit
#      on stdin EOF). Warm caches keep the integrated boot inside Codex's
#      configured per-server startup timeout on cold runners.
#   3. `gco autopilot --engine codex -y -- --version` exercises autopilot's
#      own install path: detect the missing binary, npm-install the pinned
#      release, re-detect it, write the isolated CODEX_HOME config, and exec
#      codex with the session-precedence overrides. The passthrough
#      `--version` makes that exec exit 0 deterministically.
#   4. `gco autopilot --engine codex -- exec "..."` boots the full
#      non-interactive stack: codex loads the generated config, launches the
#      MCP servers, and dispatches to Amazon Bedrock Runtime's OpenAI-
#      compatible endpoint with the shipped default model. With the
#      fail-closed fake credentials exported below, SigV4 validation answers
#      401 — proving a signed request left the wire. The probe asserts,
#      from codex's own RUST_LOG=info stderr:
#
#        - mcp_server_count/mcp_servers="..." carrying EVERY planned server
#        - Service initialized as client        (MCP handshakes under codex)
#        - model=<configured default>           (the Bedrock dispatch)
#        - https://bedrock-runtime.<region>.amazonaws.com/openai/v1/responses
#        - Turn error: unexpected status 401 Unauthorized ... security token
#
#      Codex retries the sampling request a bounded number of times and then
#      exits nonzero on its own, so the probe simply waits for it — no
#      background session management is needed.
#
# Engine delta vs the Claude probe, asserted honestly: Claude Code blocks on
# every MCP connection before the first prompt, so its probe requires a
# per-server "Successfully connected" line. Codex races MCP initialization
# against the first turn, and this session ends at the credential boundary
# after ~1 minute — slower servers may still be mid-launch when it exits.
# Per-server *boot* proof therefore lives in the pre-warm phase (each launch
# recipe must start), config fidelity is proven by the mcp_servers list the
# session logs, and the MCP subsystem is proven live by requiring at least
# one completed in-session initialize handshake.
#
# Marker stability: the debug markers above were captured from the pinned
# Codex release (cli/autopilot.py CODEX_VERSION). A pin bump can rephrase
# them; the failure output names the missing marker so the bump PR can
# refresh this probe alongside the pin.
#
# Requirements: gco (this checkout, installed), node+npm (pinned via
# .github/scripts/use-pinned-npm.sh), uv/uvx, python3, GNU coreutils
# `timeout`. The `codex` binary must NOT be preinstalled — the probe exists
# to prove autopilot's own install path works.
#
# =============================================================================

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

WORK_DIR="${RUNNER_TEMP:-$(mktemp -d)}/autopilot-codex-boot-probe"
mkdir -p "$WORK_DIR"

# Autopilot writes the session config (and the isolated CODEX_HOME beneath
# it) here instead of ~/.gco/autopilot.
export GCO_AUTOPILOT_CONFIG_DIR="${WORK_DIR}/config"

SESSION_LOG="${WORK_DIR}/session.log"
PREWARM_DIR="${WORK_DIR}/prewarm"
mkdir -p "$PREWARM_DIR"

# How long the integrated session may take to reach the credential boundary.
# Codex's own bounded retries finish in roughly a minute; the budget covers
# slow MCP launches on a cold runner without masking a hang.
BOOT_TIMEOUT_SECONDS="${BOOT_TIMEOUT_SECONDS:-420}"

# The one EXIT trap: gather evidence for the always-uploaded artifact. The
# generated TOML and codex's own log directory both live under WORK_DIR
# already (GCO_AUTOPILOT_CONFIG_DIR), so only the session log needs copying.
collect_and_cleanup() {
    cp -f "$SESSION_LOG" "${WORK_DIR}/session.log" 2>/dev/null || true
}
trap collect_and_cleanup EXIT

fail() {
    echo "✗ $1" >&2
    exit 1
}

pass() {
    echo "✓ $1"
}

# ── Fail-closed credential environment ──────────────────────────────────────
# The probe must never reach Bedrock with usable credentials, even if the
# surrounding job one day exports some. A syntactically valid but fabricated
# static key pair wins the SDK provider chain ahead of every file/role
# source, and the file/IMDS sources are disabled outright. The key id is
# assembled at runtime so repository secret scanners (gitleaks, trufflehog)
# never see a contiguous AKIA-shaped literal in the tree.
AWS_ACCESS_KEY_ID="$(printf 'AKIA%s' '00000000000fake0')"
AWS_SECRET_ACCESS_KEY="$(printf '%040d' 0)"
export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY
export AWS_SHARED_CREDENTIALS_FILE=/dev/null
export AWS_CONFIG_FILE=/dev/null
export AWS_EC2_METADATA_DISABLED=true
unset AWS_SESSION_TOKEN AWS_PROFILE AWS_ROLE_ARN AWS_WEB_IDENTITY_TOKEN_FILE 2>/dev/null || true

# ── Preflight ────────────────────────────────────────────────────────────────

for tool in gco npm uvx python3 timeout; do
    command -v "$tool" >/dev/null || fail "required tool missing: $tool"
done

if command -v codex >/dev/null; then
    fail "codex is already installed at $(command -v codex) — this probe must exercise autopilot's own install path"
fi

# Facts come from the shared autopilot CI contract — the same single source
# unit:cli:autopilot, the dev-container step, and the Claude probe assert
# against.
CONTRACT=".github/scripts/autopilot_ci_contract.py"
CODEX_PIN="$(python3 "$CONTRACT" pin --engine codex)"
EXPECTED_MODEL="$(python3 "$CONTRACT" default-model --engine codex)"
pass "preflight OK (pin ${CODEX_PIN}, default model ${EXPECTED_MODEL})"

# ── Phase 1: resolve the session plan from this checkout ────────────────────

GENERATED_CONFIG="${WORK_DIR}/print-config.toml"
gco autopilot --engine codex --print-config > "$GENERATED_CONFIG"

# Full structural validation from the shared contract (model/provider/wire
# API lines, exact expected server set, entry shapes), then load the
# expected names for the pre-warm and session assertions below.
python3 "$CONTRACT" verify-codex-config "$GENERATED_CONFIG" \
    || fail "generated Codex config failed the shared autopilot CI contract"
mapfile -t SERVER_NAMES < <(python3 "$CONTRACT" expected-servers)
[ "${#SERVER_NAMES[@]}" -ge 2 ] || fail "contract lists ${#SERVER_NAMES[@]} servers; expected the gco server plus companions"
pass "session plan resolves: ${#SERVER_NAMES[@]} MCP servers (${SERVER_NAMES[*]})"

# ── Phase 2: pre-warm every server's exact launch recipe ────────────────────
# Each companion is launched exactly as the generated TOML specifies and
# handed EOF on stdin, which a stdio MCP server treats as client disconnect.
# This resolves and installs every uvx/npx package (an independent
# per-package install check with a pinpointed log on failure) and warms the
# caches so the integrated boot below is not racing package managers against
# Codex's per-server startup timeout.

mapfile -t PREWARM_CMDS < <(python3 - "$GENERATED_CONFIG" <<'PY'
import shlex, sys, tomllib
with open(sys.argv[1], "rb") as handle:
    config = tomllib.load(handle)
for name, entry in sorted(config["mcp_servers"].items()):
    env_prefix = " ".join(
        f"{key}={shlex.quote(str(value))}" for key, value in entry.get("env", {}).items()
    )
    command = " ".join(shlex.quote(str(part)) for part in [entry["command"], *entry.get("args", [])])
    print(f"{name}\t{env_prefix} {command}".replace("\t ", "\t", 1))
PY
)

PREWARM_FAILURES=0
for line in "${PREWARM_CMDS[@]}"; do
    name="${line%%$'\t'*}"
    launch="${line#*$'\t'}"
    rc=0
    timeout 240 bash -c "$launch" </dev/null >"${PREWARM_DIR}/${name}.log" 2>&1 || rc=$?
    case "$rc" in
        0)
            pass "pre-warm ${name}: launched and exited on stdin EOF" ;;
        124)
            # Ran the full 240s before timeout killed it: the package
            # resolved, installed, and booted (cache warmed) — it just
            # doesn't exit on EOF. The in-session behavior happens later
            # under codex, where connection management is codex's job.
            pass "pre-warm ${name}: launched and ran until the warm-up timeout" ;;
        125 | 126 | 127)
            # timeout itself failed / command not executable / not found:
            # the launch recipe is broken.
            echo "── ${PREWARM_DIR}/${name}.log ──"
            cat "${PREWARM_DIR}/${name}.log" || true
            echo "✗ pre-warm ${name}: launch recipe failed (exit ${rc}): ${launch}" >&2
            PREWARM_FAILURES=$((PREWARM_FAILURES + 1)) ;;
        *)
            # Any other non-zero exit on EOF is server-specific and fine;
            # the process launched, which is all warming needs.
            pass "pre-warm ${name}: launched and exited on stdin EOF (rc ${rc})" ;;
    esac
done
[ "$PREWARM_FAILURES" -eq 0 ] || fail "${PREWARM_FAILURES} companion launch recipe(s) failed to start at all"

# ── Phase 3: autopilot's own install path, exec verified by --version ───────
# codex is absent, so `-y` makes autopilot npm-install the exact pin,
# re-detect the binary, write the isolated CODEX_HOME config, and exec it
# with the session-precedence argv. `--version` in the passthrough position
# makes that real exec terminate deterministically.

VERSION_OUTPUT="$(gco autopilot --engine codex -y -- --version 2>&1 | tee "${WORK_DIR}/version-probe.log")"
echo "$VERSION_OUTPUT" | grep -qF "$CODEX_PIN" \
    || fail "autopilot exec'd codex, but its --version output does not carry the pin ${CODEX_PIN}: ${VERSION_OUTPUT}"
command -v codex >/dev/null || fail "autopilot reported an install but codex is not on PATH"
pass "autopilot installed the pin and exec'd codex ${CODEX_PIN} with the session-precedence argv"

WRITTEN_CONFIG="${GCO_AUTOPILOT_CONFIG_DIR}/codex/config.toml"
[ -f "$WRITTEN_CONFIG" ] || fail "autopilot did not write the isolated Codex config to ${WRITTEN_CONFIG}"
python3 - "$GENERATED_CONFIG" "$WRITTEN_CONFIG" <<'PY'
import sys, tomllib
def servers(path):
    with open(path, "rb") as handle:
        return set(tomllib.load(handle)["mcp_servers"])
planned, written = servers(sys.argv[1]), servers(sys.argv[2])
assert planned == written, f"planned {sorted(planned)} != written {sorted(written)}"
PY
pass "written CODEX_HOME config matches the printed plan (${WRITTEN_CONFIG})"

# ── Phase 4: full session boot, stopped at the credential boundary ──────────
# RUST_LOG=info surfaces codex's tracing on stderr: the session-configured
# event (mcp_server_count / mcp_servers list), each MCP initialize handshake,
# the Bedrock Runtime dispatch spans, and the terminal 401. `codex exec`
# retries the rejected sampling request a bounded number of times and then
# exits nonzero by itself, so this run is awaited in the foreground; the
# expected exit is nonzero and asserted as such.

echo "booting the full session (budget ${BOOT_TIMEOUT_SECONDS}s): gco autopilot --engine codex -- exec ..."
SESSION_RC=0
RUST_LOG=info timeout "$BOOT_TIMEOUT_SECONDS" \
    gco autopilot --engine codex -- exec "Reply with the single word OK." \
    </dev/null >"$SESSION_LOG" 2>&1 || SESSION_RC=$?

if [ "$SESSION_RC" -eq 124 ]; then
    echo "── session stdout/stderr (${SESSION_LOG}) ──"
    tail -50 "$SESSION_LOG" || true
    fail "session still running after ${BOOT_TIMEOUT_SECONDS}s — it never reached codex's own bounded-retry exit"
fi
[ "$SESSION_RC" -ne 0 ] \
    || fail "session exited 0 with fabricated credentials — the credential boundary was never enforced"
pass "session ran to codex's own bounded-retry exit (rc ${SESSION_RC})"

LOGS="$(cat "$SESSION_LOG")"

MISSING=""
for name in "${SERVER_NAMES[@]}"; do
    grep -qE "mcp_servers=\"[^\"]*\b${name}\b" <<<"$LOGS" \
        || MISSING+="session-config:${name} "
done
grep -qF "Service initialized as client" <<<"$LOGS" \
    || MISSING+="mcp-initialize-handshake "
grep -qF "model=${EXPECTED_MODEL}" <<<"$LOGS" \
    || MISSING+="bedrock-dispatch:${EXPECTED_MODEL} "
grep -qE 'https://bedrock-runtime\.[a-z0-9-]+\.amazonaws\.com/openai/v1/responses' <<<"$LOGS" \
    || MISSING+="bedrock-runtime-endpoint "
grep -qE 'Turn error: unexpected status 40[13] .*security token' <<<"$LOGS" \
    || MISSING+="credential-boundary-401 "

if [ -n "$MISSING" ]; then
    echo "── session stdout/stderr (${SESSION_LOG}) ──"
    tail -80 "$SESSION_LOG" || true
    fail "session did not produce these boot markers: ${MISSING}"
fi

pass "codex loaded the generated plan: all ${#SERVER_NAMES[@]} servers in the session's mcp_servers list"
pass "MCP subsystem live under codex (initialize handshake observed)"
pass "codex dispatched to Bedrock Runtime with the shipped default model (${EXPECTED_MODEL})"
pass "AWS rejected the fabricated credentials — the exact credential boundary"

# In-session handshake evidence for the job summary (informational: codex
# races MCP init against the turn, so the set observed before the bounded
# 401 exit varies run to run — the required minimum is asserted above).
echo ""
echo "MCP initialize report (servers' self-reported names):"
grep -oE 'Implementation \{ name: "[^"]+"' "$SESSION_LOG" \
    | sed 's/Implementation { name: /  /' | sort -u || true

echo ""
echo "autopilot codex boot probe: PASS"
