"""
Tests for the shared async subprocess runner used by long-running MCP tools
(`mcp/tools/_long_task.py::_run_long_task`).

Covers four behaviours of the helper:

* the lifecycle path — drains stdout/stderr from a real Python subprocess,
  emits progress messages, increments the progress counter on every
  ``(CREATE|UPDATE|DELETE)_COMPLETE`` line, and returns the success JSON
  on a clean exit;
* the cancellation path — on ``asyncio.CancelledError`` the runner sends
  ``SIGTERM``, waits up to 10 s for graceful shutdown, sends ``SIGKILL`` if
  needed, and re-raises the cancellation;
* the stack-op cancellation disclaimer — when ``is_stack_op=True`` the
  re-raised ``CancelledError`` carries the literal CFN partial-state
  disclaimer so operators know AWS state may be inconsistent;
* path-traversal rejection — any non-flag argv element containing a ``..``
  segment short-circuits before ``asyncio.create_subprocess_exec`` is
  called, returning a structured error JSON.
"""

import asyncio
import contextlib
import json
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure mcp/ is importable, mirroring tests/test_mcp_audit.py and
# tests/test_mcp_feature_flags.py.
sys.path.insert(0, str(Path(__file__).parent.parent / "mcp"))

from tools._long_task import _run_long_task  # noqa: E402


class _FakeProgress(dict):
    """Minimal async-callable stand-in for FastMCP's Progress dependency.

    Subclasses ``dict`` so the JSON encoder used by the audit decorator can
    serialize an instance without complaint when the fake is passed in via
    a wrapper's ``progress=`` keyword. The ``messages`` and ``increments``
    attributes the test code reads from are kept as plain attributes — the
    encoder only sees the empty mapping.
    """

    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []
        self.increments: int = 0

    async def set_message(self, msg: str) -> None:
        self.messages.append(msg)

    async def increment(self) -> None:
        self.increments += 1


class _FakeCtx(dict):
    """Minimal async-callable stand-in for FastMCP's Context dependency.

    Subclasses ``dict`` for the same JSON-serialization reason as
    ``_FakeProgress`` — it lets the audit decorator's argument sanitizer
    handle a ``ctx=`` keyword without the encoder choking on a non-mapping
    object.
    """

    def __init__(self) -> None:
        super().__init__()
        self.infos: list[str] = []

    async def info(self, msg: str) -> None:
        self.infos.append(msg)


# Lifecycle test
async def test_run_long_task_lifecycle() -> None:
    """A short-lived subprocess produces progress messages and a success JSON.

    The child prints a few lines (one of them matching the CFN
    ``CREATE_COMPLETE`` regex) so we can assert both the line-by-line
    progress drain and the increment-on-COMPLETE counting.
    """
    progress = _FakeProgress()
    ctx = _FakeCtx()
    argv = [
        sys.executable,
        "-c",
        (
            "import sys, time\n"
            "print('starting'); sys.stdout.flush()\n"
            "print('CREATE_COMPLETE Foo'); sys.stdout.flush()\n"
            "time.sleep(0.05)\n"
            "print('done'); sys.stdout.flush()\n"
        ),
    ]

    result = await _run_long_task(argv, ctx=ctx, progress=progress, is_stack_op=False)

    assert json.loads(result) == {"status": "ok", "completes": 1}
    assert any("starting" in m for m in progress.messages), progress.messages
    assert any("CREATE_COMPLETE Foo" in m for m in progress.messages), progress.messages
    assert any("done" in m for m in progress.messages), progress.messages
    # Exactly one CREATE_COMPLETE line → exactly one increment.
    assert progress.increments == 1


# Cancellation test
async def test_run_long_task_cancellation() -> None:
    """An in-flight task that sleeps for a minute terminates within 11 s on cancel.

    The 11 s budget is the 10 s SIGTERM grace window the runner enforces
    plus a small safety margin for scheduler jitter.
    """
    progress = _FakeProgress()
    ctx = _FakeCtx()
    argv = [sys.executable, "-c", "import time; time.sleep(60)"]

    coro_task = asyncio.create_task(
        _run_long_task(argv, ctx=ctx, progress=progress, is_stack_op=False)
    )
    # Let the subprocess actually start before we cancel it.
    await asyncio.sleep(0.5)

    t0 = time.monotonic()
    coro_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await coro_task
    elapsed = time.monotonic() - t0

    assert elapsed < 11.0, f"cancellation took {elapsed:.2f}s, expected <11s"


