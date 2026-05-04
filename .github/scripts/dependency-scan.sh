#!/usr/bin/env bash
# =============================================================================
# dependency-scan.sh — check Python, Docker, Helm, and EKS-addon versions
# =============================================================================
#
# Invoked by .github/workflows/deps-scan.yml (monthly schedule).
#
# Checks for drift across:
#
#   - Python packages pinned in pyproject.toml
#   - Docker images referenced from workflows, K8s manifests, examples,
#     and Helm chart values
#   - Helm chart versions from charts.yaml
#   - EKS add-on versions from gco/stacks/constants.py (AWS creds)
#   - Aurora PostgreSQL engine versions (AWS creds)
#   - EMR Serverless release labels (AWS creds)
#   - Dockerfile.dev ARG pins (Node LTS major, CDK CLI, kubectl, AWS CLI v2,
#     Docker CLI) — public endpoints, no AWS creds needed
#
# Ports the `.dependency-scan-script` YAML anchor from the retired
# GitLab pipeline into a standalone shell script. Two behavior changes:
#
# 1. Workflow file input. The GitLab version grepped `.gitlab-ci.yml` for
#    CI image tags. This version scans every file under
#    `$WORKFLOWS_DIR` (default: `.github/workflows`).
# 2. Reporting. The GitLab version POSTed directly to the GitLab issues
#    API. This version writes a Markdown report to a file and emits
#    `has_drift=true|false` + `report_path=…` on $GITHUB_OUTPUT so the
#    calling workflow can open an issue via `gh issue create`.
#
# Environment inputs:
#   WORKFLOWS_DIR  default: .github/workflows
#
# Outputs (via $GITHUB_OUTPUT):
#   has_drift    "true" when any version is outdated, else "false"
#   report_path  path to the Markdown report (only set when has_drift=true)
# =============================================================================
set -uo pipefail

WORKFLOWS_DIR="${WORKFLOWS_DIR:-.github/workflows}"
REPORT_FILE="$(mktemp -t dep-scan-XXXXXX.md 2>/dev/null || mktemp --suffix=.md)"

# Source shared functions (also used by BATS tests)
SCAN_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=.github/scripts/lib_dependency_scan.sh
source "${SCAN_SCRIPT_DIR}/lib_dependency_scan.sh"

# ---------------------------------------------------------------------------
# Python packages
# ---------------------------------------------------------------------------
echo "=== Checking for outdated Python dependencies ==="

pip install -e . --quiet --root-user-action=ignore
OUTDATED="$(pip list --outdated --format=json)"
PYTHON_COUNT="$(echo "$OUTDATED" | jq 'length')"
if [ "$PYTHON_COUNT" -eq 0 ]; then
  echo "All Python dependencies are up to date."
  PYTHON_OUTDATED=""
else
  echo "Found $PYTHON_COUNT outdated Python package(s)"
  echo "$OUTDATED" | jq -r '.[] | "  - \(.name): \(.version) -> \(.latest_version)"'
  PYTHON_OUTDATED="$OUTDATED"
fi

# ---------------------------------------------------------------------------
# Docker image tags
# ---------------------------------------------------------------------------
echo ""
echo "=== Checking for outdated Docker images ==="

DOCKER_RESULTS="$(mktemp)"
ALL_IMAGES="$(mktemp)"

check_image() {
  local image="$1"
  local current_tag="$2"

  # Only handle semver tags
  if ! is_semver_tag "$current_tag"; then
    return
  fi
  # Skip images we build in this project
  if is_project_image "$image"; then
    return
  fi

  local parsed registry repo
  parsed="$(parse_image_registry "$image")"
  registry="$(echo "$parsed" | cut -d'|' -f1)"
  repo="$(echo "$parsed" | cut -d'|' -f2)"

  local tags=""
  tags="$(skopeo list-tags "docker://${registry}/${repo}" 2>/dev/null \
    | jq -r '.Tags[]' 2>/dev/null \
    | grep -E "^v?[0-9]+\.[0-9]+\.[0-9]+$" \
    | sort -V | tail -10)" || return

  [ -z "$tags" ] && return

  local latest_tag
  latest_tag="$(echo "$tags" | tail -1)"

  if [ "$(compare_semver "$current_tag" "$latest_tag")" = "newer" ]; then
    echo "  - ${image}:${current_tag} -> ${latest_tag}"
    echo "${image}|${current_tag}|${latest_tag}" >> "$DOCKER_RESULTS"
  fi
}

