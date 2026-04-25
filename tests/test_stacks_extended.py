"""
Extended coverage for cli/stacks.StackManager — CloudFormation output
discovery, bootstrap gating, and the deploy/destroy CLI wrapper.

Exercises get_outputs and get_stack_status against mocked boto3
CloudFormation clients (success, missing outputs, stack-not-found,
ClientError), deploy/destroy argv shape with --all/--outputs-file/
--parameters/--tags/CDK_DOCKER env handling, _get_deploy_region
mapping for gco-global/gco-api-gateway/gco-monitoring/regional
stacks (with cdk.json override support), and the
is_bootstrapped + ensure_bootstrapped pair that gates cdk deploy on
a live CDKToolkit stack. Also covers update_fsx_config tmp_path
round-trips and the deploy() integration with ensure_bootstrapped.
"""

import json
import os
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch


class TestStackManagerGetOutputs:
    """Tests for StackManager.get_outputs method."""

    def test_get_outputs_success(self):
        """Test getting stack outputs successfully."""
        from cli.stacks import StackManager

        config = MagicMock()

        with patch("boto3.client") as mock_boto:
            mock_cf = MagicMock()
            mock_cf.describe_stacks.return_value = {
                "Stacks": [
                    {
                        "StackName": "test-stack",
                        "Outputs": [
                            {"OutputKey": "VpcId", "OutputValue": "vpc-12345"},
                            {
                                "OutputKey": "ClusterArn",
                                "OutputValue": "arn:aws:eks:us-east-1:123:cluster/test",
                            },
                        ],
                    }
                ]
            }
            mock_boto.return_value = mock_cf

            manager = StackManager(config)
            outputs = manager.get_outputs("test-stack", "us-east-1")

            assert outputs["VpcId"] == "vpc-12345"
            assert outputs["ClusterArn"] == "arn:aws:eks:us-east-1:123:cluster/test"

    def test_get_outputs_no_outputs(self):
        """Test getting stack outputs when stack has no outputs."""
        from cli.stacks import StackManager

        config = MagicMock()

        with patch("boto3.client") as mock_boto:
            mock_cf = MagicMock()
            mock_cf.describe_stacks.return_value = {"Stacks": [{"StackName": "test-stack"}]}
            mock_boto.return_value = mock_cf

            manager = StackManager(config)
            outputs = manager.get_outputs("test-stack", "us-east-1")

            assert outputs == {}

    def test_get_outputs_stack_not_found(self):
        """Test getting outputs when stack doesn't exist."""
        from cli.stacks import StackManager

        config = MagicMock()

        with patch("boto3.client") as mock_boto:
            mock_cf = MagicMock()
            mock_cf.describe_stacks.return_value = {"Stacks": []}
            mock_boto.return_value = mock_cf

            manager = StackManager(config)
            outputs = manager.get_outputs("nonexistent-stack", "us-east-1")

            assert outputs == {}

    def test_get_outputs_exception(self):
        """Test getting outputs when exception occurs."""
        from cli.stacks import StackManager

        config = MagicMock()

        with patch("boto3.client") as mock_boto:
            mock_cf = MagicMock()
            mock_cf.describe_stacks.side_effect = Exception("Stack not found")
            mock_boto.return_value = mock_cf

            manager = StackManager(config)
            outputs = manager.get_outputs("test-stack", "us-east-1")

            assert outputs == {}


