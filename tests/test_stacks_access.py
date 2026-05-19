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


class TestStacksAccessEndpointWarning:
    """Endpoint-access warning surfaced when the cluster is private-only.

    When the EKS cluster's ``resourcesVpcConfig`` reports
    ``endpointPublicAccess=false``, ``gco stacks access`` still attempts the
    access-entry creation and policy association (those go through the EKS
    control plane via boto3, not through the cluster endpoint), but the
    final ``kubectl get nodes`` call cannot reach the API server from
    outside the VPC. The command surfaces a structured warning that
    points the operator at the ``cdk.json`` knob and the redeploy command.
    """

    def _patch_describe_endpoint(self, *, public: bool, public_cidrs: list[str] | None = None):
        """Build a ``subprocess.run`` side effect that returns the right
        cluster-endpoint payload for the describe-cluster call and the
        usual success exit codes for everything else, with kubectl
        reporting a network timeout to mimic a private-only cluster.
        """
        import json
        import subprocess

        def side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
            mock = MagicMock(returncode=0, stderr="")
            if "describe-cluster" in cmd_str and "resourcesVpcConfig" in cmd_str:
                mock.stdout = json.dumps(
                    {
                        "public": public,
                        "private": True,
                        "publicCidrs": public_cidrs or [],
                    }
                )
            elif "get-caller-identity" in cmd_str and "Arn" in cmd_str:
                mock.stdout = "arn:aws:iam::123456789012:user/dev\n"
            elif "kubectl" in cmd_str and not public:
                # Mimic the laptop-from-outside-the-VPC failure mode.
                mock.returncode = 1
                mock.stderr = "Unable to connect to the server: dial tcp 10.0.0.1:443: i/o timeout"
            elif "kubectl" in cmd_str:
                mock.stdout = "NAME STATUS\nnode1 Ready\n"
            elif "create-access-entry" in cmd_str:
                # Idempotent — pretend it already exists, exercising the
                # CalledProcessError path without affecting anything else.
                raise subprocess.CalledProcessError(1, cmd, stderr="already exists")
            return mock

        return side_effect

    def test_private_only_emits_actionable_warning(self, runner):
        with patch("subprocess.run") as mock_run, patch("time.sleep"):
            mock_run.side_effect = self._patch_describe_endpoint(public=False)
            result = runner.invoke(stacks, ["access", "-r", "us-east-1"])
        assert result.exit_code == 0, result.output
        # The early warning fires once on detection.
        assert "endpointPublicAccess=false" in result.output
        # The remediation hint surfaces at least once with the exact
        # cdk.json key the operator needs to flip.
        assert "PUBLIC_AND_PRIVATE" in result.output
        # And the redeploy command is named explicitly.
        assert "gco stacks deploy gco-us-east-1" in result.output

    def test_public_with_cidr_allowlist_notes_the_allowlist(self, runner):
        with patch("subprocess.run") as mock_run, patch("time.sleep"):
            mock_run.side_effect = self._patch_describe_endpoint(
                public=True, public_cidrs=["203.0.113.0/24"]
            )
            result = runner.invoke(stacks, ["access", "-r", "us-east-1"])
        assert result.exit_code == 0, result.output
        # We should mention the CIDR allowlist explicitly so the
        # operator can verify their egress IP is covered.
        assert "203.0.113.0/24" in result.output
        # And we shouldn't have also fired the private-only warning.
        assert "endpointPublicAccess=false" not in result.output

    def test_public_unrestricted_no_warning(self, runner):
        with patch("subprocess.run") as mock_run, patch("time.sleep"):
            mock_run.side_effect = self._patch_describe_endpoint(public=True)
            result = runner.invoke(stacks, ["access", "-r", "us-east-1"])
        assert result.exit_code == 0, result.output
        # No private-only or CIDR-allowlist warning should appear.
        assert "endpointPublicAccess=false" not in result.output
        assert "verify your egress IP" not in result.output
