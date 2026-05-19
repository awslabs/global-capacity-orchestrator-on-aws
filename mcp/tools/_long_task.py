"""Shared async subprocess runner for long-running MCP tools that
stream progress updates through FastMCP's Progress dependency."""

from __future__ import annotations

import asyncio
import json
import re
import signal
from collections.abc import Sequence
from typing import Any

# <pyflowchart-code-diagram> BEGIN - auto-inserted, do not edit
# Flowchart(s) generated from this file:
#   * ``_run_long_task`` -> ``diagrams/code_diagrams/mcp/tools/_long_task._run_long_task.html``
#     (PNG: ``diagrams/code_diagrams/mcp/tools/_long_task._run_long_task.png``)
# Regenerate with ``python diagrams/code_diagrams/generate.py``.
# <pyflowchart-code-diagram> END


_CFN_COMPLETE_RE = re.compile(r"(CREATE|UPDATE|DELETE)_COMPLETE")
_CANCEL_GRACE_SECONDS = 10
_PARTIAL_STATE_DISCLAIMER = (
    "Partial CloudFormation state may remain — inspect via stack_status or the AWS console."
)


def _argv_has_traversal(argv: Sequence[str]) -> tuple[int, str] | None:
    """Return (index, offending_value) for the first non-flag arg with a ``..``
    segment, else None.

    A non-flag element is one that does not start with ``-``. The check matches
    the convention used by ``mcp.cli_runner._run_cli`` so the rejection shape
    is consistent across short- and long-running tools.
    """
    for i, v in enumerate(argv):
        if v.startswith("-"):
            continue
        if ".." in v.split("/") or ".." in v.split("\\"):
            return (i, v[:100])
    return None


async def _run_long_task(
    argv: Sequence[str],
    *,
    ctx: Any,
    progress: Any,
    is_stack_op: bool = True,
) -> str:
    """Run a long-running subprocess, streaming progress through FastMCP.

    Spawns ``argv`` via :func:`asyncio.create_subprocess_exec`, drains stdout
    and stderr concurrently, forwards every line as a progress message
    (truncated to 200 chars), increments the progress counter on every line
    matching ``(CREATE|UPDATE|DELETE)_COMPLETE``, and surfaces stderr lines
    through ``ctx.info`` so MCP clients see them.

    On cancellation, sends ``SIGTERM``, waits up to ``_CANCEL_GRACE_SECONDS``
    for graceful shutdown, sends ``SIGKILL`` on timeout, cancels the drain
    tasks, and re-raises ``CancelledError``. When ``is_stack_op`` is True, the
    re-raised exception's message includes a CloudFormation partial-state
    disclaimer so operators know AWS state may be inconsistent.

    Returns a JSON string:
      * ``{"status": "ok", "completes": <count>}`` on a clean zero exit code.
      * ``{"error": "exit_code=<rc>", "completes": <count>}`` on non-zero exit.
      * ``{"error": "path_traversal_detected", "argv_index": <i>, "value": <v>}``
        when any non-flag argv element contains a ``..`` path segment; in that
        case no subprocess is spawned.
    """
    hit = _argv_has_traversal(argv)
    if hit is not None:
        idx, val = hit
        return json.dumps({"error": "path_traversal_detected", "argv_index": idx, "value": val})

    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    completes = 0

    async def _drain(stream: asyncio.StreamReader | None, label: str) -> None:
        nonlocal completes
        assert stream is not None  # PIPE is set above for both streams
        async for raw in stream:
            line = raw.decode("utf-8", errors="replace").rstrip()
            if not line:
                continue
            await progress.set_message(line[:200])
            if _CFN_COMPLETE_RE.search(line):
                completes += 1
                await progress.increment()
            if label == "stderr":
                await ctx.info(f"stderr: {line[:200]}")

    drains = [
        asyncio.create_task(_drain(proc.stdout, "stdout")),
        asyncio.create_task(_drain(proc.stderr, "stderr")),
    ]

    try:
        rc = await proc.wait()
        await asyncio.gather(*drains, return_exceptions=True)
    except asyncio.CancelledError:
        if proc.returncode is None:
            proc.send_signal(signal.SIGTERM)
            try:
                await asyncio.wait_for(proc.wait(), timeout=_CANCEL_GRACE_SECONDS)
            except TimeoutError:
                if proc.returncode is None:
                    proc.send_signal(signal.SIGKILL)
                await proc.wait()
        for d in drains:
            d.cancel()
        if is_stack_op:
            raise asyncio.CancelledError(_PARTIAL_STATE_DISCLAIMER) from None
        raise

    if rc != 0:
        return json.dumps({"error": f"exit_code={rc}", "completes": completes})
    return json.dumps({"status": "ok", "completes": completes})
