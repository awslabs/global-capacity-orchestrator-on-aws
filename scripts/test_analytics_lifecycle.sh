#!/usr/bin/env bash
# One-line shell wrapper around test_analytics_lifecycle.py — preserves the
# .sh extension for operators who want to invoke the script via bash tooling
# (make targets, CI hooks, etc.). All args and environment passthrough to python.
set -euo pipefail
exec python3 "$(dirname "$0")/test_analytics_lifecycle.py" "$@"
