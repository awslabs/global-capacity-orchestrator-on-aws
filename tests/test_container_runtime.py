"""
Unit tests for cli/_container_runtime.py — the shared container runtime
detection helper used by both StackManager (CDK asset bundling) and
ImageManager (image build/push).

Covers the priority order docker > finch > podman, the CDK_DOCKER env
var override, and the no-runtime case. Each test reloads the module so
the global cache (``_container_runtime_cache``) starts in its
unchecked sentinel state.
"""

from __future__ import annotations

import importlib
import os
import subprocess
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def fresh_module():
    """Reload cli._container_runtime so each test starts with an empty cache."""
    import cli._container_runtime as cr_module

    importlib.reload(cr_module)
    yield cr_module
    importlib.reload(cr_module)


class TestPriorityOrder:
    """Docker > finch > podman priority when ``<cmd> info`` returns 0."""

    def test_docker_wins_when_all_three_available(self, fresh_module):
        with patch.dict(os.environ, {}, clear=True):

            def which_side(cmd: str) -> str | None:
                return {
                    "docker": "/usr/bin/docker",
                    "finch": "/usr/local/bin/finch",
                    "podman": "/usr/local/bin/podman",
                }.get(cmd)

            with (
                patch("cli._container_runtime.shutil.which", side_effect=which_side),
                patch("cli._container_runtime.subprocess.run") as mock_run,
            ):
                mock_run.return_value = MagicMock(returncode=0)
                assert fresh_module.detect_container_runtime() == "docker"

    def test_finch_wins_when_docker_fails(self, fresh_module):
        with patch.dict(os.environ, {}, clear=True):

            def which_side(cmd: str) -> str | None:
                return {
                    "docker": "/usr/bin/docker",
                    "finch": "/usr/local/bin/finch",
                    "podman": "/usr/local/bin/podman",
                }.get(cmd)

            def run_side(cmd, **kwargs):
                if cmd[0] == "docker":
                    return MagicMock(returncode=1)
                return MagicMock(returncode=0)

            with (
                patch("cli._container_runtime.shutil.which", side_effect=which_side),
                patch("cli._container_runtime.subprocess.run", side_effect=run_side),
            ):
                assert fresh_module.detect_container_runtime() == "finch"

    def test_podman_wins_when_docker_and_finch_fail(self, fresh_module):
        with patch.dict(os.environ, {}, clear=True):

            def which_side(cmd: str) -> str | None:
                return {
                    "docker": "/usr/bin/docker",
                    "finch": "/usr/local/bin/finch",
                    "podman": "/usr/local/bin/podman",
                }.get(cmd)

            def run_side(cmd, **kwargs):
                if cmd[0] in ("docker", "finch"):
                    return MagicMock(returncode=1)
                return MagicMock(returncode=0)

            with (
                patch("cli._container_runtime.shutil.which", side_effect=which_side),
                patch("cli._container_runtime.subprocess.run", side_effect=run_side),
            ):
                assert fresh_module.detect_container_runtime() == "podman"


class TestEnvOverride:
    """``CDK_DOCKER`` env var short-circuits the probe."""

    def test_cdk_docker_returns_value_without_subprocess(self, fresh_module):
        with (
            patch.dict(os.environ, {"CDK_DOCKER": "podman"}, clear=True),
            patch("cli._container_runtime.shutil.which") as mock_which,
            patch("cli._container_runtime.subprocess.run") as mock_run,
        ):
            assert fresh_module.detect_container_runtime() == "podman"
            mock_which.assert_not_called()
            mock_run.assert_not_called()

    def test_cdk_docker_value_is_returned_verbatim(self, fresh_module):
        with patch.dict(os.environ, {"CDK_DOCKER": "/opt/custom/runtime"}, clear=True):
            assert fresh_module.detect_container_runtime() == "/opt/custom/runtime"


class TestNoRuntime:
    """When nothing is on PATH, return ``None``."""

    def test_returns_none_when_nothing_on_path(self, fresh_module):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("cli._container_runtime.shutil.which", return_value=None),
            patch("cli._container_runtime.subprocess.run") as mock_run,
        ):
            assert fresh_module.detect_container_runtime() is None
            mock_run.assert_not_called()

    def test_returns_none_when_all_runtimes_timeout(self, fresh_module):
        def which_side(cmd: str) -> str | None:
            return {
                "docker": "/usr/bin/docker",
                "finch": "/usr/local/bin/finch",
                "podman": "/usr/local/bin/podman",
            }.get(cmd)

        with (
            patch.dict(os.environ, {}, clear=True),
            patch("cli._container_runtime.shutil.which", side_effect=which_side),
            patch(
                "cli._container_runtime.subprocess.run",
                side_effect=subprocess.TimeoutExpired("docker", 5),
            ),
        ):
            assert fresh_module.detect_container_runtime() is None


class TestCaching:
    """The result is cached after the first probe."""

    def test_second_call_does_not_reprobe(self, fresh_module):
        with patch.dict(os.environ, {"CDK_DOCKER": "docker"}, clear=True):
            assert fresh_module.detect_container_runtime() == "docker"
            # Second call returns from cache without env lookup.
            with patch.dict(os.environ, {}, clear=True):
                assert fresh_module.detect_container_runtime() == "docker"


class TestUncachedHelper:
    """The uncached helper is exported and reusable for fresh probes."""

    def test_uncached_helper_runs_full_probe(self, fresh_module):
        with patch.dict(os.environ, {"CDK_DOCKER": "finch"}, clear=True):
            assert fresh_module._detect_container_runtime_uncached() == "finch"