# Collect image:tag pairs from workflow files (bare `image:` references in
# container specs and `uses: …@sha` are handled by Dependabot; here we look
# for free-form image references in run steps).
echo "Checking workflow files in $WORKFLOWS_DIR..."
if [ -d "$WORKFLOWS_DIR" ]; then
  grep -rhoE "image: [a-zA-Z0-9_./-]+:[a-zA-Z0-9._-]+" "$WORKFLOWS_DIR" 2>/dev/null \
    | sed 's/image: //' >> "$ALL_IMAGES" || true
  grep -rhoE "[a-zA-Z0-9_./-]+:[a-zA-Z0-9._-]+" "$WORKFLOWS_DIR" 2>/dev/null \
    | grep -E '^(hadolint|koalaman|semgrep|bridgecrew|checkmarx|trufflesecurity|zricethezav|aquasec|bats|python):' \
    | sed 's/[[:space:]]*$//' >> "$ALL_IMAGES" || true
fi

echo "Checking K8s manifest images..."
grep -rhoE "image: [a-zA-Z0-9_./-]+:[a-zA-Z0-9._-]+" lambda/kubectl-applier-simple/manifests/ 2>/dev/null \
  | grep -v '{{' | sed 's/image: //' >> "$ALL_IMAGES" || true

echo "Checking example manifest images..."
grep -rhoE "image: [a-zA-Z0-9_./-]+:[a-zA-Z0-9._-]+" examples/ 2>/dev/null \
  | sed 's/image: //' >> "$ALL_IMAGES" || true

echo "Checking Helm chart value images..."
python3 - <<'PY' >> "$ALL_IMAGES" || true
import yaml
try:
    with open('lambda/helm-installer/charts.yaml') as f:
        data = yaml.safe_load(f)
except FileNotFoundError:
    raise SystemExit(0)


def find_images(d):
    if isinstance(d, dict):
        repo = d.get('repository', '')
        tag = d.get('tag', '')
        if repo and tag and '/' in repo:
            print(f'{repo}:{tag}')
        for v in d.values():
            find_images(v)
    elif isinstance(d, list):
        for item in d:
            find_images(item)


for name, cfg in (data or {}).get('charts', {}).items():
    find_images(cfg.get('values', {}))
PY

sort -u "$ALL_IMAGES" | while read -r img; do
  [ -z "$img" ] && continue
  image="$(echo "$img" | cut -d':' -f1)"
  tag="$(echo "$img" | cut -d':' -f2)"
  check_image "$image" "$tag"
done
rm -f "$ALL_IMAGES"

DOCKER_COUNT="$(wc -l < "$DOCKER_RESULTS" | tr -d ' ')"
[ -z "$DOCKER_COUNT" ] && DOCKER_COUNT=0

# ---------------------------------------------------------------------------
# Helm chart versions
# ---------------------------------------------------------------------------
echo ""
echo "=== Checking Helm chart versions ==="

HELM_RESULTS="$(mktemp)"
CHARTS_FILE="lambda/helm-installer/charts.yaml"

if [ -f "$CHARTS_FILE" ]; then
  python3 - "$CHARTS_FILE" <<'PY' | while IFS= read -r entry; do
import json, sys, yaml
with open(sys.argv[1]) as f:
    data = yaml.safe_load(f)
for name, cfg in (data or {}).get('charts', {}).items():
    print(json.dumps({
        'name':     name,
        'repo_url': cfg.get('repo_url', ''),
        'chart':    cfg.get('chart', ''),
        'version':  cfg.get('version', ''),
        'use_oci':  cfg.get('use_oci', False),
    }))
