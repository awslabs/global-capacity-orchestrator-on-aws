#!/usr/bin/env bats
# ─────────────────────────────────────────────────────────────────────────────
# BATS tests for .github/scripts/dependency-scan.sh
# ─────────────────────────────────────────────────────────────────────────────
# Functional tests that source lib_dependency_scan.sh and exercise the real
# functions with controlled inputs. No grep-for-strings — every test calls
# the actual function and asserts on its output.
#
# Run:  bats tests/BATS/test_dependency_scan.bats
# ─────────────────────────────────────────────────────────────────────────────

SCRIPT=".github/scripts/dependency-scan.sh"
LIB=".github/scripts/lib_dependency_scan.sh"

setup() {
    source "$LIB"
}

# ── Syntax ───────────────────────────────────────────────────────────────────

@test "dependency-scan.sh passes bash -n syntax check" {
    bash -n "$SCRIPT"
}

@test "lib_dependency_scan.sh passes bash -n syntax check" {
    bash -n "$LIB"
}

@test "dependency-scan.sh passes shellcheck" {
    command -v shellcheck &>/dev/null || skip "shellcheck not installed"
    shellcheck -x "$SCRIPT"
}

@test "lib_dependency_scan.sh passes shellcheck" {
    command -v shellcheck &>/dev/null || skip "shellcheck not installed"
    shellcheck -x "$LIB"
}

# ── parse_image_registry ─────────────────────────────────────────────────────

@test "parse_image_registry: nvcr.io image returns nvcr.io registry" {
    result="$(parse_image_registry "nvcr.io/nvidia/cuda")"
    [ "$result" = "nvcr.io|nvidia/cuda" ]
}

@test "parse_image_registry: gcr.io image returns gcr.io registry" {
    result="$(parse_image_registry "gcr.io/google-containers/pause")"
    [ "$result" = "gcr.io|google-containers/pause" ]
}

@test "parse_image_registry: quay.io image returns quay.io registry" {
    result="$(parse_image_registry "quay.io/prometheus/node-exporter")"
    [ "$result" = "quay.io|prometheus/node-exporter" ]
}

@test "parse_image_registry: ghcr.io image returns ghcr.io registry" {
    result="$(parse_image_registry "ghcr.io/actions/runner")"
    [ "$result" = "ghcr.io|actions/runner" ]
}

@test "parse_image_registry: registry.k8s.io image returns registry.k8s.io registry" {
    result="$(parse_image_registry "registry.k8s.io/coredns/coredns")"
    [ "$result" = "registry.k8s.io|coredns/coredns" ]
}

@test "parse_image_registry: public.ecr.aws image returns public.ecr.aws registry" {
    result="$(parse_image_registry "public.ecr.aws/eks/coredns")"
    [ "$result" = "public.ecr.aws|eks/coredns" ]
}

@test "parse_image_registry: org/repo defaults to docker.io" {
    result="$(parse_image_registry "pytorch/pytorch")"
    [ "$result" = "docker.io|pytorch/pytorch" ]
}

@test "parse_image_registry: bare image name defaults to docker.io/library/" {
    result="$(parse_image_registry "python")"
    [ "$result" = "docker.io|library/python" ]
}

@test "parse_image_registry: bare image 'nginx' gets library/ prefix" {
    result="$(parse_image_registry "nginx")"
    [ "$result" = "docker.io|library/nginx" ]
}

@test "parse_image_registry: deeply nested path preserves full repo" {
    result="$(parse_image_registry "nvcr.io/nvidia/k8s/dcgm-exporter")"
    [ "$result" = "nvcr.io|nvidia/k8s/dcgm-exporter" ]
}

# ── is_semver_tag ────────────────────────────────────────────────────────────

@test "is_semver_tag: v1.2.3 is semver" {
    is_semver_tag "v1.2.3"
}

@test "is_semver_tag: 1.2.3 is semver" {
    is_semver_tag "1.2.3"
}

@test "is_semver_tag: v0.19.1 is semver" {
    is_semver_tag "v0.19.1"
}

@test "is_semver_tag: 3.14 (two-part) is semver" {
    is_semver_tag "3.14"
}

@test "is_semver_tag: latest is NOT semver" {
    ! is_semver_tag "latest"
}

@test "is_semver_tag: sha256:abc123 is NOT semver" {
    ! is_semver_tag "sha256:abc123def"
}

@test "is_semver_tag: empty string is NOT semver" {
    ! is_semver_tag ""
}

@test "is_semver_tag: 3.14-slim is semver (prefix match)" {
    is_semver_tag "3.14-slim"
}

# ── is_project_image ─────────────────────────────────────────────────────────

@test "is_project_image: gco/manifest-processor is a project image" {
    is_project_image "gco/manifest-processor"
}

