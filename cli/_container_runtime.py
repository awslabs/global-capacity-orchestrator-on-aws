"""
Container runtime detection (Docker, Finch, Podman) — shared helper.

Originally part of cli/stacks.py for CDK asset bundling; extracted so
the new cli/images.py ImageManager can reuse the cached detection
without duplicating the probe logic.

CDK requires a container runtime to build Lambda function assets, and
the image registry uses the same runtime for ``docker build`` /
``docker push`` calls. This module checks for available runtimes in
order of preference and verifies they are actually running (not just
installed).

Priority order: docker > finch > podman.

If the ``CDK_DOCKER`` environment variable is set, that value is
returned without checking if the runtime is available.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess

# <pyflowchart-code-diagram> BEGIN - auto-inserted, do not edit
# Flowchart(s) generated from this file:
#   * ``detect_container_runtime`` -> ``diagrams/code_diagrams/cli/_container_runtime.detect_container_runtime.html``
#     (PNG: ``diagrams/code_diagrams/cli/_container_runtime.detect_container_runtime.png``)
# Regenerate with ``python diagrams/code_diagrams/generate.py``.
# <pyflowchart-code-diagram> END


logger = logging.getLogger(__name__)

# Cached result for container runtime detection (None = not yet checked)
_container_runtime_cache: str | None = None
_container_runtime_checked: bool = False


def detect_container_runtime() -> str | None:
    """
    Detect available container runtime (cached).

    Returns:
        Runtime name (``"docker"``, ``"finch"``, or ``"podman"``) if a
        runtime is found and running, ``None`` if nothing is available.

    Note:
        If the ``CDK_DOCKER`` environment variable is set, that value
        is returned without checking if the runtime is available.
    """
    global _container_runtime_cache, _container_runtime_checked
    if _container_runtime_checked:
        return _container_runtime_cache

    _container_runtime_cache = _detect_container_runtime_uncached()
    _container_runtime_checked = True
    return _container_runtime_cache


def _detect_container_runtime_uncached() -> str | None:
    """Uncached implementation of container runtime detection."""
    # Check if CDK_DOCKER is already set
    if os.environ.get("CDK_DOCKER"):
        return os.environ["CDK_DOCKER"]

    # Try docker first
    if shutil.which("docker"):
        # Verify docker is actually running
        try:
            result = subprocess.run(
                ["docker", "info"],
                capture_output=True,
                timeout=5,
            )
            if result.returncode == 0:
                return "docker"
        except Exception as e:
            logger.debug("docker info check failed: %s", e)

    # Try finch as fallback
    if shutil.which("finch"):
        try:
            result = subprocess.run(
                ["finch", "info"],
                capture_output=True,
                timeout=5,
            )
            if result.returncode == 0:
                return "finch"
        except Exception as e:
            logger.debug("finch info check failed: %s", e)

    # Try podman as last resort
    if shutil.which("podman"):
        try:
            result = subprocess.run(
                ["podman", "info"],
                capture_output=True,
                timeout=5,
            )
            if result.returncode == 0:
                return "podman"
        except Exception as e:
            logger.debug("podman info check failed: %s", e)

    return None
