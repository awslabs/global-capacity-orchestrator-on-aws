#!/usr/bin/env python3
"""
GCO MCP Server — Exposes the GCO CLI as MCP tools for LLM interaction.

Run with:
    python mcp/run_mcp.py

Add to Kiro MCP config (.kiro/settings/mcp.json):
    {
        "mcpServers": {
            "gco": {
                "command": "python3",
                "args": ["mcp/run_mcp.py"],
                "cwd": "/path/to/GCO"
            }
        }
    }

This file is a thin entrypoint. The actual implementation lives in the
``mcp/`` directory:

    mcp/
    ├── server.py          — FastMCP instance and instructions
    ├── audit.py           — Audit logging, sanitization, decorator
    ├── iam.py             — IAM role assumption
    ├── cli_runner.py      — _run_cli() subprocess wrapper
    ├── version.py         — Project version management
    ├── tools/             — MCP tool definitions (one file per domain)
    │   ├── jobs.py
    │   ├── capacity.py
    │   ├── inference.py
    │   ├── costs.py
    │   ├── stacks.py
    │   ├── storage.py
    │   └── models.py
    └── resources/         — MCP resource definitions (one file per scheme)
        ├── docs.py        — docs:// (documentation + examples with metadata)
        ├── source.py      — source:// (full source code browser)
        ├── k8s.py         — k8s:// (cluster manifests)
        ├── iam_policies.py — iam:// (IAM policy templates)
        ├── infra.py       — infra:// (Dockerfiles, Helm, CI/CD)
        ├── ci.py          — ci:// (GitHub Actions, workflows)
        ├── demos.py       — demos:// (walkthroughs, scripts)
        ├── clients.py     — clients:// (API client examples)
        ├── scripts.py     — scripts:// (utility scripts)
        ├── tests.py       — tests:// (test suite docs and patterns)
        └── config.py      — config:// (CDK config, feature toggles, env vars)
"""

import sys
from pathlib import Path

# Ensure the project root is on the path so CLI modules can be imported
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Ensure the mcp/ directory is on the path so internal modules can import
# each other without a package prefix (avoids shadowing the ``mcp`` PyPI
# package that fastmcp depends on).
MCP_DIR = Path(__file__).parent
sys.path.insert(0, str(MCP_DIR))

# --- Re-export everything the existing tests expect on ``run_mcp.*`` ---

from audit import (  # noqa: E402, F401
    _MCP_SERVER_VERSION,
    _sanitize_arguments,
    audit_logged,
    audit_logger,
    emit_startup_log,
)
from iam import assume_mcp_role  # noqa: E402, F401
from server import mcp  # noqa: E402, F401
from version import get_project_version  # noqa: E402, F401

# Re-export the project version for tests that check run_mcp._PROJECT_VERSION
_PROJECT_VERSION = get_project_version()

# --- Register all tools and resources ---

from resources import register_all_resources  # noqa: E402
from tools import register_all_tools  # noqa: E402

register_all_tools()
register_all_resources()

# --- Re-export tool functions for backward compat with existing tests ---
# Tests call e.g. run_mcp.list_jobs(), so we import them into this namespace.

# Conditionally re-export reserve_capacity if it was registered.
# contextlib.suppress is the idiomatic "swallow this exception" form.
import contextlib as _contextlib  # noqa: E402

from tools.capacity import (  # noqa: E402, F401
    ai_recommend,
    capacity_status,
    check_capacity,
    list_reservations,
    recommend_region,
    reservation_check,
    spot_prices,
)
from tools.costs import cost_by_region, cost_forecast, cost_summary, cost_trend  # noqa: E402, F401
from tools.inference import (  # noqa: E402, F401
    canary_deploy,
    chat_inference,
    delete_inference,
    deploy_inference,
    inference_health,
    inference_status,
    invoke_inference,
    list_endpoint_models,
    list_inference_endpoints,
    promote_canary,
    rollback_canary,
    scale_inference,
    start_inference,
    stop_inference,
    update_inference_image,
)
from tools.jobs import (  # noqa: E402, F401
    cluster_health,
    delete_job,
    get_job,
    get_job_events,
    get_job_logs,
    list_jobs,
    queue_status,
    submit_job_api,
    submit_job_sqs,
)
from tools.models import get_model_uri, list_models  # noqa: E402, F401
from tools.stacks import (  # noqa: E402, F401
    fsx_status,
    list_stacks,
    setup_cluster_access,
    stack_status,
)
from tools.storage import list_file_systems, list_storage_contents  # noqa: E402, F401

