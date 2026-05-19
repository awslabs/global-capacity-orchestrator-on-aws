"""File storage MCP tools."""

import asyncio

import cli_runner
from audit import audit_logged
from server import mcp


@mcp.tool(tags={"safe", "storage"})
@audit_logged
def list_storage_contents(region: str, path: str = "/") -> str:
    """List contents of shared EFS storage.

    Args:
        region: AWS region.
        path: Directory path to list (default: root).
    """
    args = ["files", "ls", "-r", region]
    if path != "/":
        args.append(path)
    return cli_runner._run_cli(*args)


@mcp.tool(tags={"safe", "storage"})
@audit_logged
def list_file_systems(region: str | None = None) -> str:
    """List EFS and FSx file systems.

    Args:
        region: Specific region, or omit for all.
    """
    args = ["files", "list"]
    if region:
        args += ["-r", region]
    return cli_runner._run_cli(*args)


# =============================================================================
# Read-only inspection tools (async)
# =============================================================================


@mcp.tool(tags={"safe", "files"})
@audit_logged
async def files_get(path: str, region: str) -> str:
    """`gco files get` — fetch a single file from EFS.

    Args:
        path: File path relative to the storage root.
        region: AWS region.
    """
    return await asyncio.to_thread(cli_runner._run_cli, "files", "get", path, "-r", region)


@mcp.tool(tags={"safe", "files"})
@audit_logged
async def files_access_points(region: str | None = None) -> str:
    """`gco files access-points` — list EFS access points.

    Args:
        region: AWS region.
    """
    args = ["files", "access-points"]
    if region:
        args += ["-r", region]
    return await asyncio.to_thread(cli_runner._run_cli, *args)