# Cancellation-disclaimer test for stack ops
async def test_run_long_task_cancellation_includes_disclaimer() -> None:
    """When ``is_stack_op=True``, the re-raised CancelledError names the disclaimer.

    Operators need to know that cancellation may have left CloudFormation
    in a partial state so they go check the stack via ``stack_status`` or
    the AWS console.
    """
    progress = _FakeProgress()
    ctx = _FakeCtx()
    argv = [sys.executable, "-c", "import time; time.sleep(60)"]

    coro_task = asyncio.create_task(
        # is_stack_op defaults to True; pass it explicitly for clarity.
        _run_long_task(argv, ctx=ctx, progress=progress, is_stack_op=True)
    )
    await asyncio.sleep(0.5)
    coro_task.cancel()

    try:
        await coro_task
    except asyncio.CancelledError as e:
        assert e.args, "expected CancelledError to carry a disclaimer message"
        msg = str(e.args[0])
        assert "Partial CloudFormation state may remain" in msg, msg
        assert "stack_status" in msg, msg
    else:
        pytest.fail("expected CancelledError, got clean completion")


# Path-traversal rejection test
async def test_run_long_task_rejects_path_traversal() -> None:
    """A ``..`` segment in any non-flag argv element short-circuits before spawn.

    Patches ``asyncio.create_subprocess_exec`` inside the ``_long_task``
    module so we can confirm the runner never reaches the spawn call. The
    structured error JSON also pinpoints which argv index tripped the
    check.
    """
    progress = _FakeProgress()
    ctx = _FakeCtx()
    argv = [sys.executable, "../etc/passwd"]

    with patch(
        "tools._long_task.asyncio.create_subprocess_exec",
        new_callable=AsyncMock,
    ) as mock_spawn:
        result = await _run_long_task(argv, ctx=ctx, progress=progress, is_stack_op=False)
        assert mock_spawn.call_count == 0, "subprocess must not be spawned for traversal argv"

    parsed = json.loads(result)
    assert parsed == {
        "error": "path_traversal_detected",
        "argv_index": 1,
        "value": "../etc/passwd",
    }


# =============================================================================
# Long-running stack lifecycle tools — registration + argv kick-off
# =============================================================================
#
# The deploy/destroy/bootstrap tools register conditionally: deploy_stack /
# deploy_all / bootstrap_cdk under ``GCO_ENABLE_INFRASTRUCTURE_DEPLOY`` and
# destroy_stack / destroy_all under ``GCO_ENABLE_INFRASTRUCTURE_DESTROY``.
# Each one builds a CLI argv and hands it to ``_run_long_task`` to drive the
# subprocess and stream progress through FastMCP's task protocol.
#
# These tests mock ``_run_long_task`` so the suite runs with no AWS access
# and no spawned subprocesses — what we care about here is the constructed
# argv, not the real CDK invocation. The cancellation-disclaimer test below
# drives a real subprocess so we can confirm the disclaimer surfaces.

import importlib  # noqa: E402
import logging  # noqa: E402
import os  # noqa: E402

import run_mcp  # noqa: E402


def _list_tool_names() -> set[str]:
    """Snapshot every registered tool name from the live mcp instance."""
    tools = asyncio.run(run_mcp.mcp._list_tools())
    return {t.name for t in tools}


# =============================================================================
# Absent-by-default registration tests
# =============================================================================


_INFRASTRUCTURE_GATED_TOOLS = (
    "deploy_stack",
    "deploy_all",
    "bootstrap_cdk",
    "destroy_stack",
    "destroy_all",
)


