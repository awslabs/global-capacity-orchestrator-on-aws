#!/usr/bin/env bash
# =============================================================================
# lib_dependency_scan.sh — sourceable functions for dependency-scan.sh
# =============================================================================
# Extracted from dependency-scan.sh so BATS tests can exercise the real logic
# without running the full scan (which needs pip, skopeo, helm, AWS creds).
#
# Usage:
#   source .github/scripts/lib_dependency_scan.sh
# =============================================================================

# parse_image_registry <image>
#
# Given a Docker image name (without tag), prints "registry|repo" where
# registry is the domain and repo is the path within that registry.
#
# Examples:
#   parse_image_registry "nvcr.io/nvidia/cuda"        → "nvcr.io|nvidia/cuda"
#   parse_image_registry "pytorch/pytorch"            → "docker.io|pytorch/pytorch"
#   parse_image_registry "python"                     → "docker.io|library/python"
#   parse_image_registry "public.ecr.aws/eks/coredns" → "public.ecr.aws|eks/coredns"
parse_image_registry() {
  local image="$1"
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
  echo "${registry}|${repo}"
}

# is_semver_tag <tag>
#
# Returns 0 (true) if the tag looks like a semver version (v1.2.3, 1.2, etc).
# Returns 1 (false) otherwise.
is_semver_tag() {
  echo "$1" | grep -qE "^v?[0-9]+\.[0-9]+(\.[0-9]+)?"
}

# is_project_image <image>
#
# Returns 0 (true) if the image is built by this project (gco/*).
is_project_image() {
  echo "$1" | grep -q "^gco/"
}

# compare_semver <current> <candidate>
#
# Prints "newer" if candidate is strictly newer than current (by sort -V),
# "same" if they're equal, "older" otherwise.
compare_semver() {
  local current="${1#v}"
  local candidate="${2#v}"
  if [ "$current" = "$candidate" ]; then
    echo "same"
    return
  fi
  local newest
  newest="$(printf '%s\n%s' "$current" "$candidate" | sort -V | tail -1)"
  if [ "$newest" = "$candidate" ]; then
    echo "newer"
  else
    echo "older"
  fi
}

# extract_aurora_versions <file>
#
# Extracts Aurora PostgreSQL engine versions from the constants module.
# Prints one "major.minor" per line, sorted and deduplicated.
# Falls back to reading constants.py directly if the module can't be imported.
extract_aurora_versions() {
  local file="${1:-gco/stacks/regional_stack.py}"
  python3 -c "
import sys
try:
    from gco.stacks.constants import AURORA_POSTGRES_VERSION_DISPLAY
    print(AURORA_POSTGRES_VERSION_DISPLAY)
except ImportError:
    # Fallback: read constants.py directly
    import re, os
    constants_path = os.path.join(os.path.dirname(sys.argv[1]), 'constants.py')
    if os.path.exists(constants_path):
        with open(constants_path) as f:
            text = f.read()
        m = re.search(r'AURORA_POSTGRES_VERSION_DISPLAY\s*=\s*\"([^\"]+)\"', text)
        if m:
            print(m.group(1))
    else:
        # Last resort: scan the file for VER_XX_Y patterns
        with open(sys.argv[1]) as f:
            text = f.read()
        seen = set()
        for m in re.finditer(r'AuroraPostgresEngineVersion\.VER_(\d+)_(\d+)', text):
            v = f'{m.group(1)}.{m.group(2)}'
            if v not in seen:
                seen.add(v)
                print(v)
" "$file" 2>/dev/null | sort -V
}

