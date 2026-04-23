"""
Additional CLI coverage tests aimed at edge cases and error paths.

Complements the other test_cli_* files by exercising less common
branches: pod-logs success/empty/error paths with container
selection, error handling in command handlers, and misc formatter
interactions. Mocks get_job_manager and get_output_formatter from
cli.commands.jobs_cmd rather than the underlying classes so the
Click wiring is also exercised.
"""

from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner


@pytest.fixture
def runner():
    """Create a CLI runner."""
    return CliRunner()


# =============================================================================
# Pod Logs Command Tests
# =============================================================================


class TestPodLogsCommand:
    """Tests for pod-logs command."""

    def test_pod_logs_success(self, runner):
        """Test pod-logs command success."""
        mock_job_manager = MagicMock()
        mock_job_manager.get_pod_logs.return_value = {
            "logs": "Line 1\nLine 2\nLine 3",
        }

        with (
            patch("cli.commands.jobs_cmd.get_output_formatter") as mock_formatter,
            patch("cli.commands.jobs_cmd.get_job_manager", return_value=mock_job_manager),
        ):
            mock_formatter.return_value = MagicMock()

            from cli.main import jobs

            result = runner.invoke(
                jobs,
                ["pod-logs", "my-job", "my-job-abc123", "-r", "us-east-1"],
                catch_exceptions=False,
            )
            assert result.exit_code == 0
            assert "Line 1" in result.output

    def test_pod_logs_no_logs(self, runner):
        """Test pod-logs command with no logs."""
        mock_job_manager = MagicMock()
        mock_job_manager.get_pod_logs.return_value = {"logs": ""}

        with (
            patch("cli.commands.jobs_cmd.get_output_formatter") as mock_formatter,
            patch("cli.commands.jobs_cmd.get_job_manager", return_value=mock_job_manager),
        ):
            mock_fmt = MagicMock()
            mock_formatter.return_value = mock_fmt

            from cli.main import jobs

            result = runner.invoke(
                jobs,
                ["pod-logs", "my-job", "my-job-abc123", "-r", "us-east-1"],
                catch_exceptions=False,
            )
            assert result.exit_code == 0
            mock_fmt.print_info.assert_called_with("No logs available")

    def test_pod_logs_error(self, runner):
        """Test pod-logs command with error."""
        mock_job_manager = MagicMock()
        mock_job_manager.get_pod_logs.side_effect = Exception("Connection failed")

        with (
            patch("cli.commands.jobs_cmd.get_output_formatter") as mock_formatter,
            patch("cli.commands.jobs_cmd.get_job_manager", return_value=mock_job_manager),
        ):
            mock_fmt = MagicMock()
            mock_formatter.return_value = mock_fmt

            from cli.main import jobs

            result = runner.invoke(
                jobs,
                ["pod-logs", "my-job", "my-job-abc123", "-r", "us-east-1"],
            )
            assert result.exit_code == 1
            mock_fmt.print_error.assert_called()

    def test_pod_logs_with_container(self, runner):
        """Test pod-logs command with container option."""
        mock_job_manager = MagicMock()
        mock_job_manager.get_pod_logs.return_value = {"logs": "Container logs"}

        with (
            patch("cli.commands.jobs_cmd.get_output_formatter") as mock_formatter,
            patch("cli.commands.jobs_cmd.get_job_manager", return_value=mock_job_manager),
        ):
            mock_formatter.return_value = MagicMock()

            from cli.main import jobs

            result = runner.invoke(
                jobs,
                ["pod-logs", "my-job", "my-job-abc123", "-r", "us-east-1", "-c", "sidecar"],
                catch_exceptions=False,
            )
            assert result.exit_code == 0
            mock_job_manager.get_pod_logs.assert_called_once_with(
                job_name="my-job",
                pod_name="my-job-abc123",
                namespace="gco-jobs",
                region="us-east-1",
                tail_lines=100,
                container="sidecar",
            )


# =============================================================================
# Destroy All Stacks Command Tests
# =============================================================================


