# CodeQL Configuration

Configuration for GitHub's Code Scanning (CodeQL) analysis.

## Table of Contents

- [Files](#files)
- [What Gets Scanned](#what-gets-scanned)
- [Query Packs](#query-packs)
- [Excluded Rules](#excluded-rules)
- [Modifying the Config](#modifying-the-config)

## Files

| File | Description |
|------|-------------|
| `codeql-config.yml` | Paths to scan, paths to skip, query packs, and rule exclusions |

## What Gets Scanned

Only hand-authored Python code:

- `gco/`, `cli/`, `mcp/`, `lambda/`, `scripts/`, `app.py`

Excluded: `cdk.out/`, build directories, caches, tests, demo scripts.

## Query Packs

- `security-and-quality` — includes both security rules and maintainability queries

## Excluded Rules

| Rule | Reason |
|------|--------|
| `py/clear-text-logging-sensitive-data` | False positives on logging registry names and secret ARNs (not secret values) |
| `py/incomplete-url-substring-sanitization` | URL access control is handled by API Gateway IAM and ALB allowlists, not substring checks |

Each exclusion is documented inline in `codeql-config.yml` with the specific files and rationale.

## Modifying the Config

- To scan additional paths: add them to the `paths:` list
- To exclude a new rule: add an entry to `query-filters:` with `exclude: id:` and document why
- To add a query pack: add it to the `queries:` list