def _force_unregister_infrastructure_tools() -> None:
    """Strip infrastructure-gated tool registrations from the live mcp singleton.

    Earlier tests in the suite may set GCO_ENABLE_INFRASTRUCTURE_DEPLOY /
    GCO_ENABLE_INFRASTRUCTURE_DESTROY and reload run_mcp, which leaves the
    gated tool names registered against the module-level FastMCP instance.
    Default-env assertions in this file need to start from a clean registry,
    so this helper drops the names before each absent-by-default check.
    """
    for name in _INFRASTRUCTURE_GATED_TOOLS:
        with contextlib.suppress(Exception):
            run_mcp.mcp.local_provider.remove_tool(name)


class TestInfrastructureDeployFlag:
    """deploy_stack / deploy_all / bootstrap_cdk register only under the deploy flag."""

    def test_deploy_stack_absent_by_default(self):
        # Clean env (no infrastructure flag) → tool is unregistered.
        for var in (
            "GCO_ENABLE_INFRASTRUCTURE_DEPLOY",
            "GCO_ENABLE_INFRASTRUCTURE_DESTROY",
            "GCO_ENABLE_ALL_TOOLS",
        ):
            os.environ.pop(var, None)
        _force_unregister_infrastructure_tools()
        importlib.reload(run_mcp)
        # Strip again after reload — run_mcp's reload blocks may re-register
        # tools when env vars from a previous test were patched but cleared
        # without unsetting them on the live singleton.
        _force_unregister_infrastructure_tools()

        names = _list_tool_names()
        assert "deploy_stack" not in names
        assert "deploy_all" not in names
        assert "bootstrap_cdk" not in names


class TestInfrastructureDestroyFlag:
    """destroy_stack / destroy_all register only under the destroy flag."""

    def test_destroy_stack_absent_by_default(self):
        for var in (
            "GCO_ENABLE_INFRASTRUCTURE_DEPLOY",
            "GCO_ENABLE_INFRASTRUCTURE_DESTROY",
            "GCO_ENABLE_ALL_TOOLS",
        ):
            os.environ.pop(var, None)
        _force_unregister_infrastructure_tools()
        importlib.reload(run_mcp)
        _force_unregister_infrastructure_tools()

        names = _list_tool_names()
        assert "destroy_stack" not in names
        assert "destroy_all" not in names


# =============================================================================
# argv kick-off tests — mock ``_run_long_task`` and inspect the constructed argv
# =============================================================================


