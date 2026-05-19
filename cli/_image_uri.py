"""
ECR image URI helpers — pure functions, no AWS calls.

Lives in its own small module so both ``cli.images`` (which builds and
manages images) and ``cli.inference`` (which has to rewrite URIs to
target the local region of each deployed endpoint) can depend on it
without forming an import cycle.

Static-analysis tools (CodeQL, pyright) flag deferred-import cycles
even when both imports happen inside method bodies, because the
resulting module-level dependency graph still has a cycle. Splitting
the helper out keeps the dependency graph a DAG: ``cli.images`` and
``cli.inference`` both depend on ``cli._image_uri``, and neither
depends on the other.
"""

from __future__ import annotations

import re

# ECR registry host shape:
#   <account-id>.dkr.ecr.<region>.amazonaws.com
_ECR_HOST_RE = re.compile(r"^(?P<account>\d+)\.dkr\.ecr\.(?P<region>[a-z0-9-]+)\.amazonaws\.com$")


def rewrite_image_uri_for_region(uri: str, region: str) -> str:
    """Rewrite an ECR image URI to target a specific region's replica.

    Pure helper — no AWS calls. Detects ECR URIs by matching the
    ``<account>.dkr.ecr.<region>.amazonaws.com`` host shape and swaps
    the region segment when it matches. Non-ECR refs (Docker Hub,
    GHCR, etc.) are returned unchanged.

    Args:
        uri: The image URI (with optional ``host/path:tag`` shape).
        region: Target AWS region for the rewrite.

    Returns:
        The rewritten URI when the input is an ECR URI; otherwise the
        original input.
    """
    if "://" in uri:
        # Not a bare image ref (looks like a URL with a scheme).
        return uri
    parts = uri.split("/", 1)
    host = parts[0]
    match = _ECR_HOST_RE.match(host)
    if match is None:
        return uri
    new_host = f"{match.group('account')}.dkr.ecr.{region}.amazonaws.com"
    if len(parts) > 1:
        return f"{new_host}/{parts[1]}"
    return new_host