PY
    chart_name="$(echo "$entry" | jq -r '.name')"
    repo_url="$(echo "$entry" | jq -r '.repo_url')"
    chart="$(echo "$entry" | jq -r '.chart')"
    current="$(echo "$entry" | jq -r '.version')"
    use_oci="$(echo "$entry" | jq -r '.use_oci')"
    [ -z "$current" ] && continue

    latest=""
    if [ "$use_oci" = "true" ]; then
      latest="$(helm show chart "${repo_url}/${chart}" 2>/dev/null | grep '^version:' | awk '{print $2}')" || true
    else
      helm repo add "$chart_name" "$repo_url" --force-update > /dev/null 2>&1 || true
      latest="$(helm search repo "${chart_name}/${chart}" --output json 2>/dev/null | jq -r '.[0].version // empty')" || true
    fi

    if [ -n "$latest" ] && [ "$current" != "$latest" ]; then
      current_stripped="${current#v}"
      latest_stripped="${latest#v}"
      if [ "$current_stripped" != "$latest_stripped" ]; then
        echo "  - ${chart_name} (${chart}): ${current} -> ${latest}"
        echo "${chart_name}|${chart}|${current}|${latest}" >> "$HELM_RESULTS"
      fi
    fi
  done
fi

HELM_COUNT="$(wc -l < "$HELM_RESULTS" 2>/dev/null | tr -d ' ')"
[ -z "$HELM_COUNT" ] && HELM_COUNT=0

# ---------------------------------------------------------------------------
# EKS add-on versions (best-effort — requires AWS credentials)
#
# Pre-flight: probe for usable AWS credentials. If `sts get-caller-identity`
# fails the scan is skipped entirely and a one-line note goes into both the
# console log and the Markdown report — this is more honest than silently
# dropping the section. Wire AWS creds through OIDC (see the deps-scan
# section in .github/CI.md) to enable the check.
# ---------------------------------------------------------------------------
echo ""
echo "=== Checking EKS add-on versions ==="

ADDON_RESULTS="$(mktemp)"
ADDON_SKIP_REASON=""
K8S_VERSION="$(extract_k8s_version "")"

if ! aws sts get-caller-identity >/dev/null 2>&1; then
  ADDON_SKIP_REASON="No AWS credentials available (scan needs eks:DescribeAddonVersions). Configure OIDC to enable."
  echo "  $ADDON_SKIP_REASON"
else
  extract_eks_addons "gco/stacks/regional_stack.py" | while IFS='|' read -r addon_name current_version; do
      [ -z "$addon_name" ] && continue
      latest="$(aws eks describe-addon-versions \
        --addon-name "$addon_name" \
        --kubernetes-version "$K8S_VERSION" \
        --query 'addons[0].addonVersions[0].addonVersion' \
        --output text 2>/dev/null)" || true

      if [ -n "$latest" ] && [ "$latest" != "None" ] && [ "$current_version" != "$latest" ]; then
        echo "  - ${addon_name}: ${current_version} -> ${latest}"
        echo "${addon_name}|${current_version}|${latest}" >> "$ADDON_RESULTS"
      fi
    done
fi

ADDON_COUNT="$(wc -l < "$ADDON_RESULTS" 2>/dev/null | tr -d ' ')"
[ -z "$ADDON_COUNT" ] && ADDON_COUNT=0

# ---------------------------------------------------------------------------
# Aurora PostgreSQL engine versions (best-effort — requires AWS credentials)
#
# Checks whether the Aurora PostgreSQL engine version pinned in
# regional_stack.py has a newer minor or major release available.
# Uses the same credential gate as the EKS add-on check above.
# ---------------------------------------------------------------------------
echo ""
echo "=== Checking Aurora PostgreSQL engine versions ==="

AURORA_RESULTS="$(mktemp)"
AURORA_SKIP_REASON=""

if ! aws sts get-caller-identity >/dev/null 2>&1; then
  AURORA_SKIP_REASON="No AWS credentials available (scan needs rds:DescribeDBEngineVersions). Configure OIDC to enable."
  echo "  $AURORA_SKIP_REASON"