class TestDestroyAllStacksCommand:
    """Tests for destroy-all stacks command."""

    def test_destroy_all_stacks_success(self, runner):
        """Test destroy-all command success."""
        mock_manager = MagicMock()
        mock_manager.list_stacks.return_value = ["gco-global", "gco-us-east-1"]
        mock_manager.destroy_orchestrated.return_value = (
            True,
            ["gco-global", "gco-us-east-1"],
            [],
        )

        with (
            patch("cli.commands.stacks_cmd.get_output_formatter") as mock_formatter,
            patch("cli.stacks.get_stack_manager", return_value=mock_manager),
            patch(
                "cli.stacks.get_stack_destroy_order",
                return_value=["gco-us-east-1", "gco-global"],
            ),
        ):
            mock_fmt = MagicMock()
            mock_formatter.return_value = mock_fmt

            from cli.main import stacks

            result = runner.invoke(
                stacks,
                ["destroy-all", "-y"],
                catch_exceptions=False,
            )
            assert result.exit_code == 0
            mock_fmt.print_success.assert_called()

    def test_destroy_all_stacks_parallel(self, runner):
        """Test destroy-all command with parallel option."""
        mock_manager = MagicMock()
        mock_manager.list_stacks.return_value = ["gco-global", "gco-us-east-1"]
        mock_manager.destroy_orchestrated.return_value = (
            True,
            ["gco-global", "gco-us-east-1"],
            [],
        )

        with (
            patch("cli.commands.stacks_cmd.get_output_formatter") as mock_formatter,
            patch("cli.stacks.get_stack_manager", return_value=mock_manager),
            patch(
                "cli.stacks.get_stack_destroy_order",
                return_value=["gco-us-east-1", "gco-global"],
            ),
        ):
            mock_fmt = MagicMock()
            mock_formatter.return_value = mock_fmt

            from cli.main import stacks

            result = runner.invoke(
                stacks,
                ["destroy-all", "-y", "--parallel", "--max-workers", "4"],
                catch_exceptions=False,
            )
            assert result.exit_code == 0
            mock_manager.destroy_orchestrated.assert_called_once()
            call_kwargs = mock_manager.destroy_orchestrated.call_args.kwargs
            assert call_kwargs["parallel"] is True
            assert call_kwargs["max_workers"] == 4

    def test_destroy_all_stacks_with_failures(self, runner):
        """Test destroy-all command retries and eventually fails."""
        mock_manager = MagicMock()
        mock_manager.list_stacks.return_value = ["gco-global", "gco-us-east-1"]
        # All 3 attempts fail
        mock_manager.destroy_orchestrated.return_value = (
            False,
            ["gco-us-east-1"],
            ["gco-global"],
        )

        with (
            patch("cli.commands.stacks_cmd.get_output_formatter") as mock_formatter,
            patch("cli.stacks.get_stack_manager", return_value=mock_manager),
            patch(
                "cli.stacks.get_stack_destroy_order",
                return_value=["gco-us-east-1", "gco-global"],
            ),
            patch("time.sleep"),  # Skip the 30s waits
        ):
            mock_fmt = MagicMock()
            mock_formatter.return_value = mock_fmt

            from cli.main import stacks

            result = runner.invoke(
                stacks,
                ["destroy-all", "-y"],
            )
            assert result.exit_code == 1
            # Should have been called 3 times (initial + 2 retries)
            assert mock_manager.destroy_orchestrated.call_count == 3
            mock_fmt.print_error.assert_called()

    def test_destroy_all_stacks_retry_succeeds(self, runner):
        """Test destroy-all retries and succeeds on second attempt."""
        mock_manager = MagicMock()
        mock_manager.list_stacks.return_value = ["gco-global", "gco-us-east-1"]
        # First attempt fails, second succeeds
        mock_manager.destroy_orchestrated.side_effect = [
            (False, ["gco-us-east-1"], ["gco-global"]),
            (True, ["gco-global", "gco-us-east-1"], []),
        ]

        with (
            patch("cli.commands.stacks_cmd.get_output_formatter") as mock_formatter,
            patch("cli.stacks.get_stack_manager", return_value=mock_manager),
            patch(
                "cli.stacks.get_stack_destroy_order",
                return_value=["gco-us-east-1", "gco-global"],
            ),
            patch("time.sleep"),  # Skip the 30s waits
        ):
            mock_fmt = MagicMock()
            mock_formatter.return_value = mock_fmt

            from cli.main import stacks

            result = runner.invoke(
                stacks,
                ["destroy-all", "-y"],
                catch_exceptions=False,
            )
            assert result.exit_code == 0
            assert mock_manager.destroy_orchestrated.call_count == 2
            mock_fmt.print_success.assert_called()


# =============================================================================
# FSx Enable Command Tests
# =============================================================================


