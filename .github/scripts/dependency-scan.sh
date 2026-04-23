#!/usr/bin/env bash
# =============================================================================
# dependency-scan.sh — check Python, Docker, Helm, and EKS-addon versions
# =============================================================================
#
# Invoked by .github/workflows/deps-scan.yml (monthly schedule).
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
REPORT_FILE="$(mktemp --suffix=.md)"

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
  if ! echo "$current_tag" | grep -qE "^v?[0-9]+\.[0-9]+(\.[0-9]+)?"; then
    return
  fi
  # Skip images we build in this project
  if echo "$image" | grep -q "^gco/"; then
    return
  fi

  local registry="" repo=""
  case "$image" in
    nvcr.io/*|gcr.io/*|quay.io/*|ghcr.io/*|registry.k8s.io/*|public.ecr.aws/*)
      registry="$(echo "$image" | cut -d'/' -f1)"
      repo="$(echo "$image" | cut -d'/' -f2-)"
      ;;
    */*)
      registry="docker.io"
      repo="$image"
      ;;
    *)
      registry="docker.io"
      repo="library/$image"
      ;;
  esac

  local tags=""
  tags="$(skopeo list-tags "docker://${registry}/${repo}" 2>/dev/null \
    | jq -r '.Tags[]' 2>/dev/null \
    | grep -E "^v?[0-9]+\.[0-9]+\.[0-9]+$" \
    | sort -V | tail -10)" || return

  [ -z "$tags" ] && return

  local latest_tag current_ver latest_ver newer
  latest_tag="$(echo "$tags" | tail -1)"
  current_ver="$(echo "$current_tag" | sed 's/^v//')"
  latest_ver="$(echo "$latest_tag"   | sed 's/^v//')"

  if [ "$current_ver" != "$latest_ver" ]; then
    newer="$(printf '%s\n%s' "$current_ver" "$latest_ver" | sort -V | tail -1)"
    if [ "$newer" = "$latest_ver" ]; then
      echo "  - ${image}:${current_tag} -> ${latest_tag}"
      echo "${image}|${current_tag}|${latest_tag}" >> "$DOCKER_RESULTS"
    fi
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
      current_stripped="$(echo "$current" | sed 's/^v//')"
      latest_stripped="$(echo "$latest"   | sed 's/^v//')"
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
# ---------------------------------------------------------------------------
echo ""
echo "=== Checking EKS add-on versions ==="

ADDON_RESULTS="$(mktemp)"
K8S_VERSION="$(python3 -c "import json; print(json.load(open('cdk.json'))['context']['kubernetes_version'])" 2>/dev/null || echo "1.35")"

python3 - <<'PY' | while IFS='|' read -r addon_name current_version; do
import re
try:
    with open('gco/stacks/regional_stack.py') as f:
        text = f.read()
except FileNotFoundError:
    raise SystemExit(0)
for m in re.finditer(r'addon_name="([^"]+)".*?addon_version="([^"]+)"', text, re.DOTALL):
    print(f'{m.group(1)}|{m.group(2)}')
PY
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

ADDON_COUNT="$(wc -l < "$ADDON_RESULTS" 2>/dev/null | tr -d ' ')"
[ -z "$ADDON_COUNT" ] && ADDON_COUNT=0

# ---------------------------------------------------------------------------
# Summary + Markdown report
# ---------------------------------------------------------------------------
echo ""
echo "=== Summary ==="
echo "Python packages outdated: $PYTHON_COUNT"
echo "Docker images outdated:   $DOCKER_COUNT"
echo "Helm charts outdated:     $HELM_COUNT"
echo "EKS add-ons outdated:     $ADDON_COUNT"

if [ "$PYTHON_COUNT" -eq 0 ] && [ "$DOCKER_COUNT" -eq 0 ] \
   && [ "$HELM_COUNT" -eq 0 ] && [ "$ADDON_COUNT" -eq 0 ]; then
  echo ""
  echo "All dependencies are up to date."
  rm -f "$DOCKER_RESULTS" "$HELM_RESULTS" "$ADDON_RESULTS"
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

rm -f "$DOCKER_RESULTS" "$HELM_RESULTS" "$ADDON_RESULTS"

if [ -n "${GITHUB_OUTPUT:-}" ]; then
  echo "has_drift=true"            >> "$GITHUB_OUTPUT"
  echo "report_path=$REPORT_FILE"  >> "$GITHUB_OUTPUT"
fi

echo ""
echo "Wrote report to $REPORT_FILE"