else
  # Extract pinned Aurora PostgreSQL versions from regional_stack.py
  # Pattern: AuroraPostgresEngineVersion.VER_XX_Y
  extract_aurora_versions "gco/stacks/regional_stack.py" | while read -r current_ver; do
    [ -z "$current_ver" ] && continue
    major="$(echo "$current_ver" | cut -d. -f1)"

    # Query the latest available engine version for this major line
    latest="$(aws rds describe-db-engine-versions \
      --engine aurora-postgresql \
      --query "DBEngineVersions[?starts_with(EngineVersion, '${major}.')].EngineVersion" \
      --output text 2>/dev/null \
      | tr '\t' '\n' | sort -V | tail -1)" || true

    if [ -n "$latest" ] && [ "$current_ver" != "$latest" ]; then
      echo "  - aurora-postgresql: ${current_ver} -> ${latest}"
      echo "aurora-postgresql|${current_ver}|${latest}" >> "$AURORA_RESULTS"
    fi
  done
fi

AURORA_COUNT="$(wc -l < "$AURORA_RESULTS" 2>/dev/null | tr -d ' ')"
[ -z "$AURORA_COUNT" ] && AURORA_COUNT=0

# ---------------------------------------------------------------------------
# EMR Serverless release labels (best-effort — requires AWS credentials)
#
# Checks whether the EMR Serverless release label pinned in
# gco/stacks/constants.py has a newer release available. Uses the same
# credential gate as the EKS add-on / Aurora checks above.
#
# AWS CLI note: the `list-release-labels` subcommand lives on the classic
# `aws emr` service, not on `aws emr-serverless`. Classic EMR and EMR
# Serverless share the same release-label namespace (e.g. emr-7.13.0),
# so calling the classic service returns the labels usable by Serverless
# applications. The IAM action is ``elasticmapreduce:ListReleaseLabels``
# (which is what the OIDC policy grants) and is shared between the two
# services — the CLI routing is just a surface-level difference.
# ---------------------------------------------------------------------------
echo ""
echo "=== Checking EMR Serverless release labels ==="

EMR_RESULTS="$(mktemp)"
EMR_SKIP_REASON=""

if ! aws sts get-caller-identity >/dev/null 2>&1; then
  EMR_SKIP_REASON="No AWS credentials available (scan needs elasticmapreduce:ListReleaseLabels). Configure OIDC to enable."
  echo "  $EMR_SKIP_REASON"