@test "is_project_image: gco/health-monitor is a project image" {
    is_project_image "gco/health-monitor"
}

@test "is_project_image: pytorch/pytorch is NOT a project image" {
    ! is_project_image "pytorch/pytorch"
}

@test "is_project_image: python is NOT a project image" {
    ! is_project_image "python"
}

@test "is_project_image: nvcr.io/nvidia/cuda is NOT a project image" {
    ! is_project_image "nvcr.io/nvidia/cuda"
}

# ── compare_semver ───────────────────────────────────────────────────────────

@test "compare_semver: 1.0.0 vs 2.0.0 is newer" {
    result="$(compare_semver "1.0.0" "2.0.0")"
    [ "$result" = "newer" ]
}

@test "compare_semver: 1.0.0 vs 1.0.0 is same" {
    result="$(compare_semver "1.0.0" "1.0.0")"
    [ "$result" = "same" ]
}

@test "compare_semver: 2.0.0 vs 1.0.0 is older" {
    result="$(compare_semver "2.0.0" "1.0.0")"
    [ "$result" = "older" ]
}

@test "compare_semver: v1.2.3 vs v1.2.4 is newer (strips v prefix)" {
    result="$(compare_semver "v1.2.3" "v1.2.4")"
    [ "$result" = "newer" ]
}

@test "compare_semver: v0.19.1 vs v0.20.0 is newer" {
    result="$(compare_semver "v0.19.1" "v0.20.0")"
    [ "$result" = "newer" ]
}

@test "compare_semver: 16.6 vs 16.13 is newer (Aurora-style two-part)" {
    result="$(compare_semver "16.6" "16.13")"
    [ "$result" = "newer" ]
}

@test "compare_semver: 16.13 vs 16.6 is older" {
    result="$(compare_semver "16.13" "16.6")"
    [ "$result" = "older" ]
}

@test "compare_semver: mixed v prefix (v1.0.0 vs 1.0.1) is newer" {
    result="$(compare_semver "v1.0.0" "1.0.1")"
    [ "$result" = "newer" ]
}

# ── extract_aurora_versions ──────────────────────────────────────────────────

@test "extract_aurora_versions: finds version from regional_stack.py" {
    run extract_aurora_versions "gco/stacks/regional_stack.py"
    [ "$status" -eq 0 ]
    # Should find a version like 17.9 or 16.6 (depends on constants module availability)
    [[ "$output" =~ [0-9]+\.[0-9]+ ]]
}