class TestFsxEnableCommand:
    """Tests for FSx enable command."""

    def test_fsx_enable_success(self, runner):
        """Test FSx enable command success."""
        with (
            patch("cli.commands.stacks_cmd.get_output_formatter") as mock_formatter,
            patch("cli.stacks.update_fsx_config") as mock_update,
        ):
            mock_fmt = MagicMock()
            mock_formatter.return_value = mock_fmt

            from cli.main import stacks

            result = runner.invoke(
                stacks,
                ["fsx", "enable", "-y", "--storage-capacity", "1200"],
                catch_exceptions=False,
            )
            assert result.exit_code == 0
            mock_update.assert_called_once()
            mock_fmt.print_success.assert_called()

    def test_fsx_enable_invalid_capacity(self, runner):
        """Test FSx enable command with invalid capacity."""
        with patch("cli.commands.stacks_cmd.get_output_formatter") as mock_formatter:
            mock_fmt = MagicMock()
            mock_formatter.return_value = mock_fmt

            from cli.main import stacks

            result = runner.invoke(
                stacks,
                ["fsx", "enable", "-y", "--storage-capacity", "500"],
            )
            assert result.exit_code == 1
            mock_fmt.print_error.assert_called_with("Storage capacity must be at least 1200 GiB")

    def test_fsx_enable_with_import_path(self, runner):
        """Test FSx enable command with import path."""
        with (
            patch("cli.commands.stacks_cmd.get_output_formatter") as mock_formatter,
            patch("cli.stacks.update_fsx_config") as mock_update,
        ):
            mock_fmt = MagicMock()
            mock_formatter.return_value = mock_fmt

            from cli.main import stacks

            result = runner.invoke(
                stacks,
                [
                    "fsx",
                    "enable",
                    "-y",
                    "--storage-capacity",
                    "2400",
                    "--import-path",
                    "s3://my-bucket/data",
                ],
                catch_exceptions=False,
            )
            assert result.exit_code == 0
            call_args = mock_update.call_args[0][0]
            assert call_args["import_path"] == "s3://my-bucket/data"
            assert call_args["auto_import_policy"] == "NEW_CHANGED_DELETED"

    def test_fsx_enable_for_region(self, runner):
        """Test FSx enable command for specific region."""
        with (
            patch("cli.commands.stacks_cmd.get_output_formatter") as mock_formatter,
            patch("cli.stacks.update_fsx_config") as mock_update,
        ):
            mock_fmt = MagicMock()
            mock_formatter.return_value = mock_fmt

            from cli.main import stacks

            result = runner.invoke(
                stacks,
                ["fsx", "enable", "-y", "-r", "us-west-2", "--storage-capacity", "1200"],
                catch_exceptions=False,
            )
            assert result.exit_code == 0
            mock_update.assert_called_once()
            call_args = mock_update.call_args
            assert call_args[0][1] == "us-west-2"


# =============================================================================
# Jobs Commands Edge Cases
# =============================================================================


class TestJobsCommandsEdgeCases:
    """Tests for jobs command edge cases."""

    def test_jobs_metrics_success(self, runner):
        """Test jobs metrics command success."""
        mock_job_manager = MagicMock()
        mock_job_manager.get_job_metrics.return_value = {
            "job_name": "my-job",
            "summary": {"total_cpu_millicores": 500, "total_memory_mib": 256},
            "pods": [],
        }

        with (
            patch("cli.commands.jobs_cmd.get_output_formatter") as mock_formatter,
            patch("cli.commands.jobs_cmd.get_job_manager", return_value=mock_job_manager),
        ):
            mock_fmt = MagicMock()
            mock_formatter.return_value = mock_fmt

            from cli.main import jobs

            result = runner.invoke(jobs, ["metrics", "my-job", "-r", "us-east-1"])
            assert result.exit_code == 0

    def test_jobs_metrics_error(self, runner):
        """Test jobs metrics command with error."""
        mock_job_manager = MagicMock()
        mock_job_manager.get_job_metrics.side_effect = Exception("API error")

        with (
            patch("cli.commands.jobs_cmd.get_output_formatter") as mock_formatter,
            patch("cli.commands.jobs_cmd.get_job_manager", return_value=mock_job_manager),
        ):
            mock_fmt = MagicMock()
            mock_formatter.return_value = mock_fmt

            from cli.main import jobs

            result = runner.invoke(jobs, ["metrics", "my-job", "-r", "us-east-1"])
            assert result.exit_code == 1
            mock_fmt.print_error.assert_called()

    def test_jobs_bulk_delete_success(self, runner):
        """Test jobs bulk-delete command success."""
        mock_job_manager = MagicMock()
        mock_job_manager.bulk_delete_jobs.return_value = {
            "deleted": 5,
            "failed": 0,
            "deleted_jobs": [],
        }

        with (
            patch("cli.commands.jobs_cmd.get_output_formatter") as mock_formatter,
            patch("cli.commands.jobs_cmd.get_job_manager", return_value=mock_job_manager),
        ):
            mock_fmt = MagicMock()
            mock_formatter.return_value = mock_fmt

            from cli.main import jobs

            result = runner.invoke(jobs, ["bulk-delete", "-r", "us-east-1", "-y"])
            assert result.exit_code == 0

    def test_jobs_bulk_delete_error(self, runner):
        """Test jobs bulk-delete command with error."""
        mock_job_manager = MagicMock()
        mock_job_manager.bulk_delete_jobs.side_effect = Exception("API error")

        with (
            patch("cli.commands.jobs_cmd.get_output_formatter") as mock_formatter,
            patch("cli.commands.jobs_cmd.get_job_manager", return_value=mock_job_manager),
        ):
            mock_fmt = MagicMock()
            mock_formatter.return_value = mock_fmt

            from cli.main import jobs

            result = runner.invoke(jobs, ["bulk-delete", "-r", "us-east-1", "-y"])
            assert result.exit_code == 1
            mock_fmt.print_error.assert_called()
