# CI Helper Scripts

Shell scripts invoked by GitHub Actions workflows. Separated from the workflows themselves so they can be tested independently (via BATS) and reused.

## Table of Contents

- [Files](#files)
- [Testing](#testing)
- [Adding a New Script](#adding-a-new-script)

## Files

| File | Invoked By | Description |
|------|------------|-------------|
| `dependency-scan.sh` | `deps-scan.yml` (monthly) | Checks for outdated Python packages, Docker images, Helm charts, and EKS add-on versions. Writes a Markdown report and sets `has_drift=true` on `$GITHUB_OUTPUT` if any are outdated. |
| `lib_dependency_scan.sh` | `dependency-scan.sh` | Sourceable helper functions — image registry parsing (`parse_image_registry`), semver comparison (`compare_semver`), tag filtering (`is_semver_tag`, `is_project_image`). Extracted so BATS tests can exercise the logic without running the full scan. |

## Testing

The helper functions in `lib_dependency_scan.sh` are tested by BATS:

```bash
# From the repository root
bats tests/BATS/test_dependency_scan.bats
```

## Adding a New Script

1. Create the script in this directory
2. Make it executable: `chmod +x .github/scripts/my-script.sh`
3. If it has reusable functions, extract them into a `lib_*.sh` file
4. Add BATS tests in `tests/BATS/`
5. Reference it from the workflow with `run: bash .github/scripts/my-script.sh`