with _contextlib.suppress(ImportError):
    from tools.capacity import reserve_capacity  # noqa: F401

# Also make reserve_capacity available after module reload (tests use
# importlib.reload with GCO_ENABLE_CAPACITY_PURCHASE=true)
import os as _os  # noqa: E402

if _os.environ.get("GCO_ENABLE_CAPACITY_PURCHASE", "").lower() == "true":
    import importlib as _importlib  # noqa: E402

    from tools import capacity as _cap_mod  # noqa: E402

    _importlib.reload(_cap_mod)
    if hasattr(_cap_mod, "reserve_capacity"):
        reserve_capacity = _cap_mod.reserve_capacity  # noqa: F811

# --- Re-export resource directory constants for tests ---
from resources.ci import (  # noqa: E402, F401
    GITHUB_ACTIONS_DIR,
    GITHUB_CODEQL_DIR,
    GITHUB_DIR,
    GITHUB_ISSUE_TEMPLATE_DIR,
    GITHUB_KIND_DIR,
    GITHUB_SCRIPTS_DIR,
    GITHUB_WORKFLOWS_DIR,
)
from resources.docs import DOCS_DIR, EXAMPLES_DIR  # noqa: E402, F401
from resources.infra import DOCKERFILES_DIR, HELM_CHARTS_FILE  # noqa: E402, F401
from resources.k8s import MANIFESTS_DIR  # noqa: E402, F401

# Declare every name that is intentionally re-exported for tests and
# downstream consumers. This silences unused-import warnings from static
# analyzers that don't recognise the per-line ruff/flake8 markers above.
__all__ = [
    "DOCKERFILES_DIR",
    "DOCS_DIR",
    "EXAMPLES_DIR",
    "GITHUB_ACTIONS_DIR",
    "GITHUB_CODEQL_DIR",
    "GITHUB_DIR",
    "GITHUB_ISSUE_TEMPLATE_DIR",
    "GITHUB_KIND_DIR",
    "GITHUB_SCRIPTS_DIR",
    "GITHUB_WORKFLOWS_DIR",
    "HELM_CHARTS_FILE",
    "MANIFESTS_DIR",
    "_MCP_SERVER_VERSION",
    "_PROJECT_VERSION",
    "_sanitize_arguments",
    "ai_recommend",
    "assume_mcp_role",
    "audit_logged",
    "audit_logger",
    "canary_deploy",
    "capacity_status",
    "chat_inference",
    "check_capacity",
    "cluster_health",
    "cost_by_region",
    "cost_forecast",
    "cost_summary",
    "cost_trend",
    "delete_inference",
    "delete_job",
    "deploy_inference",
    "emit_startup_log",
    "fsx_status",
    "get_job",
    "get_job_events",
    "get_job_logs",
    "get_model_uri",
    "get_project_version",
    "inference_health",
    "inference_status",
    "invoke_inference",
    "list_endpoint_models",
    "list_file_systems",
    "list_inference_endpoints",
    "list_jobs",
    "list_models",
    "list_reservations",
    "list_stacks",
    "list_storage_contents",
    "mcp",
    "promote_canary",
    "queue_status",
    "recommend_region",
    "reservation_check",
    "rollback_canary",
    "scale_inference",
    "setup_cluster_access",
    "spot_prices",
    "stack_status",
    "start_inference",
    "stop_inference",
    "submit_job_api",
    "submit_job_sqs",
    "update_inference_image",
]

# --- Startup ---

emit_startup_log()

try:
    assume_mcp_role()
except Exception:
    raise

# =============================================================================
# ENTRYPOINT
# =============================================================================

if __name__ == "__main__":
    mcp.run()
