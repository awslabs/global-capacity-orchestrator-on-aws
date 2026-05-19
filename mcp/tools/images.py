"""Container image registry MCP tools.

All tools wrap ``cli/images.py::ImageManager`` so the MCP layer never
re-implements the underlying ECR/runtime logic. Read-only and
administrative tools are unconditional. Build/push tools register only
when ``GCO_ENABLE_IMAGE_PUBLISH`` is set; destructive tools register
only when ``GCO_ENABLE_DESTRUCTIVE_OPERATIONS`` is set.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from audit import audit_logged
from feature_flags import (
    FLAG_DESTRUCTIVE_OPERATIONS,
    FLAG_IMAGE_PUBLISH,
    is_enabled,
)
from server import mcp

from tools._long_task import _run_long_task

# FastMCP's Progress / Context dependencies are optional from this
# module's perspective — when ``fastmcp[tasks]`` is reachable they
# inject real instances per call; otherwise the gated build/push tools
# still register but rely on caller-provided fakes (the test path).
try:
    from fastmcp.server.dependencies import CurrentContext, Progress
except ImportError:  # pragma: no cover - degraded fastmcp install
    CurrentContext = None  # type: ignore[assignment]
    Progress = None  # type: ignore[misc,assignment]

# TaskConfig is best-effort wired so MCP clients that opt into the task
# protocol can run build/push as background tasks. If the import path
# moves between fastmcp versions, the tools register without it.
try:
    from fastmcp.server.tasks.config import TaskConfig

    _TASK_CONFIG_OPTIONAL: Any = TaskConfig(mode="optional")
except ImportError:  # pragma: no cover - degraded fastmcp install
    _TASK_CONFIG_OPTIONAL = None


def _get_manager() -> Any:
    """Lazy-import ``cli.images.get_image_manager`` so MCP server
    import doesn't pull boto3 prematurely.
    """
    from cli.images import get_image_manager

    return get_image_manager()


# =============================================================================
# Read-only tools — Risk_Tier "safe"
# =============================================================================


@mcp.tool(tags={"safe", "images"})
@audit_logged
async def images_list() -> str:
    """`gco images list` — list every gco/* repository in ECR."""
    return await asyncio.to_thread(lambda: json.dumps(_get_manager().list_repos()))


@mcp.tool(tags={"safe", "images"})
@audit_logged
async def images_tags(name: str) -> str:
    """`gco images tags` — list tags within a repository.

    Args:
        name: Repository name (without the ``gco/`` prefix).
    """
    return await asyncio.to_thread(lambda: json.dumps(_get_manager().list_tags(name)))


@mcp.tool(tags={"safe", "images"})
@audit_logged
async def images_describe(name: str, tag: str) -> str:
    """`gco images describe` — full ECR details for a single image tag.

    Args:
        name: Repository name (without the ``gco/`` prefix).
        tag: Image tag.
    """
    return await asyncio.to_thread(lambda: json.dumps(_get_manager().describe(name, tag)))


@mcp.tool(tags={"safe", "images"})
@audit_logged
async def images_uri(name: str, tag: str = "latest") -> str:
    """`gco images uri` — return the registry URI for an image. No AWS calls.

    Args:
        name: Repository name (without the ``gco/`` prefix).
        tag: Image tag. Defaults to ``latest``.
    """
    return await asyncio.to_thread(
        lambda: json.dumps({"uri": _get_manager().get_uri(name, tag=tag)})
    )


@mcp.tool(tags={"safe", "images"})
@audit_logged
async def images_replication_get() -> str:
    """`gco images replication get` — current ECR replication configuration."""
    return await asyncio.to_thread(lambda: json.dumps(_get_manager().replication_get()))


@mcp.tool(tags={"safe", "images"})
@audit_logged
async def images_replication_status() -> str:
    """`gco images replication status` — per-image replication status across project repos."""
    return await asyncio.to_thread(lambda: json.dumps(_get_manager().replication_status()))


@mcp.tool(tags={"safe", "images"})
@audit_logged
async def images_orphans(threshold_days: int = 30) -> str:
    """`gco images orphans` — list gco/* tags older than ``threshold_days`` with no references.

    Args:
        threshold_days: Age threshold in days. Defaults to 30.
    """
    return await asyncio.to_thread(
        lambda: json.dumps(_get_manager().orphans(threshold_days=threshold_days))
    )


# =============================================================================
# Administrative tools — Risk_Tier "low-risk"
# =============================================================================


@mcp.tool(tags={"low-risk", "images"})
@audit_logged
async def images_init(name: str, retain: bool = False) -> str:
    """`gco images init` — create the project ECR repo idempotently with default lifecycle.

    Args:
        name: Repository name (without the ``gco/`` prefix).
        retain: When True, mark the repository with ``gco:retain=true`` so it
            survives stack destroys.
    """
    return await asyncio.to_thread(lambda: json.dumps(_get_manager().init(name, retain=retain)))


@mcp.tool(tags={"low-risk", "images"})
@audit_logged
async def images_lifecycle_get(name: str) -> str:
    """`gco images lifecycle get` — print the lifecycle policy on a repository.

    Args:
        name: Repository name (without the ``gco/`` prefix).
    """
    return await asyncio.to_thread(lambda: json.dumps(_get_manager().lifecycle_get(name)))


@mcp.tool(tags={"low-risk", "images"})
@audit_logged
async def images_lifecycle_set(name: str, policy: dict[str, Any]) -> str:
    """`gco images lifecycle set` — replace the lifecycle policy on a repository.

    Args:
        name: Repository name (without the ``gco/`` prefix).
        policy: ECR lifecycle policy document as a dict.
    """
    return await asyncio.to_thread(lambda: json.dumps(_get_manager().lifecycle_set(name, policy)))


@mcp.tool(tags={"low-risk", "images"})
@audit_logged
async def images_replication_sync() -> str:
    """`gco images replication sync` — apply the standard gco/* replication rule."""
    return await asyncio.to_thread(lambda: json.dumps(_get_manager().replication_sync()))


# =============================================================================
# Image publish — gated by GCO_ENABLE_IMAGE_PUBLISH
# =============================================================================
#
# build/push are long-running data-upload operations. They run via
# ``_run_long_task`` so progress messages stream back through the
# FastMCP Progress dependency.

if is_enabled(FLAG_IMAGE_PUBLISH):
    # Build the decorator kwargs dict so we only pass ``task=...`` when
    # TaskConfig was importable on this fastmcp version.
    _publish_decorator_kwargs: dict[str, Any] = {"tags": {"image", "images"}}
    if _TASK_CONFIG_OPTIONAL is not None:
        _publish_decorator_kwargs["task"] = _TASK_CONFIG_OPTIONAL

    if Progress is not None and CurrentContext is not None:

        @mcp.tool(**_publish_decorator_kwargs)  # type: ignore[untyped-decorator]
        @audit_logged
        async def images_build(
            context: str,
            name: str,
            tag: str | None = None,
            dockerfile: str = "Dockerfile",
            platform: str = "linux/amd64",
            retain: bool = False,
            *,
            ctx: Any = CurrentContext(),
            progress: Any = Progress(),
        ) -> str:
            """[gated by GCO_ENABLE_IMAGE_PUBLISH] long-running, data-upload.

            `gco images build` — build a container image and push to ECR.

            Args:
                context: Build context directory.
                name: Image name (lowercase letters, digits, dashes; max 63 chars).
                tag: Image tag (defaults to git short SHA, else ``latest``).
                dockerfile: Path to the Dockerfile, relative to ``context``.
                platform: ``--platform`` argument for the build.
                retain: When True, mark the repository with ``gco:retain=true``
                    so it survives stack destroys.
            """
            argv = ["gco", "images", "build", context, "--name", name]
            if tag:
                argv += ["--tag", tag]
            argv += ["--dockerfile", dockerfile, "--platform", platform]
            if retain:
                argv.append("--retain")
            return await _run_long_task(argv, ctx=ctx, progress=progress, is_stack_op=False)

        @mcp.tool(**_publish_decorator_kwargs)  # type: ignore[untyped-decorator]
        @audit_logged
        async def images_push(
            name: str,
            tag: str,
            local_image: str,
            retain: bool = False,
            *,
            ctx: Any = CurrentContext(),
            progress: Any = Progress(),
        ) -> str:
            """[gated by GCO_ENABLE_IMAGE_PUBLISH] long-running, data-upload.

            `gco images push` — push an already-built local image to the project ECR repo.

            Args:
                name: Image name (lowercase letters, digits, dashes; max 63 chars).
                tag: Image tag.
                local_image: Source image reference on the local container runtime.
                retain: When True, mark the repository with ``gco:retain=true``
                    so it survives stack destroys.
            """
            argv = [
                "gco",
                "images",
                "push",
                name,
                "--tag",
                tag,
                "--local-image",
                local_image,
            ]
            if retain:
                argv.append("--retain")
            return await _run_long_task(argv, ctx=ctx, progress=progress, is_stack_op=False)


# =============================================================================
# Destructive image tools — gated by GCO_ENABLE_DESTRUCTIVE_OPERATIONS
# =============================================================================


async def _ctx_warning(message: str) -> None:
    """Emit ``ctx.warning(...)`` from inside a tool body, no-op when no Context.

    Tools wrapped here are short-lived enough that we don't need the full
    ``_run_long_task`` stack — we just want operators (and the audit log)
    to see a warning when destructive work runs.
    """
    import contextlib as _contextlib

    try:
        from fastmcp.server.dependencies import get_context

        ctx = get_context()
    except Exception:
        return
    with _contextlib.suppress(Exception):
        await ctx.warning(message)


if is_enabled(FLAG_DESTRUCTIVE_OPERATIONS):

    @mcp.tool(tags={"destructive", "images"})
    @audit_logged
    async def images_delete_tag(name: str, tag: str) -> str:
        """[gated by GCO_ENABLE_DESTRUCTIVE_OPERATIONS] destructive.

        `gco images delete-tag` — delete a single tag from a repository.
        Cannot be undone — the image manifest is removed from ECR.

        Args:
            name: Repository name (without the ``gco/`` prefix).
            tag: Image tag to delete.
        """
        await _ctx_warning(f"Deleting tag {tag!r} from gco/{name} — this cannot be undone.")
        return await asyncio.to_thread(lambda: json.dumps(_get_manager().delete_tag(name, tag)))

    @mcp.tool(tags={"destructive", "images"})
    @audit_logged
    async def images_delete_repo(name: str, force: bool = False) -> str:
        """[gated by GCO_ENABLE_DESTRUCTIVE_OPERATIONS] destructive.

        `gco images delete-repo` — delete a whole repository.
        Cannot be undone — the repo and (when ``force=True``) every image
        inside it are permanently removed from ECR.

        Args:
            name: Repository name (without the ``gco/`` prefix).
            force: When True, also delete every image inside the repo.
        """
        await _ctx_warning(
            f"Deleting repository gco/{name} (force={force}) — this cannot be undone."
        )
        return await asyncio.to_thread(
            lambda: json.dumps(_get_manager().delete_repo(name, force=force))
        )

    @mcp.tool(tags={"destructive", "images"})
    @audit_logged
    async def images_cleanup(name: str | None = None, all: bool = False) -> str:
        """[gated by GCO_ENABLE_DESTRUCTIVE_OPERATIONS] destructive.

        `gco images cleanup` — remove every untagged image across one or all project repos.
        Cannot be undone — untagged image manifests are permanently deleted.

        Args:
            name: Repository name to clean (without the ``gco/`` prefix). Required
                unless ``all=True``.
            all: When True, clean every project repository.
        """
        scope = "all repos" if all else f"gco/{name}"
        await _ctx_warning(f"Cleaning untagged images from {scope} — this cannot be undone.")
        return await asyncio.to_thread(
            lambda: json.dumps(_get_manager().cleanup(name=name, all=all))
        )

    @mcp.tool(tags={"destructive", "images"})
    @audit_logged
    async def images_prune(dry_run: bool = True) -> str:
        """[gated by GCO_ENABLE_DESTRUCTIVE_OPERATIONS] destructive.

        `gco images prune` — remove untagged images older than 30 days.
        Cannot be undone when ``dry_run=False``; the matching image manifests
        are permanently deleted.

        Args:
            dry_run: When True (default), report what would be deleted without
                deleting anything.
        """
        if not dry_run:
            await _ctx_warning(
                "Pruning untagged images older than 30 days — this cannot be undone."
            )
        return await asyncio.to_thread(lambda: json.dumps(_get_manager().prune(dry_run=dry_run)))