else
  extract_emr_versions "gco/stacks/constants.py" | while read -r current_label; do
    [ -z "$current_label" ] && continue
    # current_label looks like "emr-7.13.0". Filter labels to ones that
    # start with "emr-<major>." and take the latest by semver-ish sort.
    # Skip preview/nightly tags (``-preview``, ``-beta``, ``-rc*``). The
    # latest release label is what we compare against.
    major="$(echo "$current_label" | sed -E 's/^emr-([0-9]+)\..*/\1/')"
    latest="$(aws emr list-release-labels \
      --region us-east-1 \
      --query 'ReleaseLabels[]' --output text 2>/dev/null \
      | tr '\t' '\n' \
      | grep -E "^emr-${major}\.[0-9]+\.[0-9]+$" \
      | sort -V | tail -1)" || true

    # Also check whether a newer major release line exists.
    latest_any="$(aws emr list-release-labels \
      --region us-east-1 \
      --query 'ReleaseLabels[]' --output text 2>/dev/null \
      | tr '\t' '\n' \
      | grep -E "^emr-[0-9]+\.[0-9]+\.[0-9]+$" \
      | sort -V | tail -1)" || true

    if [ -n "$latest" ] && [ "$current_label" != "$latest" ]; then
      echo "  - emr-serverless: ${current_label} -> ${latest}"
      echo "emr-serverless|${current_label}|${latest}" >> "$EMR_RESULTS"
    elif [ -n "$latest_any" ] && [ "$current_label" != "$latest_any" ] \
         && [ "$(compare_semver "${current_label#emr-}" "${latest_any#emr-}")" = "newer" ]; then
      # Same minor — no new release in our pinned major — but a new
      # major exists.
      echo "  - emr-serverless: ${current_label} -> ${latest_any} (new major available)"
      echo "emr-serverless|${current_label}|${latest_any}" >> "$EMR_RESULTS"
    fi
  done
fi

EMR_COUNT="$(wc -l < "$EMR_RESULTS" 2>/dev/null | tr -d ' ')"
[ -z "$EMR_COUNT" ] && EMR_COUNT=0

# ---------------------------------------------------------------------------
# Dockerfile.dev ARG pins
#
# Checks the tooling versions pinned in ``Dockerfile.dev`` (Node.js LTS
# major, AWS CDK CLI, kubectl, AWS CLI v2, Docker CLI). These ARGs sit
# outside the main dependency surfaces above — Dependabot watches the
# ``FROM python:…`` base image but not the ARG pins — so drift here has
# historically gone undetected until someone rebuilt the image.
#
# Each pin has its own upstream:
#
#   NODE_MAJOR     github://nodejs/Release → schedule.json (LTS majors)
#   CDK_VERSION    registry.npmjs.org/aws-cdk/latest
#   KUBECTL_VERSION https://dl.k8s.io/release/stable-<minor>.txt
#                  (minor from cdk.json::kubernetes_version)
#   AWSCLI_VERSION github://aws/aws-cli/tags (v2.x.y semver, no GitHub Releases)
#   DOCKER_VERSION github://moby/moby/releases/latest (``docker-v<ver>``)
#
# All endpoints are public — no AWS credentials needed.
# ---------------------------------------------------------------------------
echo ""
echo "=== Checking Dockerfile.dev ARG pins ==="

DOCKERFILE_RESULTS="$(mktemp)"
DOCKERFILE_PIN_FILE="Dockerfile.dev"

check_dockerfile_pin() {
  local name="$1" current="$2" latest=""
  case "$name" in
    NODE_MAJOR)
      # Pick the highest major with an active LTS window:
      #   lts <= today AND (end missing or end > today).
      latest="$(curl -fsSL --max-time 15 \
        "https://raw.githubusercontent.com/nodejs/Release/main/schedule.json" 2>/dev/null \
        | python3 -c '
import sys, json, datetime
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)
today = datetime.date.today().isoformat()
candidates = []
for k, v in data.items():
    if not k.startswith("v") or "lts" not in v:
        continue
    if v["lts"] > today:
        continue
    if v.get("end", "9999-12-31") <= today:
        continue
    try:
        candidates.append(int(k[1:]))
    except ValueError:
        continue
if candidates:
    print(max(candidates))
' 2>/dev/null)" || true
      ;;
    CDK_VERSION)
      latest="$(curl -fsSL --max-time 15 \
        "https://registry.npmjs.org/aws-cdk/latest" 2>/dev/null \
        | jq -r '.version // empty' 2>/dev/null)" || true
      ;;
    KUBECTL_VERSION)
      # Match the minor line already committed to cdk.json so the pin
      # and the EKS cluster stay within the ±1 minor skew policy.
      local k8s_minor
      k8s_minor="$(extract_k8s_version "cdk.json")"
      latest="$(curl -fsSL --max-time 15 \
        "https://dl.k8s.io/release/stable-${k8s_minor}.txt" 2>/dev/null | tr -d '[:space:]')" || true
      ;;
    AWSCLI_VERSION)
      # aws/aws-cli doesn't publish GitHub Releases for v2; tags are the
      # canonical source. First page (per_page=20) is newest-first;
      # filter to 2.x.y semver and take the top match.
      latest="$(curl -fsSL --max-time 15 \
        "https://api.github.com/repos/aws/aws-cli/tags?per_page=20" 2>/dev/null \
        | jq -r '[.[].name | select(test("^2\\.[0-9]+\\.[0-9]+$"))][0] // empty' 2>/dev/null)" || true
      ;;
    DOCKER_VERSION)
      # moby/moby tags releases as ``docker-v<semver>``; strip the
      # prefix so compare_semver can handle the value.
      latest="$(curl -fsSL --max-time 15 \
        "https://api.github.com/repos/moby/moby/releases/latest" 2>/dev/null \
        | jq -r '.tag_name // empty' 2>/dev/null \
        | sed -E 's/^(docker-)?v//')" || true
      ;;
    *)
      return
      ;;
  esac

  [ -z "$latest" ] && return

  # NODE_MAJOR is a bare integer (e.g. ``24``) not a semver. Compare as
  # integers; everything else goes through compare_semver.
  local relation
  if [ "$name" = "NODE_MAJOR" ]; then
    if ! [[ "$current" =~ ^[0-9]+$ ]] || ! [[ "$latest" =~ ^[0-9]+$ ]]; then
      return
    fi
    if [ "$latest" -gt "$current" ]; then
      relation="newer"
    else
      relation="same_or_older"
    fi
  else
    relation="$(compare_semver "$current" "$latest")"
  fi

  if [ "$relation" = "newer" ]; then
    echo "  - ${name}: ${current} -> ${latest}"
    echo "${name}|${current}|${latest}" >> "$DOCKERFILE_RESULTS"
  fi
}