class TestInfrastructureDeployTools:
    """Constructed argv for deploy_stack / deploy_all / bootstrap_cdk."""

    @patch.dict(os.environ, {"GCO_ENABLE_INFRASTRUCTURE_DEPLOY": "true"})
    def test_deploy_stack_argv_includes_require_approval_never_and_yes(self):
        importlib.reload(run_mcp)
        with patch("tools.stacks._run_long_task", new_callable=AsyncMock) as mock_task:
            mock_task.return_value = '{"status": "ok", "completes": 0}'
            asyncio.run(
                run_mcp.deploy_stack(
                    stack_name="gco-us-east-1",
                    ctx=_FakeCtx(),
                    progress=_FakeProgress(),
                )
            )
        argv = mock_task.call_args.args[0]
        assert argv[:4] == ["gco", "stacks", "deploy", "gco-us-east-1"]
        assert "--require-approval" in argv
        assert "never" in argv
        assert "-y" in argv
        # is_stack_op must default to True so cancellation surfaces the disclaimer.
        assert mock_task.call_args.kwargs["is_stack_op"] is True

    @patch.dict(os.environ, {"GCO_ENABLE_INFRASTRUCTURE_DEPLOY": "true"})
    def test_deploy_stack_argv_includes_outputs_file_and_tags(self):
        importlib.reload(run_mcp)
        with patch("tools.stacks._run_long_task", new_callable=AsyncMock) as mock_task:
            mock_task.return_value = '{"status": "ok", "completes": 0}'
            asyncio.run(
                run_mcp.deploy_stack(
                    stack_name="gco-global",
                    yes=False,
                    outputs_file="/tmp/out.json",
                    tags=["Environment=prod", "Owner=ml"],
                    ctx=_FakeCtx(),
                    progress=_FakeProgress(),
                )
            )
        argv = mock_task.call_args.args[0]
        # ``yes=False`` skips the -y flag.
        assert "-y" not in argv
        assert "--outputs-file" in argv
        assert "/tmp/out.json" in argv
        # Each tag becomes a ``--tag value`` pair.
        tag_indices = [i for i, v in enumerate(argv) if v == "--tag"]
        assert len(tag_indices) == 2
        assert {argv[i + 1] for i in tag_indices} == {"Environment=prod", "Owner=ml"}

    @patch.dict(os.environ, {"GCO_ENABLE_INFRASTRUCTURE_DEPLOY": "true"})
    def test_deploy_all_argv_includes_require_approval_never_and_yes(self):
        importlib.reload(run_mcp)
        with patch("tools.stacks._run_long_task", new_callable=AsyncMock) as mock_task:
            mock_task.return_value = '{"status": "ok", "completes": 0}'
            asyncio.run(
                run_mcp.deploy_all(
                    parallel=True,
                    max_workers=8,
                    ctx=_FakeCtx(),
                    progress=_FakeProgress(),
                )
            )
        argv = mock_task.call_args.args[0]
        assert argv[:3] == ["gco", "stacks", "deploy-all"]
        assert "--require-approval" in argv
        assert "never" in argv
        assert "-y" in argv
        assert "--parallel" in argv
        assert "--max-workers" in argv
        assert "8" in argv
        assert mock_task.call_args.kwargs["is_stack_op"] is True

    @patch.dict(os.environ, {"GCO_ENABLE_INFRASTRUCTURE_DEPLOY": "true"})
    def test_bootstrap_cdk_argv_includes_region_and_account(self):
        importlib.reload(run_mcp)
        with patch("tools.stacks._run_long_task", new_callable=AsyncMock) as mock_task:
            mock_task.return_value = '{"status": "ok", "completes": 0}'
            asyncio.run(
                run_mcp.bootstrap_cdk(
                    region="eu-west-1",
                    account="123456789012",
                    ctx=_FakeCtx(),
                    progress=_FakeProgress(),
                )
            )
        argv = mock_task.call_args.args[0]
        assert argv[:3] == ["gco", "stacks", "bootstrap"]
        assert "--region" in argv
        assert "eu-west-1" in argv
        assert "--account" in argv
        assert "123456789012" in argv
        # Bootstrap is a stack op too — cancellation should carry the disclaimer.
        assert mock_task.call_args.kwargs["is_stack_op"] is True

    @patch.dict(os.environ, {"GCO_ENABLE_INFRASTRUCTURE_DEPLOY": "true"})
    def test_bootstrap_cdk_omits_account_when_not_provided(self):
        importlib.reload(run_mcp)
        with patch("tools.stacks._run_long_task", new_callable=AsyncMock) as mock_task:
            mock_task.return_value = '{"status": "ok", "completes": 0}'
            asyncio.run(
                run_mcp.bootstrap_cdk(
                    region="us-west-2",
                    ctx=_FakeCtx(),
                    progress=_FakeProgress(),
                )
            )
        argv = mock_task.call_args.args[0]
        assert "--account" not in argv


class TestInfrastructureDestroyTools:
    """Constructed argv for destroy_stack / destroy_all."""

    @patch.dict(os.environ, {"GCO_ENABLE_INFRASTRUCTURE_DESTROY": "true"})
    def test_destroy_stack_argv_includes_yes(self):
        importlib.reload(run_mcp)
        with patch("tools.stacks._run_long_task", new_callable=AsyncMock) as mock_task:
            mock_task.return_value = '{"status": "ok", "completes": 0}'
            asyncio.run(
                run_mcp.destroy_stack(
                    stack_name="gco-us-east-1",
                    ctx=_FakeCtx(),
                    progress=_FakeProgress(),
                )
            )
        argv = mock_task.call_args.args[0]
        assert argv[:4] == ["gco", "stacks", "destroy", "gco-us-east-1"]
        assert "-y" in argv
        assert mock_task.call_args.kwargs["is_stack_op"] is True

    @patch.dict(os.environ, {"GCO_ENABLE_INFRASTRUCTURE_DESTROY": "true"})
    def test_destroy_stack_omits_yes_when_disabled(self):
        importlib.reload(run_mcp)
        with patch("tools.stacks._run_long_task", new_callable=AsyncMock) as mock_task:
            mock_task.return_value = '{"status": "ok", "completes": 0}'
            asyncio.run(
                run_mcp.destroy_stack(
                    stack_name="gco-us-east-1",
                    yes=False,
                    ctx=_FakeCtx(),
                    progress=_FakeProgress(),
                )
            )
        argv = mock_task.call_args.args[0]
        assert "-y" not in argv

    @patch.dict(os.environ, {"GCO_ENABLE_INFRASTRUCTURE_DESTROY": "true"})
    def test_destroy_all_argv_includes_yes_and_parallel(self):
        importlib.reload(run_mcp)
        with patch("tools.stacks._run_long_task", new_callable=AsyncMock) as mock_task:
            mock_task.return_value = '{"status": "ok", "completes": 0}'
            asyncio.run(
                run_mcp.destroy_all(
                    parallel=True,
                    max_workers=4,
                    ctx=_FakeCtx(),
                    progress=_FakeProgress(),
                )
            )
        argv = mock_task.call_args.args[0]
        assert argv[:3] == ["gco", "stacks", "destroy-all"]
        assert "-y" in argv
        assert "--parallel" in argv
        assert "--max-workers" in argv
        assert "4" in argv
        assert mock_task.call_args.kwargs["is_stack_op"] is True