class TestStackManagerGetStackStatus:
    """Tests for StackManager.get_stack_status method."""

    def test_get_stack_status_success(self):
        """Test getting stack status successfully."""
        from cli.stacks import StackManager

        config = MagicMock()
        created_time = datetime(2024, 1, 1, 10, 0, 0)
        updated_time = datetime(2024, 1, 15, 14, 30, 0)

        with patch("boto3.client") as mock_boto:
            mock_cf = MagicMock()
            mock_cf.describe_stacks.return_value = {
                "Stacks": [
                    {
                        "StackName": "test-stack",
                        "StackStatus": "CREATE_COMPLETE",
                        "CreationTime": created_time,
                        "LastUpdatedTime": updated_time,
                        "Outputs": [
                            {"OutputKey": "VpcId", "OutputValue": "vpc-12345"},
                        ],
                        "Tags": [
                            {"Key": "Environment", "Value": "production"},
                        ],
                    }
                ]
            }
            mock_boto.return_value = mock_cf

            manager = StackManager(config)
            status = manager.get_stack_status("test-stack", "us-east-1")

            assert status is not None
            assert status.name == "test-stack"
            assert status.status == "CREATE_COMPLETE"
            assert status.region == "us-east-1"
            assert status.created_time == created_time
            assert status.updated_time == updated_time
            assert status.outputs["VpcId"] == "vpc-12345"
            assert status.tags["Environment"] == "production"

    def test_get_stack_status_not_found(self):
        """Test getting status when stack doesn't exist."""
        from cli.stacks import StackManager

        config = MagicMock()

        with patch("boto3.client") as mock_boto:
            mock_cf = MagicMock()
            mock_cf.describe_stacks.return_value = {"Stacks": []}
            mock_boto.return_value = mock_cf

            manager = StackManager(config)
            status = manager.get_stack_status("nonexistent-stack", "us-east-1")

            assert status is None

    def test_get_stack_status_exception(self):
        """Test getting status when exception occurs."""
        from cli.stacks import StackManager

        config = MagicMock()

        with patch("boto3.client") as mock_boto:
            mock_cf = MagicMock()
            mock_cf.describe_stacks.side_effect = Exception("Access denied")
            mock_boto.return_value = mock_cf

            manager = StackManager(config)
            status = manager.get_stack_status("test-stack", "us-east-1")

            assert status is None


class TestStackManagerDeployOptions:
    """Tests for StackManager.deploy with various options."""

    def test_deploy_with_all_stacks(self):
        """Test deployment with --all flag."""
        from cli.stacks import StackManager

        config = MagicMock()

        with (
            patch("cli.stacks._detect_container_runtime", return_value="docker"),
            patch.object(StackManager, "_run_cdk") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)

            manager = StackManager(config)
            result = manager.deploy(all_stacks=True, require_approval=False)

            assert result is True
            # Verify --all was passed
            call_args = mock_run.call_args[0][0]
            assert "--all" in call_args

    def test_deploy_with_outputs_file(self):
        """Test deployment with outputs file."""
        from cli.stacks import StackManager

        config = MagicMock()

        with (
            patch("cli.stacks._detect_container_runtime", return_value="docker"),
            patch.object(StackManager, "_run_cdk") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)

            manager = StackManager(config)
            result = manager.deploy(
                "test-stack",
                outputs_file="/tmp/outputs.json",  # nosec B108 - test fixture using temp directory
                require_approval=False,
            )

            assert result is True
            call_args = mock_run.call_args[0][0]
            assert "--outputs-file" in call_args
            assert (
                "/tmp/outputs.json" in call_args  # nosec B108 - test fixture using temp directory
            )

    def test_deploy_with_parameters(self):
        """Test deployment with parameters."""
        from cli.stacks import StackManager

        config = MagicMock()

        with (
            patch("cli.stacks._detect_container_runtime", return_value="docker"),
            patch.object(StackManager, "_run_cdk") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)

            manager = StackManager(config)
            result = manager.deploy(
                "test-stack",
                parameters={"Param1": "Value1", "Param2": "Value2"},
                require_approval=False,
            )

            assert result is True
            call_args = mock_run.call_args[0][0]
            assert "--parameters" in call_args
            # Check parameters are included
            params_str = " ".join(call_args)
            assert "Param1=Value1" in params_str
            assert "Param2=Value2" in params_str

    def test_deploy_with_tags(self):
        """Test deployment with tags."""
        from cli.stacks import StackManager

        config = MagicMock()

        with (
            patch("cli.stacks._detect_container_runtime", return_value="docker"),
            patch.object(StackManager, "_run_cdk") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)

            manager = StackManager(config)
            result = manager.deploy(
                "test-stack",
                tags={"Environment": "prod", "Team": "platform"},
                require_approval=False,
            )

            assert result is True
            call_args = mock_run.call_args[0][0]
            assert "--tags" in call_args
            tags_str = " ".join(call_args)
            assert "Environment=prod" in tags_str
            assert "Team=platform" in tags_str

    def test_deploy_with_cdk_docker_env_set(self):
        """Test deployment when CDK_DOCKER is already set."""
        from cli.stacks import StackManager

        config = MagicMock()

        with (
            patch.dict(os.environ, {"CDK_DOCKER": "finch"}),
            patch("cli.stacks._detect_container_runtime", return_value="finch"),
            patch.object(StackManager, "_run_cdk") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)

            manager = StackManager(config)
            result = manager.deploy("test-stack", require_approval=False)

            assert result is True
            # env should be None since CDK_DOCKER is already set
            call_kwargs = mock_run.call_args[1]
            assert call_kwargs.get("env") is None