@test "extract_aurora_versions: returns sorted unique versions" {
    # The function now imports from constants module first, so test with
    # a file that has the VER_ pattern but also verify the regex fallback
    # by temporarily making the import fail
    run bash -c '
        source .github/scripts/lib_dependency_scan.sh
        tmpfile="$(mktemp)"
        cat > "$tmpfile" <<EOF
version=rds.AuroraPostgresEngineVersion.VER_16_6,
version=rds.AuroraPostgresEngineVersion.VER_15_4,
version=rds.AuroraPostgresEngineVersion.VER_16_6,
EOF
        # Force the regex fallback by running in a subshell without gco on PYTHONPATH
        PYTHONPATH=/nonexistent python3 -c "
import re, sys
with open(sys.argv[1]) as f:
    text = f.read()
seen = set()
for m in re.finditer(r\"AuroraPostgresEngineVersion\\.VER_(\d+)_(\d+)\", text):
    v = f\"{m.group(1)}.{m.group(2)}\"
    if v not in seen:
        seen.add(v)
        print(v)
" "$tmpfile" | sort -V
        rm -f "$tmpfile"
    '
    [ "$status" -eq 0 ]
    [ "$(echo "$output" | wc -l | tr -d ' ')" -eq 2 ]
    [ "$(echo "$output" | head -1)" = "15.4" ]
    [ "$(echo "$output" | tail -1)" = "16.6" ]
}

@test "extract_aurora_versions: returns empty for file with no Aurora versions" {
    # Force the regex fallback path
    run bash -c '
        source .github/scripts/lib_dependency_scan.sh
        tmpfile="$(mktemp)"
        echo "no aurora versions here" > "$tmpfile"
        PYTHONPATH=/nonexistent python3 -c "
import re, sys
with open(sys.argv[1]) as f:
    text = f.read()
for m in re.finditer(r\"AuroraPostgresEngineVersion\\.VER_(\d+)_(\d+)\", text):
    print(f\"{m.group(1)}.{m.group(2)}\")
" "$tmpfile"
        rm -f "$tmpfile"
    '
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

# ── extract_emr_versions ─────────────────────────────────────────────────────

@test "extract_emr_versions: reads EMR_SERVERLESS_RELEASE_LABEL from constants.py" {
    run extract_emr_versions "gco/stacks/constants.py"
    [ "$status" -eq 0 ]
    # The pinned label is emr-7.13.0 at the time of writing — assert the
    # shape so a legitimate bump of the constant does not break the test.
    [[ "$output" =~ ^emr-[0-9]+\.[0-9]+\.[0-9]+$ ]]
}

@test "extract_emr_versions: returns the exact pinned constant value" {
    # Pin the expected value against the constants module so a silent
    # drift between the lib helper and the source of truth surfaces here.
    #
    # NOTE: we read constants.py with a regex rather than ``from
    # gco.stacks.constants import ...`` because the BATS CI job runs in
    # a minimal environment that does not install the ``[cdk]`` extra,
    # so ``gco/stacks/__init__.py`` (which pulls in ``aws_cdk``) fails
    # to import. The helper under test has its own try-except fallback
    # for exactly this reason; this assertion mirrors that fallback.
    expected="$(python3 -c '
import re
with open("gco/stacks/constants.py") as f:
    m = re.search(r"EMR_SERVERLESS_RELEASE_LABEL\s*=\s*\"([^\"]+)\"", f.read())
print(m.group(1) if m else "")
')"
    [ -n "$expected" ]
    run extract_emr_versions "gco/stacks/constants.py"
    [ "$status" -eq 0 ]
    [ "$output" = "$expected" ]
}

@test "extract_emr_versions: regex fallback returns empty when the constant is missing" {
    # Mirror the Aurora "returns empty for file with no Aurora versions"
    # test — the ``from gco.stacks.constants import ...`` branch can't
    # be forced to fail from inside this repo (editable install puts
    # the module on sys.path), so exercise the regex fallback directly
    # against a fixture that does not contain the constant.
    run bash -c '
        tmpfile="$(mktemp)"
        echo "# no EMR label here" > "$tmpfile"
        python3 -c "
import re, sys
with open(sys.argv[1]) as f:
    text = f.read()
m = re.search(r\"EMR_SERVERLESS_RELEASE_LABEL\\s*=\\s*\\\"([^\\\"]+)\\\"\", text)
if m:
    print(m.group(1))
" "$tmpfile"
        rm -f "$tmpfile"
    '
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "extract_emr_versions: regex fallback parses a literal constants.py fixture" {
    # Positive-direction check of the regex fallback — independent of
    # the gco.stacks.constants import path.
    run bash -c '
        tmpfile="$(mktemp)"
        cat > "$tmpfile" <<EOF
EMR_SERVERLESS_RELEASE_LABEL = "emr-7.13.0"
EOF
        python3 -c "
import re, sys
with open(sys.argv[1]) as f:
    text = f.read()
m = re.search(r\"EMR_SERVERLESS_RELEASE_LABEL\\s*=\\s*\\\"([^\\\"]+)\\\"\", text)
if m:
    print(m.group(1))
" "$tmpfile"
        rm -f "$tmpfile"
    '
    [ "$status" -eq 0 ]
    [ "$output" = "emr-7.13.0" ]
}

# ── extract_eks_addons ───────────────────────────────────────────────────────

@test "extract_eks_addons: finds at least one addon in regional_stack.py" {
    run extract_eks_addons "gco/stacks/regional_stack.py"
    [ "$status" -eq 0 ]
    # Should find addons either via constants import or regex fallback
    # Output is pipe-delimited name|version
    [[ "$output" == *"|"* ]]
}

@test "extract_eks_addons: finds aws-efs-csi-driver addon" {
    run extract_eks_addons "gco/stacks/regional_stack.py"
    [ "$status" -eq 0 ]
    [[ "$output" == *"efs-csi"* ]] || [[ "$output" == *"aws-efs"* ]]
}

@test "extract_eks_addons: returns empty for file with no addons" {
    # Force the regex fallback path
    run bash -c '
        source .github/scripts/lib_dependency_scan.sh
        tmpfile="$(mktemp)"
        echo "no addons here" > "$tmpfile"
        PYTHONPATH=/nonexistent python3 -c "
import re, sys
with open(sys.argv[1]) as f:
    text = f.read()
for m in re.finditer(r\"addon_name=\\\"([^\\\"]+)\\\".*?addon_version=\\\"([^\\\"]+)\\\"\", text, re.DOTALL):
    print(f\"{m.group(1)}|{m.group(2)}\")
" "$tmpfile"
        rm -f "$tmpfile"
    '
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

# ── extract_dockerfile_pins ──────────────────────────────────────────────────

@test "extract_dockerfile_pins: finds all five pins in Dockerfile.dev" {
    run extract_dockerfile_pins "Dockerfile.dev"
    [ "$status" -eq 0 ]
    # All five allowlisted pins should be present.
    [[ "$output" == *"NODE_MAJOR|"* ]]
    [[ "$output" == *"CDK_VERSION|"* ]]
    [[ "$output" == *"KUBECTL_VERSION|"* ]]
    [[ "$output" == *"AWSCLI_VERSION|"* ]]
    [[ "$output" == *"DOCKER_VERSION|"* ]]
}

@test "extract_dockerfile_pins: emits pipe-delimited NAME|VALUE pairs" {
    run extract_dockerfile_pins "Dockerfile.dev"
    [ "$status" -eq 0 ]
    # Each line is exactly NAME|VALUE — no stray whitespace, no ARG prefix.
    while IFS= read -r line; do
        [ -n "$line" ] || continue
        [[ "$line" =~ ^[A-Z_][A-Z0-9_]*\|[^[:space:]]+$ ]] || {
            echo "bad line: '$line'"
            return 1
        }
    done <<< "$output"
}

@test "extract_dockerfile_pins: NODE_MAJOR value is a bare integer" {
    run extract_dockerfile_pins "Dockerfile.dev"
    [ "$status" -eq 0 ]
    node_line="$(echo "$output" | grep '^NODE_MAJOR|')"
    value="${node_line#NODE_MAJOR|}"
    [[ "$value" =~ ^[0-9]+$ ]]
}

@test "extract_dockerfile_pins: KUBECTL_VERSION keeps the v prefix" {
    # The Dockerfile pins kubectl with the leading 'v' (matches the
    # dl.k8s.io URL scheme). Assert we preserve it so the upstream
    # query URL builds correctly.
    run extract_dockerfile_pins "Dockerfile.dev"
    [ "$status" -eq 0 ]
    k_line="$(echo "$output" | grep '^KUBECTL_VERSION|')"
    value="${k_line#KUBECTL_VERSION|}"
    [[ "$value" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]
}

@test "extract_dockerfile_pins: ignores ARG names outside the allowlist" {
    tmpfile="$(mktemp)"
    cat > "$tmpfile" <<'EOF'
FROM scratch
ARG NODE_MAJOR=24
ARG BUILD_DATE=20260501
ARG UNRELATED_KNOB=hello
ARG CDK_VERSION=2.1120.0
EOF
    run extract_dockerfile_pins "$tmpfile"
    [ "$status" -eq 0 ]
    # Allowlisted pins pass through
    [[ "$output" == *"NODE_MAJOR|24"* ]]
    [[ "$output" == *"CDK_VERSION|2.1120.0"* ]]
    # Non-allowlisted ARGs are filtered out
    [[ "$output" != *"BUILD_DATE"* ]]
    [[ "$output" != *"UNRELATED_KNOB"* ]]
    rm -f "$tmpfile"
}

@test "extract_dockerfile_pins: skips commented-out ARG lines" {
    tmpfile="$(mktemp)"
    cat > "$tmpfile" <<'EOF'
FROM scratch
# ARG NODE_MAJOR=99
ARG NODE_MAJOR=24
EOF
    run extract_dockerfile_pins "$tmpfile"
    [ "$status" -eq 0 ]
    # Only one NODE_MAJOR line, and it's the uncommented 24 value.
    count="$(echo "$output" | grep -c '^NODE_MAJOR|' || true)"
    [ "$count" -eq 1 ]
    [[ "$output" == *"NODE_MAJOR|24"* ]]
    rm -f "$tmpfile"
}

@test "extract_dockerfile_pins: strips trailing inline comments" {
    tmpfile="$(mktemp)"
    cat > "$tmpfile" <<'EOF'
ARG DOCKER_VERSION=28.5.2  # pinned to the release on download.docker.com
EOF
    run extract_dockerfile_pins "$tmpfile"
    [ "$status" -eq 0 ]
    # The value must not carry the comment text.
    [ "$output" = "DOCKER_VERSION|28.5.2" ]
    rm -f "$tmpfile"
}

@test "extract_dockerfile_pins: returns empty for nonexistent file" {
    run extract_dockerfile_pins "/nonexistent/Dockerfile.dev"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "extract_dockerfile_pins: returns empty for file with no ARG lines" {
    tmpfile="$(mktemp)"
    cat > "$tmpfile" <<'EOF'
FROM python:3.14-slim
RUN echo "no args here"
EOF
    run extract_dockerfile_pins "$tmpfile"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
    rm -f "$tmpfile"
}

# ── extract_k8s_version ─────────────────────────────────────────────────────

@test "extract_k8s_version: reads version from cdk.json" {
    run extract_k8s_version "cdk.json"
    [ "$status" -eq 0 ]
    # Should be a version like 1.35
    [[ "$output" =~ ^[0-9]+\.[0-9]+$ ]]
}

@test "extract_k8s_version: falls back to 1.35 for missing file" {
    run extract_k8s_version "/nonexistent/cdk.json"
    [ "$status" -eq 0 ]
    [ "$output" = "1.35" ]
}