# =============================================================================
# Cancellation-disclaimer integration test for the stack lifecycle path
# =============================================================================


class TestDeployStackCancellation:
    """Cancellation of a real subprocess surfaces the partial-state disclaimer."""

    @patch.dict(os.environ, {"GCO_ENABLE_INFRASTRUCTURE_DEPLOY": "true"})
    def test_deploy_stack_cancellation_includes_disclaimer(self):
        """Drive ``_run_long_task`` against a long-sleep subprocess and cancel.

        Patches ``asyncio.create_subprocess_exec`` inside the long-task
        helper so the runner spawns a Python sleep instead of the real
        ``gco stacks deploy``. This keeps the test hermetic while still
        exercising the same drain + cancel + disclaimer path.
        """
        importlib.reload(run_mcp)

        progress = _FakeProgress()
        ctx = _FakeCtx()

        async def _drive() -> None:
            real_create = asyncio.create_subprocess_exec

            async def _fake_create(*_argv: str, **kwargs: object):
                # Replace the gco invocation with a long-running sleep.
                # Forward the captured kwargs (stdout/stderr=PIPE) untouched.
                return await real_create(
                    sys.executable,
                    "-c",
                    "import time; time.sleep(60)",
                    **kwargs,  # type: ignore[arg-type]
                )

            with patch(
                "tools._long_task.asyncio.create_subprocess_exec",
                side_effect=_fake_create,
            ):
                coro = asyncio.create_task(
                    run_mcp.deploy_stack(
                        stack_name="gco-us-east-1",
                        ctx=ctx,
                        progress=progress,
                    )
                )
                # Let the subprocess actually start before cancelling.
                await asyncio.sleep(0.5)
                coro.cancel()
                try:
                    await coro
                except asyncio.CancelledError as e:
                    msg = str(e.args[0]) if e.args else ""
                    assert "Partial CloudFormation state may remain" in msg, msg
                    assert "stack_status" in msg, msg
                    return
                pytest.fail("expected CancelledError, got clean completion")

        asyncio.run(_drive())


# =============================================================================
# Audit log task-id correlation for deploy_stack
# =============================================================================


