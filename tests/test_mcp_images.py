"""
Tests for the container image registry MCP tools (mcp/tools/images.py).

Covers four behaviours:

* Default-env registration — only the read-only and administrative
  ``images_*`` tools are registered when no flags are set.
* Image-publish gating — ``images_build`` and ``images_push`` register
  only when ``GCO_ENABLE_IMAGE_PUBLISH=true``.
* Destructive gating — ``images_cleanup`` / ``images_prune`` /
  ``images_delete_tag`` / ``images_delete_repo`` register only when
  ``GCO_ENABLE_DESTRUCTIVE_OPERATIONS=true``.
* Destructive tools emit ``ctx.warning(...)`` so the audit log captures
  a ``client_messages`` entry with ``level: "warning"``.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import json
import logging
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure mcp/ is importable, mirroring the other test modules.
sys.path.insert(0, str(Path(__file__).parent.parent / "mcp"))

import run_mcp  # noqa: E402


def _list_tool_names() -> set[str]:
    """Snapshot every registered tool name from the live mcp instance."""
    tools = asyncio.run(run_mcp.mcp._list_tools())
    return {t.name for t in tools}


# Every gated image tool that this file exercises. Reused as the cleanup
# target for ``_clean_gated_image_tools`` so default-env tests see an
# unpolluted registry no matter what order the suite runs in — earlier
# files (e.g. ``test_mcp_destructive_gating.py``) might set the
# destructive flag and leak ``images_*`` tool registrations into the
# module-level ``mcp`` singleton.
_GATED_IMAGE_TOOLS = (
    "images_build",
    "images_push",
    "images_cleanup",
    "images_prune",
    "images_delete_tag",
    "images_delete_repo",
)


def _force_unregister_gated_image_tools() -> None:
    for name in _GATED_IMAGE_TOOLS:
        with contextlib.suppress(Exception):
            run_mcp.mcp.local_provider.remove_tool(name)


# =============================================================================
# Default-env registration: read-only and administrative tools are present;
# gated build/push and destructive variants are NOT.
# =============================================================================


class TestImageToolsDefaultEnv:
    """Under default env, only the unconditional images_* tools register."""

    @pytest.fixture(autouse=True)
    def _clean_gated_image_tools(self):
        _force_unregister_gated_image_tools()
        importlib.reload(run_mcp)
        _force_unregister_gated_image_tools()

    def test_unconditional_tools_present(self):
        names = _list_tool_names()
        # Read-only "safe" tools.
        for n in (
            "images_list",
            "images_tags",
            "images_describe",
            "images_uri",
            "images_replication_get",
            "images_replication_status",
            "images_orphans",
        ):
            assert n in names, f"expected unconditional tool {n!r} to be registered"
        # Administrative "low-risk" tools.
        for n in (
            "images_init",
            "images_lifecycle_get",
            "images_lifecycle_set",
            "images_replication_sync",
        ):
            assert n in names, f"expected unconditional tool {n!r} to be registered"

    def test_images_build_absent_by_default(self):
        names = _list_tool_names()
        # Co-located coverage of the publish-gated pair.
        assert "images_build" not in names
        assert "images_push" not in names

    def test_images_delete_repo_absent_by_default(self):
        names = _list_tool_names()
        # Co-located coverage of every destructive image tool.
        assert "images_delete_repo" not in names
        assert "images_delete_tag" not in names
        assert "images_cleanup" not in names
        assert "images_prune" not in names


# =============================================================================
# Gated registration — image-publish flag exposes images_build / images_push
# =============================================================================


class TestImagePublishGating:
    """Build/push tools register only under ``GCO_ENABLE_IMAGE_PUBLISH``."""

    @patch.dict(os.environ, {"GCO_ENABLE_IMAGE_PUBLISH": "true"})
    def test_images_build_present_when_image_publish_flag_set(self):
        # Reload run_mcp so the gated registrations and re-exports both run.
        importlib.reload(run_mcp)
        names = _list_tool_names()
        assert "images_build" in names
        assert "images_push" in names
        # The reload block also rebinds the module-level names so callers
        # (and audit-log tests) can reach them through ``run_mcp.``.
        assert hasattr(run_mcp, "images_build")
        assert hasattr(run_mcp, "images_push")

    @patch.dict(os.environ, {"GCO_ENABLE_IMAGE_PUBLISH": "true"})
    def test_images_build_task_mode_is_optional(self):
        """The publish-gated tools opt in to the FastMCP task protocol.

        The tool's ``task_config.mode`` should be ``"optional"`` so MCP
        clients can choose between synchronous and background-task
        execution. If the running fastmcp version doesn't expose
        ``task_config`` on its registered Tool objects, the test skips
        gracefully — TaskConfig is best-effort wired in the tool module.
        """
        importlib.reload(run_mcp)
        tools = asyncio.run(run_mcp.mcp._list_tools())
        build = next((t for t in tools if t.name == "images_build"), None)
        assert build is not None, "images_build must register under the flag"
        cfg = getattr(build, "task_config", None)
        if cfg is None:
            pytest.skip("fastmcp build doesn't expose task_config on registered tools")
        assert getattr(cfg, "mode", None) == "optional"


# =============================================================================
# Gated registration — destructive flag exposes the four destructive tools
# =============================================================================


class TestImageDestructiveGating:
    """Cleanup/prune/delete tools register only under destructive flag."""

    @patch.dict(os.environ, {"GCO_ENABLE_DESTRUCTIVE_OPERATIONS": "true"})
    def test_images_delete_repo_present_when_destructive_flag_set(self):
        importlib.reload(run_mcp)
        names = _list_tool_names()
        for n in (
            "images_cleanup",
            "images_prune",
            "images_delete_tag",
            "images_delete_repo",
        ):
            assert n in names, f"expected destructive tool {n!r} to be registered"
        # Module-level rebinds also work.
        assert hasattr(run_mcp, "images_delete_tag")
        assert hasattr(run_mcp, "images_delete_repo")
        assert hasattr(run_mcp, "images_cleanup")
        assert hasattr(run_mcp, "images_prune")


# =============================================================================
# ctx.warning capture — destructive tools should record a client_messages
# entry with level="warning" via the audit middleware spy.
# =============================================================================


def _audit_invocation_entries(caplog) -> list[dict]:
    """Return every ``mcp.tool.invocation`` entry in caplog."""
    out: list[dict] = []
    for record in caplog.records:
        if record.name != "gco.mcp.audit":
            continue
        try:
            entry = json.loads(record.message)
        except json.JSONDecodeError:
            continue
        if entry.get("event") == "mcp.tool.invocation":
            out.append(entry)
    return out


class TestImageDestructiveCtxWarning:
    """Destructive tools emit ``ctx.warning`` so the audit entry has client_messages."""

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"GCO_ENABLE_DESTRUCTIVE_OPERATIONS": "true"})
    async def test_images_destructive_tools_emit_ctx_warning(self, caplog):
        # Pull in the gated tools fresh so the FastMCP middleware sees the
        # registered tool when the Client routes the call.
        importlib.reload(run_mcp)

        # Stub ImageManager.delete_tag so the call doesn't reach boto3.
        fake_manager = MagicMock()
        fake_manager.delete_tag.return_value = {
            "name": "gco/my-app",
            "tag": "old",
            "deleted": [{"digest": "sha256:abc", "tag": "old"}],
            "failures": [],
        }

        from fastmcp import Client

        with (
            caplog.at_level(logging.INFO, logger="gco.mcp.audit"),
            patch("cli.images.get_image_manager", return_value=fake_manager),
        ):
            async with Client(run_mcp.mcp) as client:
                result = await client.call_tool(
                    "images_delete_tag", {"name": "my-app", "tag": "old"}
                )

        # The tool wraps the manager's return shape in a JSON string.
        assert result.content, "expected content from images_delete_tag"
        text_payload = result.content[0].text
        assert "sha256:abc" in text_payload

        invocations = _audit_invocation_entries(caplog)
        delete_entries = [e for e in invocations if e.get("tool") == "images_delete_tag"]
        assert delete_entries, "expected an audit entry for images_delete_tag"
        entry = delete_entries[-1]
        assert entry["status"] == "success"
        msgs = entry.get("client_messages") or []
        warnings = [m for m in msgs if m.get("level") == "warning"]
        assert warnings, f"expected a warning in client_messages, got {msgs!r}"
        # The warning text mentions the destructive intent so operators see why
        # the tool flagged the call.
        assert any("cannot be undone" in m.get("message", "") for m in warnings)