class TestStackManagerDestroyOptions:
    """Tests for StackManager.destroy with various options."""

    def test_destroy_with_all_stacks(self):
        """Test destruction with --all flag."""
        from cli.stacks import StackManager

        config = MagicMock()

        with patch.object(StackManager, "_run_cdk") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            manager = StackManager(config)
            result = manager.destroy(all_stacks=True, force=True)

            assert result is True
            call_args = mock_run.call_args[0][0]
            assert "--all" in call_args
            assert "--force" in call_args

    def test_destroy_failure(self):
        """Test destruction failure."""
        from cli.stacks import StackManager

        config = MagicMock()

        with patch.object(StackManager, "_run_cdk") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)

            manager = StackManager(config)
            result = manager.destroy("test-stack")

            assert result is False


class TestStackManagerBootstrapOptions:
    """Tests for StackManager.bootstrap with various options."""

    def test_bootstrap_with_region_only(self):
        """Test bootstrap with region only."""
        from cli.stacks import StackManager

        config = MagicMock()

        with patch.object(StackManager, "_run_cdk") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            manager = StackManager(config)
            result = manager.bootstrap(region="us-west-2")

            assert result is True
            call_args = mock_run.call_args[0][0]
            assert "aws://unknown-account/us-west-2" in call_args

    def test_bootstrap_failure(self):
        """Test bootstrap failure."""
        from cli.stacks import StackManager

        config = MagicMock()

        with patch.object(StackManager, "_run_cdk") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)

            manager = StackManager(config)
            result = manager.bootstrap(account="123456789012", region="us-east-1")

            assert result is False


class TestRunCdkMethod:
    """Tests for StackManager._run_cdk method."""

    def test_run_cdk_with_env(self):
        """Test running CDK with custom environment."""
        from cli.stacks import StackManager

        config = MagicMock()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="output", stderr="")

            manager = StackManager(config)
            manager._cdk_path = "cdk"
            result = manager._run_cdk(
                ["list"],
                capture_output=True,
                env={"CUSTOM_VAR": "value"},
            )

            assert result.returncode == 0
            # Verify env was passed
            call_kwargs = mock_run.call_args[1]
            assert "CUSTOM_VAR" in call_kwargs["env"]

    def test_run_cdk_without_capture(self):
        """Test running CDK without capturing output."""
        from cli.stacks import StackManager

        config = MagicMock()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            manager = StackManager(config)
            manager._cdk_path = "cdk"
            result = manager._run_cdk(["deploy"], capture_output=False)

            assert result.returncode == 0
            call_kwargs = mock_run.call_args[1]
            assert "capture_output" not in call_kwargs or call_kwargs.get("capture_output") is False


class TestFindCdkExecutable:
    """Tests for finding CDK executable."""

    def test_find_cdk_in_common_location(self):
        """Test finding CDK in common location."""
        from cli.stacks import StackManager

        config = MagicMock()

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, "which")

            with patch("os.path.exists") as mock_exists:
                mock_exists.side_effect = lambda p: p == "/usr/local/bin/cdk"

                manager = StackManager(config)
                assert manager._cdk_path == "/usr/local/bin/cdk"


