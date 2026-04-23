"""
Tests for cli/kubectl_helpers.update_kubeconfig.

Drives the thin wrapper around `aws eks update-kubeconfig` against a
patched subprocess.run: success with correct argv shape, non-zero
return codes surfaced as RuntimeError, CalledProcessError handling,
and the friendly "AWS CLI not found" message when the binary is missing.
"""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from cli.kubectl_helpers import update_kubeconfig


class TestUpdateKubeconfig:
    """Tests for update_kubeconfig helper."""

    def test_update_kubeconfig_success(self):
        """Test successful kubeconfig update."""
        with patch("cli.kubectl_helpers.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            update_kubeconfig("my-cluster", "us-east-1")

            mock_run.assert_called_once()
            cmd = mock_run.call_args[0][0]
            assert "update-kubeconfig" in cmd
            assert "--name" in cmd
            assert "my-cluster" in cmd
            assert "--region" in cmd
            assert "us-east-1" in cmd

    def test_update_kubeconfig_failure(self):
        """Test kubeconfig update failure raises RuntimeError."""
        with patch("cli.kubectl_helpers.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="Cluster not found")
            with pytest.raises(RuntimeError, match="Failed to update kubeconfig"):
                update_kubeconfig("bad-cluster", "us-east-1")

    def test_update_kubeconfig_called_process_error(self):
        """Test kubeconfig update handles CalledProcessError."""
        with patch("cli.kubectl_helpers.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(
                1, "aws", stderr="Cluster not found"
            )
            with pytest.raises(RuntimeError, match="Failed to update kubeconfig"):
                update_kubeconfig("bad-cluster", "us-east-1")

    def test_update_kubeconfig_aws_cli_not_found(self):
        """Test kubeconfig update when AWS CLI is not installed."""
        with patch("cli.kubectl_helpers.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError()
            with pytest.raises(RuntimeError, match="AWS CLI not found"):
                update_kubeconfig("my-cluster", "us-east-1")