if [ -f "$DOCKERFILE_PIN_FILE" ]; then
  extract_dockerfile_pins "$DOCKERFILE_PIN_FILE" | while IFS='|' read -r pin_name pin_value; do
    [ -z "$pin_name" ] && continue
    check_dockerfile_pin "$pin_name" "$pin_value"
  done
else
  echo "  $DOCKERFILE_PIN_FILE not found, skipping."
fi

DOCKERFILE_COUNT="$(wc -l < "$DOCKERFILE_RESULTS" 2>/dev/null | tr -d ' ')"
[ -z "$DOCKERFILE_COUNT" ] && DOCKERFILE_COUNT=0

# ---------------------------------------------------------------------------
# Summary + Markdown report
# ---------------------------------------------------------------------------
echo ""
echo "=== Summary ==="
echo "Python packages outdated: $PYTHON_COUNT"
echo "Docker images outdated:   $DOCKER_COUNT"
echo "Helm charts outdated:     $HELM_COUNT"
if [ -n "$ADDON_SKIP_REASON" ]; then
  echo "EKS add-ons outdated:     (skipped)"
else
  echo "EKS add-ons outdated:     $ADDON_COUNT"
fi
if [ -n "$AURORA_SKIP_REASON" ]; then
  echo "Aurora PostgreSQL:        (skipped)"
else
  echo "Aurora PostgreSQL:        $AURORA_COUNT"
fi
if [ -n "$EMR_SKIP_REASON" ]; then
  echo "EMR Serverless release:   (skipped)"
else
  echo "EMR Serverless release:   $EMR_COUNT"
fi
echo "Dockerfile.dev pins:      $DOCKERFILE_COUNT"

if [ "$PYTHON_COUNT" -eq 0 ] && [ "$DOCKER_COUNT" -eq 0 ] \
   && [ "$HELM_COUNT" -eq 0 ] && [ "$ADDON_COUNT" -eq 0 ] \
   && [ "$AURORA_COUNT" -eq 0 ] && [ "$EMR_COUNT" -eq 0 ] \
   && [ "$DOCKERFILE_COUNT" -eq 0 ]; then
  echo ""
  SKIP_NOTES=""
  if [ -n "$ADDON_SKIP_REASON" ]; then
    SKIP_NOTES="EKS add-ons skipped: $ADDON_SKIP_REASON"
  fi
  if [ -n "$AURORA_SKIP_REASON" ]; then
    [ -n "$SKIP_NOTES" ] && SKIP_NOTES="$SKIP_NOTES; "
    SKIP_NOTES="${SKIP_NOTES}Aurora engine skipped: $AURORA_SKIP_REASON"
  fi
  if [ -n "$EMR_SKIP_REASON" ]; then
    [ -n "$SKIP_NOTES" ] && SKIP_NOTES="$SKIP_NOTES; "
    SKIP_NOTES="${SKIP_NOTES}EMR Serverless skipped: $EMR_SKIP_REASON"
  fi
  if [ -n "$SKIP_NOTES" ]; then
    echo "All scanned surfaces are up to date ($SKIP_NOTES)"
  else
    echo "All dependencies are up to date."
  fi
  rm -f "$DOCKER_RESULTS" "$HELM_RESULTS" "$ADDON_RESULTS" "$AURORA_RESULTS" "$EMR_RESULTS" "$DOCKERFILE_RESULTS"
  if [ -n "${GITHUB_OUTPUT:-}" ]; then
    echo "has_drift=false" >> "$GITHUB_OUTPUT"
  fi
  exit 0
fi