class TestDeployStackAuditTaskId:
    """When request_context.meta.task_id is set, the audit entry exposes it."""

    @patch.dict(os.environ, {"GCO_ENABLE_INFRASTRUCTURE_DEPLOY": "true"})
    def test_deploy_stack_audit_includes_task_id(self, caplog, monkeypatch):
        importlib.reload(run_mcp)

        # Patch the audit decorator's context lookup so the entry sees a
        # synthetic FastMCP context with a known task_id. ``audit`` is
        # imported lazily here so the reload above wins.
        import audit  # noqa: F401  - imported for monkeypatch target

        meta = MagicMock(spec=["task_id"])
        meta.task_id = "deploy-task-xyz"
        request_context = MagicMock()
        request_context.meta = meta
        fake_ctx = MagicMock()
        fake_ctx.request_context = request_context
        fake_ctx.request_id = "req-deploy-001"
        fake_ctx.client_id = "kiro"

        monkeypatch.setattr("audit._try_get_fastmcp_context", lambda: fake_ctx)

        # Stub the long-task helper so the call resolves without a real
        # subprocess. We're auditing the entry shape, not the spawn.
        with (
            caplog.at_level(logging.INFO, logger="gco.mcp.audit"),
            patch("tools.stacks._run_long_task", new_callable=AsyncMock) as mock_task,
        ):
            mock_task.return_value = '{"status": "ok", "completes": 0}'
            asyncio.run(
                run_mcp.deploy_stack(
                    stack_name="gco-us-east-1",
                    ctx=_FakeCtx(),
                    progress=_FakeProgress(),
                )
            )

        entries = [
            json.loads(r.message)
            for r in caplog.records
            if r.name == "gco.mcp.audit"
            and json.loads(r.message).get("event") == "mcp.tool.invocation"
        ]
        assert entries, "expected an audit entry for deploy_stack"
        entry = entries[-1]
        assert entry["tool"] == "deploy_stack"
        assert entry["task_id"] == "deploy-task-xyz"
        assert entry["request_id"] == "req-deploy-001"
        assert entry["client_id"] == "kiro"


# =============================================================================
# Task-config mode contract — deploy/destroy tools must be optional, not required
# =============================================================================


class TestInfrastructureToolTaskMode:
    """The five long-running infrastructure tools opt into the FastMCP task
    protocol with ``mode="optional"`` rather than ``mode="required"``.

    Required mode locks out clients that don't speak the task protocol —
    notably the GCO MCP orchestrator's ``call_tool`` proxy and any other
    client that calls a tool synchronously without sending ``task_meta``.
    Optional mode lets clients with task-protocol support poll
    ``tasks://gco/{task_id}`` while clients without it run the tool
    inline with progress streamed through the FastMCP Progress dependency.

    If the running fastmcp version doesn't expose ``task_config`` on its
    registered Tool objects, the tests skip gracefully — TaskConfig is
    best-effort wired in the tool module.
    """

    def _expect_optional_mode(self, tool_name: str) -> None:
        """Assert ``tool_name`` is registered with ``task_config.mode == "optional"``."""
        tools = asyncio.run(run_mcp.mcp._list_tools())
        tool = next((t for t in tools if t.name == tool_name), None)
        assert tool is not None, f"{tool_name} must register under its feature flag"
        cfg = getattr(tool, "task_config", None)
        if cfg is None:
            pytest.skip("fastmcp build doesn't expose task_config on registered tools")
        assert getattr(cfg, "mode", None) == "optional", (
            f"{tool_name}.task_config.mode must be 'optional' so clients without "
            "task-protocol support can call it inline"
        )

    @patch.dict(os.environ, {"GCO_ENABLE_INFRASTRUCTURE_DEPLOY": "true"})
    def test_deploy_stack_task_mode_is_optional(self):
        importlib.reload(run_mcp)
        self._expect_optional_mode("deploy_stack")

    @patch.dict(os.environ, {"GCO_ENABLE_INFRASTRUCTURE_DEPLOY": "true"})
    def test_deploy_all_task_mode_is_optional(self):
        importlib.reload(run_mcp)
        self._expect_optional_mode("deploy_all")

    @patch.dict(os.environ, {"GCO_ENABLE_INFRASTRUCTURE_DEPLOY": "true"})
    def test_bootstrap_cdk_task_mode_is_optional(self):
        importlib.reload(run_mcp)
        self._expect_optional_mode("bootstrap_cdk")

    @patch.dict(os.environ, {"GCO_ENABLE_INFRASTRUCTURE_DESTROY": "true"})
    def test_destroy_stack_task_mode_is_optional(self):
        importlib.reload(run_mcp)
        self._expect_optional_mode("destroy_stack")

    @patch.dict(os.environ, {"GCO_ENABLE_INFRASTRUCTURE_DESTROY": "true"})
    def test_destroy_all_task_mode_is_optional(self):
        importlib.reload(run_mcp)
        self._expect_optional_mode("destroy_all")