# extract_eks_addons <file>
#
# Extracts EKS addon name|version pairs from the constants module.
# Falls back to reading constants.py directly if the module can't be imported.
# Prints one "addon_name|addon_version" per line.
extract_eks_addons() {
  local file="${1:-gco/stacks/regional_stack.py}"
  python3 -c "
import sys
try:
    from gco.stacks.constants import (
        EKS_ADDON_POD_IDENTITY_AGENT,
        EKS_ADDON_METRICS_SERVER,
        EKS_ADDON_EFS_CSI_DRIVER,
        EKS_ADDON_CLOUDWATCH_OBSERVABILITY,
        EKS_ADDON_FSX_CSI_DRIVER,
    )
    addons = [
        ('eks-pod-identity-agent', EKS_ADDON_POD_IDENTITY_AGENT),
        ('metrics-server', EKS_ADDON_METRICS_SERVER),
        ('aws-efs-csi-driver', EKS_ADDON_EFS_CSI_DRIVER),
        ('amazon-cloudwatch-observability', EKS_ADDON_CLOUDWATCH_OBSERVABILITY),
        ('aws-fsx-csi-driver', EKS_ADDON_FSX_CSI_DRIVER),
    ]
    for name, version in addons:
        print(f'{name}|{version}')
except ImportError:
    # Fallback: read constants.py directly
    import re, os
    constants_path = os.path.join(os.path.dirname(sys.argv[1]), 'constants.py')
    if os.path.exists(constants_path):
        with open(constants_path) as f:
            text = f.read()
        # Map constant names to addon names
        mapping = {
            'EKS_ADDON_POD_IDENTITY_AGENT': 'eks-pod-identity-agent',
            'EKS_ADDON_METRICS_SERVER': 'metrics-server',
            'EKS_ADDON_EFS_CSI_DRIVER': 'aws-efs-csi-driver',
            'EKS_ADDON_CLOUDWATCH_OBSERVABILITY': 'amazon-cloudwatch-observability',
            'EKS_ADDON_FSX_CSI_DRIVER': 'aws-fsx-csi-driver',
        }
        for const_name, addon_name in mapping.items():
            m = re.search(const_name + r'\s*=\s*\"([^\"]+)\"', text)
            if m:
                print(f'{addon_name}|{m.group(1)}')
    else:
        # Last resort: scan the file for inline addon_name/addon_version pairs
        with open(sys.argv[1]) as f:
            text = f.read()
        for m in re.finditer(r'addon_name=\"([^\"]+)\".*?addon_version=\"([^\"]+)\"', text, re.DOTALL):
            print(f'{m.group(1)}|{m.group(2)}')
" "$file" 2>/dev/null
}

# extract_k8s_version [cdk_json_path]
#
# Reads the kubernetes_version from cdk.json. Falls back to "1.35".
extract_k8s_version() {
  local cdk="${1:-cdk.json}"
  python3 -c "import json; print(json.load(open('$cdk'))['context']['kubernetes_version'])" 2>/dev/null || echo "1.35"
}

# extract_dockerfile_pins <dockerfile>
#
# Parses ``ARG <NAME>=<VALUE>`` lines from the given Dockerfile and emits
# ``NAME|VALUE`` for each pin we care about. The allowlist below is
# intentional — random build-time ARGs (e.g. ``BUILD_DATE``) would add
# noise to the drift report.
#
# The line-anchor (``^ARG``) and single-line Python regex avoid matching
# ``ARG`` appearing inside a comment or a RUN heredoc. Leading whitespace
# is permitted so a future ``RUN --mount=…`` or multi-stage FROM line
# doesn't break the scan silently.
#
# Example output for Dockerfile.dev:
#
#     NODE_MAJOR|24
#     CDK_VERSION|2.1120.0
#     KUBECTL_VERSION|v1.35.4
#     AWSCLI_VERSION|2.32.2
#     DOCKER_VERSION|28.5.2
extract_dockerfile_pins() {
  local file="${1:-Dockerfile.dev}"
  [ -f "$file" ] || return 0
  python3 -c "
import re, sys
allowlist = {
    'NODE_MAJOR',
    'CDK_VERSION',
    'KUBECTL_VERSION',
    'AWSCLI_VERSION',
    'DOCKER_VERSION',
}
with open(sys.argv[1]) as f:
    for line in f:
        # Strip trailing inline comments but keep the ARG value itself.
        stripped = line.split('#', 1)[0]
        m = re.match(r'^\s*ARG\s+([A-Z_][A-Z0-9_]*)=(\S+)\s*$', stripped)
        if not m:
            continue
        name, value = m.group(1), m.group(2)
        if name in allowlist:
            print(f'{name}|{value}')
" "$file" 2>/dev/null
}

# extract_emr_versions <file>
#
# Extracts the pinned EMR Serverless release label from the constants module.
# Prints the label (e.g. ``emr-7.13.0``) on a single line. Falls back to
# reading constants.py directly if the module can't be imported.
extract_emr_versions() {
  local file="${1:-gco/stacks/constants.py}"
  python3 -c "
import sys
try:
    from gco.stacks.constants import EMR_SERVERLESS_RELEASE_LABEL
    print(EMR_SERVERLESS_RELEASE_LABEL)
except ImportError:
    import re, os
    constants_path = os.path.join(os.path.dirname(sys.argv[1]), 'constants.py') if 'constants.py' not in sys.argv[1] else sys.argv[1]
    if os.path.exists(constants_path):
        with open(constants_path) as f:
            text = f.read()
        m = re.search(r'EMR_SERVERLESS_RELEASE_LABEL\s*=\s*\"([^\"]+)\"', text)
        if m:
            print(m.group(1))
" "$file" 2>/dev/null
}