{
  echo "# Dependency Update Report"
  echo ""

  if [ "$PYTHON_COUNT" -gt 0 ]; then
    echo "## Python Packages"
    echo ""
    echo "| Package | Current | Latest |"
    echo "|---------|---------|--------|"
    echo "$PYTHON_OUTDATED" | jq -r '.[] | "| \(.name) | \(.version) | \(.latest_version) |"'
    echo ""
  fi

  if [ "$DOCKER_COUNT" -gt 0 ]; then
    echo "## Docker Images"
    echo ""
    echo "| Image | Current | Latest |"
    echo "|-------|---------|--------|"
    while IFS='|' read -r img cur lat; do
      echo "| $img | $cur | $lat |"
    done < "$DOCKER_RESULTS"
    echo ""
  fi

  if [ "$HELM_COUNT" -gt 0 ]; then
    echo "## Helm Charts"
    echo ""
    echo "| Chart | Name | Current | Latest |"
    echo "|-------|------|---------|--------|"
    while IFS='|' read -r cname chart cur lat; do
      echo "| $cname | $chart | $cur | $lat |"
    done < "$HELM_RESULTS"
    echo ""
  fi

  if [ "$ADDON_COUNT" -gt 0 ]; then
    echo "## EKS Add-ons"
    echo ""
    echo "| Add-on | Current | Latest |"
    echo "|--------|---------|--------|"
    while IFS='|' read -r addon cur lat; do
      echo "| $addon | $cur | $lat |"
    done < "$ADDON_RESULTS"
    echo ""
  fi

  if [ -n "$ADDON_SKIP_REASON" ]; then
    echo "## EKS Add-ons (skipped)"
    echo ""
    echo "> $ADDON_SKIP_REASON"
    echo ""
  fi

  if [ "$AURORA_COUNT" -gt 0 ]; then
    echo "## Aurora PostgreSQL Engine"
    echo ""
    echo "| Engine | Current | Latest |"
    echo "|--------|---------|--------|"
    while IFS='|' read -r engine cur lat; do
      echo "| $engine | $cur | $lat |"
    done < "$AURORA_RESULTS"
    echo ""
  fi

  if [ -n "$AURORA_SKIP_REASON" ]; then
    echo "## Aurora PostgreSQL Engine (skipped)"
    echo ""
    echo "> $AURORA_SKIP_REASON"
    echo ""
  fi

  if [ "$EMR_COUNT" -gt 0 ]; then
    echo "## EMR Serverless"
    echo ""
    echo "| Release | Current | Latest |"
    echo "|---------|---------|--------|"
    while IFS='|' read -r release cur lat; do
      echo "| $release | $cur | $lat |"
    done < "$EMR_RESULTS"
    echo ""
  fi

  if [ -n "$EMR_SKIP_REASON" ]; then
    echo "## EMR Serverless (skipped)"
    echo ""
    echo "> $EMR_SKIP_REASON"
    echo ""
  fi

  if [ "$DOCKERFILE_COUNT" -gt 0 ]; then
    echo "## Dockerfile.dev Pins"
    echo ""
    echo "Tooling versions pinned as build-time ARGs in \`Dockerfile.dev\`."
    echo ""
    echo "| Pin | Current | Latest |"
    echo "|-----|---------|--------|"
    while IFS='|' read -r pin cur lat; do
      echo "| \`$pin\` | $cur | $lat |"
    done < "$DOCKERFILE_RESULTS"
    echo ""
  fi

  echo "## Action Required"
  echo ""
  echo "1. Review changelogs for breaking changes"
  echo "2. Update versions in \`pyproject.toml\`, manifests, or \`charts.yaml\`"
  echo "3. Regenerate \`requirements-lock.txt\` if Python deps changed"
  echo "4. Run tests locally to verify compatibility"
  echo "5. Open a PR with the updates"
  echo ""
  echo "---"
  echo "_Automatically created by the \`deps-scan\` workflow._"
} > "$REPORT_FILE"

rm -f "$DOCKER_RESULTS" "$HELM_RESULTS" "$ADDON_RESULTS" "$AURORA_RESULTS" "$EMR_RESULTS" "$DOCKERFILE_RESULTS"

if [ -n "${GITHUB_OUTPUT:-}" ]; then
  echo "has_drift=true"            >> "$GITHUB_OUTPUT"
  echo "report_path=$REPORT_FILE"  >> "$GITHUB_OUTPUT"
fi

echo ""
echo "Wrote report to $REPORT_FILE"
