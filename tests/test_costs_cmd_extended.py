"""
Extended tests for cli/commands/costs_cmd.py.

Focuses on _get_deployment_regions — the helper that reads the
regional list out of cdk.json and falls back to config.default_region.
Covers cdk.json present but empty, missing regional key, regional as
a string rather than a list, and regional as a list of non-strings
(the type-safety fix that unblocked mypy). Also exercises the
workloads command iterating every discovered region and forecast
command edge cases through CliRunner.
"""

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from cli.commands.costs_cmd import _get_deployment_regions, costs
from cli.config import GCOConfig


class TestGetDeploymentRegions:
    """Tests for _get_deployment_regions helper."""

    def test_returns_regions_from_cdk_json(self):
        """Should return regional list from cdk.json."""
        config = GCOConfig()
        with patch(
            "cli.commands.costs_cmd._load_cdk_json",
            return_value={"regional": ["us-east-1", "us-west-2", "eu-west-1"]},
        ):
            result = _get_deployment_regions(config)
            assert result == ["us-east-1", "us-west-2", "eu-west-1"]

    def test_falls_back_to_default_region(self):
        """Should fall back to config.default_region when cdk.json has no regional key."""
        config = GCOConfig(default_region="ap-southeast-1")
        with patch("cli.commands.costs_cmd._load_cdk_json", return_value={}):
            result = _get_deployment_regions(config)
            assert result == ["ap-southeast-1"]

    def test_falls_back_when_cdk_json_empty(self):
        """Should fall back when _load_cdk_json returns empty dict."""
        config = GCOConfig(default_region="us-west-2")
        with patch("cli.commands.costs_cmd._load_cdk_json", return_value={}):
            result = _get_deployment_regions(config)
            assert result == ["us-west-2"]

    def test_falls_back_when_regional_is_not_list(self):
        """Should fall back when regional is not a list (type safety)."""
        config = GCOConfig(default_region="us-east-1")
        with patch(
            "cli.commands.costs_cmd._load_cdk_json",
            return_value={"regional": "us-east-1"},  # string, not list
        ):
            result = _get_deployment_regions(config)
            assert result == ["us-east-1"]

    def test_falls_back_when_regional_contains_non_strings(self):
        """Should fall back when regional list contains non-string items."""
        config = GCOConfig(default_region="us-east-1")
        with patch(
            "cli.commands.costs_cmd._load_cdk_json",
            return_value={"regional": [1, 2, 3]},
        ):
            result = _get_deployment_regions(config)
            assert result == ["us-east-1"]

    def test_returns_single_region(self):
        """Should work with a single region in the list."""
        config = GCOConfig()
        with patch(
            "cli.commands.costs_cmd._load_cdk_json",
            return_value={"regional": ["eu-central-1"]},
        ):
            result = _get_deployment_regions(config)
            assert result == ["eu-central-1"]

    def test_cdk_json_with_other_keys_but_no_regional(self):
        """Should fall back when cdk.json has other keys but no regional."""
        config = GCOConfig(default_region="us-east-1")
        with patch(
            "cli.commands.costs_cmd._load_cdk_json",
            return_value={"api_gateway": "us-east-2", "global": "us-east-2"},
        ):
            result = _get_deployment_regions(config)
            assert result == ["us-east-1"]


class TestCostsWorkloadsMultiRegion:
    """Tests for costs workloads command with multiple regions."""

    def test_workloads_iterates_all_deployment_regions(self):
        """Workloads command should query all deployment regions."""
        runner = CliRunner()

        mock_tracker = MagicMock()
        mock_tracker.estimate_running_workloads.return_value = []

        with (
            patch("cli.costs.get_cost_tracker", return_value=mock_tracker),
            patch(
                "cli.commands.costs_cmd._get_deployment_regions",
                return_value=["us-east-1", "us-west-2"],
            ),
        ):
            result = runner.invoke(costs, ["workloads"], catch_exceptions=False)

            assert result.exit_code == 0
            assert mock_tracker.estimate_running_workloads.call_count == 2

    def test_workloads_single_region_override(self):
        """Workloads command with -r should only query that region."""
        runner = CliRunner()

        mock_tracker = MagicMock()
        mock_tracker.estimate_running_workloads.return_value = []

        with patch("cli.costs.get_cost_tracker", return_value=mock_tracker):
            result = runner.invoke(costs, ["workloads", "-r", "eu-west-1"], catch_exceptions=False)

            assert result.exit_code == 0
            mock_tracker.estimate_running_workloads.assert_called_once_with("eu-west-1")


class TestCostsForecastEdgeCases:
    """Tests for forecast command edge cases."""

    def test_forecast_with_custom_days(self):
        """Forecast should pass custom days to tracker."""
        runner = CliRunner()

        mock_tracker = MagicMock()
        mock_tracker.get_forecast.return_value = {
            "forecast_total": 150.0,
            "period_start": "2024-01-01",
            "period_end": "2024-03-01",
        }

        with patch("cli.costs.get_cost_tracker", return_value=mock_tracker):
            result = runner.invoke(costs, ["forecast", "--days", "60"], catch_exceptions=False)

            assert result.exit_code == 0
            mock_tracker.get_forecast.assert_called_once_with(days_ahead=60)
            assert "150.00" in result.output

    def test_forecast_daily_average_calculation(self):
        """Forecast should show correct daily average."""
        runner = CliRunner()

        mock_tracker = MagicMock()
        mock_tracker.get_forecast.return_value = {
            "forecast_total": 300.0,
            "period_start": "2024-01-01",
            "period_end": "2024-01-31",
        }

        with patch("cli.costs.get_cost_tracker", return_value=mock_tracker):
            result = runner.invoke(costs, ["forecast", "--days", "30"], catch_exceptions=False)

            assert result.exit_code == 0
            assert "10.00" in result.output  # 300 / 30 = 10
            assert "300.00" in result.output