class TestUpdateFsxConfigEdgeCases:
    """Tests for update_fsx_config edge cases."""

    def test_update_fsx_config_creates_context(self):
        """Test that update creates context section if missing."""
        from cli.stacks import update_fsx_config

        with tempfile.TemporaryDirectory() as tmpdir:
            cdk_path = Path(tmpdir) / "cdk.json"
            cdk_config = {}  # No context section
            cdk_path.write_text(json.dumps(cdk_config))

            with patch("cli.stacks._find_cdk_json", return_value=cdk_path):
                update_fsx_config({"enabled": True})

            with open(cdk_path, encoding="utf-8") as f:
                result = json.load(f)
            assert "context" in result
            assert "fsx_lustre" in result["context"]
            assert result["context"]["fsx_lustre"]["enabled"] is True

    def test_update_fsx_config_preserves_other_settings(self):
        """Test that update preserves other cdk.json settings."""
        from cli.stacks import update_fsx_config

        with tempfile.TemporaryDirectory() as tmpdir:
            cdk_path = Path(tmpdir) / "cdk.json"
            cdk_config = {
                "app": "python app.py",
                "context": {
                    "other_setting": "value",
                    "fsx_lustre": {"enabled": False},
                },
            }
            cdk_path.write_text(json.dumps(cdk_config))

            with patch("cli.stacks._find_cdk_json", return_value=cdk_path):
                update_fsx_config({"enabled": True, "storage_capacity_gib": 2400})

            with open(cdk_path, encoding="utf-8") as f:
                result = json.load(f)
            assert result["app"] == "python app.py"
            assert result["context"]["other_setting"] == "value"
            assert result["context"]["fsx_lustre"]["enabled"] is True
            assert result["context"]["fsx_lustre"]["storage_capacity_gib"] == 2400

    def test_update_fsx_config_ignores_none_values(self):
        """Test that update ignores None values except for enabled."""
        from cli.stacks import update_fsx_config

        with tempfile.TemporaryDirectory() as tmpdir:
            cdk_path = Path(tmpdir) / "cdk.json"
            cdk_config = {
                "context": {
                    "fsx_lustre": {
                        "enabled": True,
                        "storage_capacity_gib": 1200,
                    }
                }
            }
            cdk_path.write_text(json.dumps(cdk_config))

            with patch("cli.stacks._find_cdk_json", return_value=cdk_path):
                update_fsx_config({"storage_capacity_gib": None, "enabled": False})

            with open(cdk_path, encoding="utf-8") as f:
                result = json.load(f)
            # storage_capacity_gib should remain unchanged (None ignored)
            assert result["context"]["fsx_lustre"]["storage_capacity_gib"] == 1200
            # enabled should be updated even though it's falsy
            assert result["context"]["fsx_lustre"]["enabled"] is False


class TestIsBootstrapped:
    """Tests for StackManager.is_bootstrapped()."""

    def _make_manager(self):
        config = MagicMock()
        with (
            patch(
                "cli.stacks.StackManager._find_project_root",
                return_value=Path("/tmp"),  # nosec B108 - test fixture using temp directory
            ),
            patch("cli.stacks.StackManager._find_cdk", return_value="npx cdk"),
        ):
            return __import__("cli.stacks", fromlist=["StackManager"]).StackManager(config)

    @patch("boto3.client")
    def test_bootstrapped_active_stack(self, mock_boto_client):
        """CDKToolkit stack exists with CREATE_COMPLETE → True."""
        cf = MagicMock()
        mock_boto_client.return_value = cf
        cf.describe_stacks.return_value = {"Stacks": [{"StackStatus": "CREATE_COMPLETE"}]}
        mgr = self._make_manager()
        assert mgr.is_bootstrapped("us-east-1") is True
        mock_boto_client.assert_called_with("cloudformation", region_name="us-east-1")

    @patch("boto3.client")
    def test_bootstrapped_update_complete(self, mock_boto_client):
        """CDKToolkit stack with UPDATE_COMPLETE → True."""
        cf = MagicMock()
        mock_boto_client.return_value = cf
        cf.describe_stacks.return_value = {"Stacks": [{"StackStatus": "UPDATE_COMPLETE"}]}
        mgr = self._make_manager()
        assert mgr.is_bootstrapped("eu-west-1") is True

    @patch("boto3.client")
    def test_not_bootstrapped_stack_not_found(self, mock_boto_client):
        """describe_stacks raises exception (stack not found) → False."""
        cf = MagicMock()
        mock_boto_client.return_value = cf
        cf.describe_stacks.side_effect = Exception("Stack not found")
        mgr = self._make_manager()
        assert mgr.is_bootstrapped("ap-southeast-1") is False

    @patch("boto3.client")
    def test_not_bootstrapped_delete_complete(self, mock_boto_client):
        """CDKToolkit stack with DELETE_COMPLETE → False."""
        cf = MagicMock()
        mock_boto_client.return_value = cf
        cf.describe_stacks.return_value = {"Stacks": [{"StackStatus": "DELETE_COMPLETE"}]}
        mgr = self._make_manager()
        assert mgr.is_bootstrapped("us-west-2") is False

    @patch("boto3.client")
    def test_not_bootstrapped_delete_in_progress(self, mock_boto_client):
        """CDKToolkit stack with DELETE_IN_PROGRESS → False."""
        cf = MagicMock()
        mock_boto_client.return_value = cf
        cf.describe_stacks.return_value = {
            "Stacks": [{"StackStatus": "DELETE_IN_PROGRESS"}],
        }
        mgr = self._make_manager()
        assert mgr.is_bootstrapped("us-west-2") is False

    @patch("boto3.client")
    def test_not_bootstrapped_empty_stacks(self, mock_boto_client):
        """describe_stacks returns empty list → False."""
        cf = MagicMock()
        mock_boto_client.return_value = cf
        cf.describe_stacks.return_value = {"Stacks": []}
        mgr = self._make_manager()
        assert mgr.is_bootstrapped("us-east-2") is False


