"""
Tests for `gco stacks access` — the kubectl bootstrap command in
cli/commands/stacks_cmd.py.

Covers region resolution (defaulting to the first entry of
cdk.json::context.regional and falling back to `-r`), cluster name
defaulting to `gco-{region}`, and the EKS access-entry creation path:
translating `sts:assumed-role` ARNs to IAM role ARNs, invoking
`aws eks update-kubeconfig` and `create-access-entry` via patched
subprocess.run, and swallowing the "already exists" error on rerun
so the command is idempotent.
"""

from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from cli.commands.stacks_cmd import stacks


@pytest.fixture
def runner():
    return CliRunner()


class TestStacksAccessCommand:
    """Tests for gco stacks access."""

    def test_help_shows_usage(self, runner):
        result = runner.invoke(stacks, ["access", "--help"])
        assert result.exit_code == 0
        assert "Configure kubectl access" in result.output

    def test_default_region_from_cdk_json(self, runner):
        """Without -r, should read region from cdk.json."""
        with (
            patch("cli.config._load_cdk_json") as mock_cdk,
            patch("subprocess.run") as mock_run,
            patch("time.sleep"),
        ):
            mock_cdk.return_value = {"regional": ["us-east-1"]}
            mock_run.return_value = MagicMock(
                returncode=0, stdout="NAME STATUS\nnode1 Ready\n", stderr=""
            )

            runner.invoke(stacks, ["access"])
            # Should use us-east-1 from cdk.json
            calls = [str(c) for c in mock_run.call_args_list]
            assert any("us-east-1" in c for c in calls)

    def test_custom_region_override(self, runner):
        """With -r, should use the specified region."""
        with patch("subprocess.run") as mock_run, patch("time.sleep"):
            mock_run.return_value = MagicMock(
                returncode=0, stdout="NAME STATUS\nnode1 Ready\n", stderr=""
            )

            runner.invoke(stacks, ["access", "-r", "eu-west-1"])
            calls = [str(c) for c in mock_run.call_args_list]
            assert any("eu-west-1" in c for c in calls)

    def test_custom_cluster_name(self, runner):
        """With -c, should use the specified cluster name."""
        with patch("subprocess.run") as mock_run, patch("time.sleep"):
            mock_run.return_value = MagicMock(
                returncode=0, stdout="NAME STATUS\nnode1 Ready\n", stderr=""
            )

            runner.invoke(stacks, ["access", "-c", "my-cluster", "-r", "us-east-1"])
            calls = [str(c) for c in mock_run.call_args_list]
            assert any("my-cluster" in c for c in calls)

    def test_default_cluster_name_from_region(self, runner):
        """Without -c, cluster name should be gco-{region}."""
        with patch("subprocess.run") as mock_run, patch("time.sleep"):
            mock_run.return_value = MagicMock(
                returncode=0, stdout="NAME STATUS\nnode1 Ready\n", stderr=""
            )

            runner.invoke(stacks, ["access", "-r", "us-west-2"])
            calls = [str(c) for c in mock_run.call_args_list]
            assert any("gco-us-west-2" in c for c in calls)

    def test_handles_assumed_role(self, runner):
        """Should transform assumed-role ARN to IAM role ARN."""
        with patch("subprocess.run") as mock_run, patch("time.sleep"):
            # First call: update-kubeconfig (success)
            # Second call: get-caller-identity (returns assumed-role ARN)
            # Third call: get-caller-identity for account ID
            # Fourth call: create-access-entry
            # Fifth call: associate-access-policy
            # Sixth call: kubectl get nodes
            call_count = 0

            def side_effect(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                cmd = args[0] if args else kwargs.get("args", [])
                cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)

                mock = MagicMock(returncode=0, stderr="")
                if "get-caller-identity" in cmd_str and "Arn" in cmd_str:
                    mock.stdout = "arn:aws:sts::123456789012:assumed-role/MyRole/session\n"
                elif "get-caller-identity" in cmd_str and "Account" in cmd_str:
                    mock.stdout = "123456789012\n"
                elif "kubectl" in cmd_str:
                    mock.stdout = "NAME STATUS\nnode1 Ready\n"
                else:
                    mock.stdout = ""
                return mock

            mock_run.side_effect = side_effect

            runner.invoke(stacks, ["access", "-r", "us-east-1"])
            # Should have called create-access-entry with the role ARN
            calls = [str(c) for c in mock_run.call_args_list]
            assert any("arn:aws:iam::" in c for c in calls)

    def test_handles_existing_access_entry(self, runner):
        """Should handle 'already exists' gracefully."""
        import subprocess

        with patch("subprocess.run") as mock_run, patch("time.sleep"):

            def side_effect(*args, **kwargs):
                cmd = args[0] if args else kwargs.get("args", [])
                cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)

                if "create-access-entry" in cmd_str:
                    raise subprocess.CalledProcessError(1, cmd, stderr="already exists")

                mock = MagicMock(returncode=0, stderr="")
                if "get-caller-identity" in cmd_str:
                    mock.stdout = "arn:aws:iam::123456789012:user/dev\n"
                elif "kubectl" in cmd_str:
                    mock.stdout = "NAME STATUS\nnode1 Ready\n"
                else:
                    mock.stdout = ""
                return mock

            mock_run.side_effect = side_effect

            result = runner.invoke(stacks, ["access", "-r", "us-east-1"])
            # Should not fail — handles existing entry gracefully
            assert "Access entry may already exist" in result.output