class TestEnsureBootstrapped:
    """Tests for StackManager.ensure_bootstrapped()."""

    def _make_manager(self):
        config = MagicMock()
        with (
            patch(
                "cli.stacks.StackManager._find_project_root",
                return_value=Path("/tmp"),  # nosec B108 - test fixture using temp directory
            ),
            patch("cli.stacks.StackManager._find_cdk", return_value="npx cdk"),
        ):
            return __import__("cli.stacks", fromlist=["StackManager"]).StackManager(config)

    def test_already_bootstrapped_skips(self):
        """If is_bootstrapped returns True, bootstrap is not called."""
        mgr = self._make_manager()
        mgr.is_bootstrapped = MagicMock(return_value=True)
        mgr.bootstrap = MagicMock()

        result = mgr.ensure_bootstrapped("us-east-1")
        assert result is True
        mgr.is_bootstrapped.assert_called_once_with("us-east-1")
        mgr.bootstrap.assert_not_called()

    def test_not_bootstrapped_bootstrap_succeeds(self):
        """If not bootstrapped, calls bootstrap and returns True on success."""
        mgr = self._make_manager()
        mgr.is_bootstrapped = MagicMock(return_value=False)
        mgr.bootstrap = MagicMock(return_value=True)

        result = mgr.ensure_bootstrapped("ap-southeast-1")
        assert result is True
        mgr.bootstrap.assert_called_once_with(region="ap-southeast-1")

    def test_not_bootstrapped_bootstrap_fails(self):
        """If not bootstrapped and bootstrap fails, returns False."""
        mgr = self._make_manager()
        mgr.is_bootstrapped = MagicMock(return_value=False)
        mgr.bootstrap = MagicMock(return_value=False)

        result = mgr.ensure_bootstrapped("ap-southeast-1")
        assert result is False
        mgr.bootstrap.assert_called_once_with(region="ap-southeast-1")


class TestGetDeployRegion:
    """Tests for StackManager._get_deploy_region()."""

    def _make_manager(self):
        config = MagicMock()
        config.global_region = "us-east-2"
        config.api_gateway_region = "us-east-1"
        config.monitoring_region = "us-east-2"
        with (
            patch(
                "cli.stacks.StackManager._find_project_root",
                return_value=Path("/tmp"),  # nosec B108 - test fixture using temp directory
            ),
            patch("cli.stacks.StackManager._find_cdk", return_value="npx cdk"),
        ):
            return __import__("cli.stacks", fromlist=["StackManager"]).StackManager(config)

    @patch("cli.config._load_cdk_json", return_value={})
    def test_global_stack_uses_config(self, _mock_cdk):
        """gco-global → config.global_region when cdk.json has no override."""
        mgr = self._make_manager()
        assert mgr._get_deploy_region("gco-global") == "us-east-2"

    @patch("cli.config._load_cdk_json", return_value={"global": "eu-central-1"})
    def test_global_stack_cdk_json_override(self, _mock_cdk):
        """gco-global → cdk.json global region when set."""
        mgr = self._make_manager()
        assert mgr._get_deploy_region("gco-global") == "eu-central-1"

    @patch("cli.config._load_cdk_json", return_value={})
    def test_api_gateway_stack(self, _mock_cdk):
        """gco-api-gateway → config.api_gateway_region."""
        mgr = self._make_manager()
        assert mgr._get_deploy_region("gco-api-gateway") == "us-east-1"

    @patch("cli.config._load_cdk_json", return_value={})
    def test_monitoring_stack(self, _mock_cdk):
        """gco-monitoring → config.monitoring_region."""
        mgr = self._make_manager()
        assert mgr._get_deploy_region("gco-monitoring") == "us-east-2"

    @patch("cli.config._load_cdk_json", return_value={})
    def test_regional_stack_us_east_1(self, _mock_cdk):
        """gco-us-east-1 → us-east-1."""
        mgr = self._make_manager()
        assert mgr._get_deploy_region("gco-us-east-1") == "us-east-1"

    @patch("cli.config._load_cdk_json", return_value={})
    def test_regional_stack_eu_west_1(self, _mock_cdk):
        """gco-eu-west-1 → eu-west-1."""
        mgr = self._make_manager()
        assert mgr._get_deploy_region("gco-eu-west-1") == "eu-west-1"

    @patch("cli.config._load_cdk_json", return_value={})
    def test_regional_stack_ap_southeast_1(self, _mock_cdk):
        """gco-ap-southeast-1 → ap-southeast-1."""
        mgr = self._make_manager()
        assert mgr._get_deploy_region("gco-ap-southeast-1") == "ap-southeast-1"

    @patch("cli.config._load_cdk_json", return_value={})
    def test_unknown_stack_returns_none(self, _mock_cdk):
        """Unrecognized stack name without gco- prefix → None."""
        mgr = self._make_manager()
        assert mgr._get_deploy_region("some-other-stack") is None


class TestDeployCallsEnsureBootstrapped:
    """Tests that deploy() integrates with ensure_bootstrapped correctly."""

    def _make_manager(self):
        config = MagicMock()
        config.global_region = "us-east-2"
        with (
            patch(
                "cli.stacks.StackManager._find_project_root",
                return_value=Path("/tmp"),  # nosec B108 - test fixture using temp directory
            ),
            patch("cli.stacks.StackManager._find_cdk", return_value="npx cdk"),
        ):
            return __import__("cli.stacks", fromlist=["StackManager"]).StackManager(config)

    @patch("cli.stacks._detect_container_runtime", return_value="docker")
    @patch("cli.config._load_cdk_json", return_value={})
    def test_deploy_calls_ensure_bootstrapped(self, _mock_cdk, _mock_runtime):
        """deploy() calls ensure_bootstrapped with the resolved region."""
        mgr = self._make_manager()
        mgr._sync_lambda_sources = MagicMock()
        mgr.ensure_bootstrapped = MagicMock(return_value=True)
        mgr._run_cdk = MagicMock(return_value=MagicMock(returncode=0))

        mgr.deploy(stack_name="gco-global", require_approval=False)
        mgr.ensure_bootstrapped.assert_called_once_with("us-east-2")

    @patch("cli.stacks._detect_container_runtime", return_value="docker")
    @patch("cli.config._load_cdk_json", return_value={})
    def test_deploy_raises_on_bootstrap_failure(self, _mock_cdk, _mock_runtime):
        """deploy() raises RuntimeError when ensure_bootstrapped returns False."""
        import pytest

        mgr = self._make_manager()
        mgr._sync_lambda_sources = MagicMock()
        mgr.ensure_bootstrapped = MagicMock(return_value=False)

        with pytest.raises(RuntimeError, match="could not be bootstrapped"):
            mgr.deploy(stack_name="gco-global", require_approval=False)

    @patch("cli.stacks._detect_container_runtime", return_value="docker")
    def test_deploy_skips_bootstrap_when_no_stack_name(self, _mock_runtime):
        """deploy() with all_stacks=True skips bootstrap check."""
        mgr = self._make_manager()
        mgr._sync_lambda_sources = MagicMock()
        mgr.ensure_bootstrapped = MagicMock()
        mgr._run_cdk = MagicMock(return_value=MagicMock(returncode=0))

        mgr.deploy(all_stacks=True, require_approval=False)
        mgr.ensure_bootstrapped.assert_not_called()
