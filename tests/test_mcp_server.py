"""
Tests for the GCO MCP server (mcp/run_mcp.py).

Drives the thin _run_cli wrapper that subprocesses the `gco` CLI with
--output json, covering success, empty-stdout → {"status":"ok"}, non-zero
exits mapped to {"error":..., "exit_code":...}, the stderr/stdout
fallback, TimeoutExpired surfacing, FileNotFoundError when gco isn't on
PATH, and cwd pinning to PROJECT_ROOT. Also asserts tool registration —
base tool count, gated reserve_capacity registration when
GCO_ENABLE_CAPACITY_PURCHASE=true, and expected tool name set —
without mocking the FastMCP server itself.
"""

import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure mcp/ is importable
sys.path.insert(0, str(Path(__file__).parent.parent / "mcp"))

import run_mcp


class TestRunCli:
    """Tests for the _run_cli helper function."""

    def test_successful_command(self):
        with patch("run_mcp.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout='{"jobs": []}', stderr="")
            result = run_mcp._run_cli("jobs", "list")
            assert result == '{"jobs": []}'
            mock_run.assert_called_once()
            cmd = mock_run.call_args[0][0]
            assert cmd[0] == "gco"
            assert "--output" in cmd
            assert "json" in cmd

    def test_empty_stdout_returns_ok(self):
        with patch("run_mcp.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = run_mcp._run_cli("stacks", "list")
            assert json.loads(result) == {"status": "ok"}

    def test_nonzero_exit_returns_error(self):
        with patch("run_mcp.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Stack not found")
            result = run_mcp._run_cli("stacks", "status", "bad-stack")
            parsed = json.loads(result)
            assert parsed["error"] == "Stack not found"
            assert parsed["exit_code"] == 1

    def test_nonzero_exit_falls_back_to_stdout(self):
        with patch("run_mcp.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="some output", stderr="")
            result = run_mcp._run_cli("jobs", "get", "missing")
            parsed = json.loads(result)
            assert parsed["error"] == "some output"

    def test_timeout_returns_error(self):
        with patch("run_mcp.subprocess.run") as mock_run:
            import subprocess

            mock_run.side_effect = subprocess.TimeoutExpired(
                cmd="gco", timeout=120
            )  # nosemgrep: dangerous-subprocess-use-audit - test fixture: mocking TimeoutExpired with static string, not a real subprocess call
            result = run_mcp._run_cli("stacks", "deploy-all")
            parsed = json.loads(result)
            assert "timed out" in parsed["error"].lower()

    def test_cli_not_found_returns_error(self):
        with patch("run_mcp.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError()
            result = run_mcp._run_cli("jobs", "list")
            parsed = json.loads(result)
            assert "not found" in parsed["error"].lower()

    def test_passes_cwd_as_project_root(self):
        with patch("run_mcp.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            run_mcp._run_cli("stacks", "list")
            assert mock_run.call_args[1]["cwd"] == str(run_mcp.PROJECT_ROOT)


class TestToolRegistration:
    """Verify tools are registered with correct signatures."""

    def test_tool_count(self):
        tools = asyncio.run(run_mcp.mcp.list_tools())
        # 43 base tools; reserve_capacity only registers when
        # GCO_ENABLE_CAPACITY_PURCHASE=true
        base_count = 43
        tool_names = [t.name for t in tools]
        if "reserve_capacity" in tool_names:
            assert len(tools) == base_count + 1
        else:
            assert len(tools) == base_count

    def test_all_tool_names(self):
        tools = asyncio.run(run_mcp.mcp.list_tools())
        names = {t.name for t in tools}
        expected = {
            "list_jobs",
            "submit_job_sqs",
            "submit_job_api",
            "get_job",
            "get_job_logs",
            "get_job_events",
            "delete_job",
            "cluster_health",
            "queue_status",
            "check_capacity",
            "capacity_status",
            "recommend_region",
            "spot_prices",
            "ai_recommend",
            "list_reservations",
            "reservation_check",
            "deploy_inference",
            "list_inference_endpoints",
            "inference_status",
            "scale_inference",
            "update_inference_image",
            "stop_inference",
            "start_inference",
            "delete_inference",
            "canary_deploy",
            "promote_canary",
            "rollback_canary",
            "invoke_inference",
            "chat_inference",
            "inference_health",
            "list_endpoint_models",
            "cost_summary",
            "cost_by_region",
            "cost_trend",
            "cost_forecast",
            "list_stacks",
            "stack_status",
            "setup_cluster_access",
            "fsx_status",
            "list_storage_contents",
            "list_file_systems",
            "list_models",
            "get_model_uri",
        }
        # reserve_capacity is conditionally registered via env var
        # and may also appear if a prior test reloaded the module
        if "reserve_capacity" in names:
            expected.add("reserve_capacity")
        assert names == expected

    def test_each_tool_has_description(self):
        tools = asyncio.run(run_mcp.mcp.list_tools())
        for tool in tools:
            assert tool.description, f"Tool {tool.name} has no description"


class TestJobTools:
    """Tests for job management tools."""

    def test_list_jobs_all_regions(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout='{"jobs":[]}', stderr="")
            run_mcp.list_jobs()
            cmd = mock.call_args[0][0]
            assert "--all-regions" in cmd

    def test_list_jobs_specific_region(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout='{"jobs":[]}', stderr="")
            run_mcp.list_jobs(region="us-east-1")
            cmd = mock.call_args[0][0]
            assert "-r" in cmd
            assert "us-east-1" in cmd
            assert "--all-regions" not in cmd

    def test_list_jobs_with_filters(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            run_mcp.list_jobs(namespace="ml-jobs", status="running")
            cmd = mock.call_args[0][0]
            assert "-n" in cmd
            assert "ml-jobs" in cmd
            assert "-s" in cmd
            assert "running" in cmd

    def test_submit_job_sqs(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout='{"status":"queued"}', stderr="")
            run_mcp.submit_job_sqs("job.yaml", "us-east-1")
            cmd = mock.call_args[0][0]
            assert "submit-sqs" in cmd
            assert "job.yaml" in cmd
            assert "us-east-1" in cmd

    def test_submit_job_sqs_with_options(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            run_mcp.submit_job_sqs("job.yaml", "us-west-2", namespace="ml", priority=50)
            cmd = mock.call_args[0][0]
            assert "-n" in cmd
            assert "ml" in cmd
            assert "--priority" in cmd
            assert "50" in cmd

    def test_submit_job_api(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            run_mcp.submit_job_api("job.yaml", namespace="test")
            cmd = mock.call_args[0][0]
            assert "submit" in cmd
            assert "-n" in cmd
            assert "test" in cmd

    def test_get_job(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            run_mcp.get_job("my-job", "us-east-1")
            cmd = mock.call_args[0][0]
            assert "get" in cmd
            assert "my-job" in cmd

    def test_get_job_logs(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="log output", stderr="")
            run_mcp.get_job_logs("my-job", "us-east-1", tail=500)
            cmd = mock.call_args[0][0]
            assert "logs" in cmd
            assert "500" in cmd

    def test_delete_job(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            run_mcp.delete_job("old-job", "us-east-1")
            cmd = mock.call_args[0][0]
            assert "delete" in cmd
            assert "-y" in cmd

    def test_get_job_events(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            run_mcp.get_job_events("my-job", "us-east-1")
            cmd = mock.call_args[0][0]
            assert "events" in cmd

    def test_cluster_health_all(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            run_mcp.cluster_health()
            cmd = mock.call_args[0][0]
            assert "--all-regions" in cmd

    def test_cluster_health_region(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            run_mcp.cluster_health(region="us-east-1")
            cmd = mock.call_args[0][0]
            assert "-r" in cmd
            assert "--all-regions" not in cmd

    def test_queue_status(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            run_mcp.queue_status()
            cmd = mock.call_args[0][0]
            assert "queue-status" in cmd


class TestCapacityTools:
    """Tests for capacity tools."""

    def test_check_capacity(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            run_mcp.check_capacity("g4dn.xlarge", "us-east-1")
            cmd = mock.call_args[0][0]
            assert "g4dn.xlarge" in cmd
            assert "us-east-1" in cmd

    def test_capacity_status(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            run_mcp.capacity_status()
            cmd = mock.call_args[0][0]
            assert "status" in cmd

    def test_recommend_region_gpu(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            run_mcp.recommend_region(gpu=True)
            cmd = mock.call_args[0][0]
            assert "--gpu" in cmd

    def test_recommend_region_instance(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            run_mcp.recommend_region(instance_type="p4d.24xlarge")
            cmd = mock.call_args[0][0]
            assert "p4d.24xlarge" in cmd

    def test_spot_prices(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            run_mcp.spot_prices("g5.xlarge", "us-west-2")
            cmd = mock.call_args[0][0]
            assert "spot-prices" in cmd

    def test_ai_recommend_basic(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            run_mcp.ai_recommend(workload="Fine-tuning a 20B parameter LLM")
            cmd = mock.call_args[0][0]
            assert "ai-recommend" in cmd
            assert "-w" in cmd
            assert "Fine-tuning a 20B parameter LLM" in cmd

    def test_ai_recommend_with_options(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            run_mcp.ai_recommend(
                workload="Training job",
                instance_type="p4d.24xlarge",
                region="us-east-1",
                gpu=True,
                min_gpus=8,
                min_memory_gb=320,
                fault_tolerance="high",
                max_cost=25.0,
            )
            cmd = mock.call_args[0][0]
            assert "ai-recommend" in cmd
            assert "-i" in cmd
            assert "p4d.24xlarge" in cmd
            assert "-r" in cmd
            assert "--gpu" in cmd
            assert "--min-gpus" in cmd
            assert "--min-memory-gb" in cmd
            assert "--fault-tolerance" in cmd
            assert "high" in cmd
            assert "--max-cost" in cmd

    def test_list_reservations(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            run_mcp.list_reservations(instance_type="p5.48xlarge", region="us-east-1")
            cmd = mock.call_args[0][0]
            assert "reservations" in cmd
            assert "p5.48xlarge" in cmd
            assert "-r" in cmd

    def test_list_reservations_no_filters(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            run_mcp.list_reservations()
            cmd = mock.call_args[0][0]
            assert "reservations" in cmd

    def test_reservation_check(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            run_mcp.reservation_check("p4d.24xlarge", region="us-east-1", block_duration=48)
            cmd = mock.call_args[0][0]
            assert "reservation-check" in cmd
            assert "p4d.24xlarge" in cmd
            assert "-r" in cmd
            assert "--block-duration" in cmd
            assert "48" in cmd

    def test_reservation_check_no_blocks(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            run_mcp.reservation_check("g5.48xlarge", include_blocks=False)
            cmd = mock.call_args[0][0]
            assert "reservation-check" in cmd
            assert "--no-blocks" in cmd

    @patch.dict(os.environ, {"GCO_ENABLE_CAPACITY_PURCHASE": "true"})
    def test_reserve_capacity_dry_run(self):
        # Re-register the tool with env var enabled
        import importlib

        importlib.reload(run_mcp)
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            run_mcp.reserve_capacity("cb-0123456789abcdef0", "us-east-1", dry_run=True)
            cmd = mock.call_args[0][0]
            assert "reserve" in cmd
            assert "cb-0123456789abcdef0" in cmd
            assert "-r" in cmd
            assert "--dry-run" in cmd

    @patch.dict(os.environ, {"GCO_ENABLE_CAPACITY_PURCHASE": "true"})
    def test_reserve_capacity_no_dry_run(self):
        import importlib

        importlib.reload(run_mcp)
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            run_mcp.reserve_capacity("cb-0123456789abcdef0", "us-east-1")
            cmd = mock.call_args[0][0]
            assert "reserve" in cmd
            assert "--dry-run" not in cmd


class TestInferenceTools:
    """Tests for inference endpoint tools."""

    def test_deploy_inference_basic(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            run_mcp.deploy_inference("my-llm", "vllm/vllm-openai:v0.17.0")
            cmd = mock.call_args[0][0]
            assert "deploy" in cmd
            assert "my-llm" in cmd
            assert "vllm/vllm-openai:v0.17.0" in cmd

    def test_deploy_inference_with_options(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            run_mcp.deploy_inference(
                "my-llm",
                "img:v1",
                gpu_count=4,
                replicas=2,
                region="us-east-1",
                env_vars=["MODEL=llama3"],
            )
            cmd = mock.call_args[0][0]
            assert "4" in cmd
            assert "2" in cmd
            assert "-r" in cmd
            assert "-e" in cmd
            assert "MODEL=llama3" in cmd

    def test_list_inference_endpoints(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            run_mcp.list_inference_endpoints(state="running")
            cmd = mock.call_args[0][0]
            assert "--state" in cmd
            assert "running" in cmd

    def test_inference_status(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            run_mcp.inference_status("my-llm")
            cmd = mock.call_args[0][0]
            assert "status" in cmd
            assert "my-llm" in cmd

    def test_scale_inference(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            run_mcp.scale_inference("my-llm", 5)
            cmd = mock.call_args[0][0]
            assert "scale" in cmd
            assert "5" in cmd

    def test_update_inference_image(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            run_mcp.update_inference_image("my-llm", "img:v2")
            cmd = mock.call_args[0][0]
            assert "update-image" in cmd
            assert "img:v2" in cmd

    def test_stop_inference(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            run_mcp.stop_inference("my-llm")
            cmd = mock.call_args[0][0]
            assert "stop" in cmd
            assert "-y" in cmd

    def test_start_inference(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            run_mcp.start_inference("my-llm")
            cmd = mock.call_args[0][0]
            assert "start" in cmd

    def test_delete_inference(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            run_mcp.delete_inference("my-llm")
            cmd = mock.call_args[0][0]
            assert "delete" in cmd
            assert "-y" in cmd

    def test_canary_deploy(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            run_mcp.canary_deploy("my-llm", "img:v2", weight=25)
            cmd = mock.call_args[0][0]
            assert "canary" in cmd
            assert "25" in cmd

    def test_promote_canary(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            run_mcp.promote_canary("my-llm")
            cmd = mock.call_args[0][0]
            assert "promote" in cmd
            assert "-y" in cmd

    def test_rollback_canary(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            run_mcp.rollback_canary("my-llm")
            cmd = mock.call_args[0][0]
            assert "rollback" in cmd
            assert "-y" in cmd

    def test_invoke_inference_basic(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout='{"choices":[]}', stderr="")
            run_mcp.invoke_inference("my-llm", "Hello world")
            cmd = mock.call_args[0][0]
            assert "invoke" in cmd
            assert "my-llm" in cmd
            assert "-p" in cmd
            assert "Hello world" in cmd

    def test_invoke_inference_with_options(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            run_mcp.invoke_inference(
                "my-llm", "test", max_tokens=200, api_path="/v1/completions", region="us-east-1"
            )
            cmd = mock.call_args[0][0]
            assert "200" in cmd
            assert "--path" in cmd
            assert "/v1/completions" in cmd
            assert "-r" in cmd
            assert "us-east-1" in cmd

    def test_invoke_inference_with_stream(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            run_mcp.invoke_inference("my-llm", "test", stream=True)
            cmd = mock.call_args[0][0]
            assert "--stream" in cmd

    def test_chat_inference_basic(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            run_mcp.chat_inference("my-llm", [{"role": "user", "content": "Hi"}])
            cmd = mock.call_args[0][0]
            assert "invoke" in cmd
            assert "my-llm" in cmd
            assert "-d" in cmd
            assert "/v1/chat/completions" in cmd

    def test_chat_inference_with_options(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            run_mcp.chat_inference(
                "my-llm",
                [
                    {"role": "system", "content": "You are helpful"},
                    {"role": "user", "content": "Hi"},
                ],
                max_tokens=512,
                temperature=0.7,
                region="eu-west-1",
            )
            cmd = mock.call_args[0][0]
            assert "-r" in cmd
            assert "eu-west-1" in cmd
            # Verify temperature is in the JSON body
            data_arg_idx = cmd.index("-d") + 1
            import json

            body = json.loads(cmd[data_arg_idx])
            assert body["temperature"] == 0.7
            assert body["max_tokens"] == 512
            assert len(body["messages"]) == 2

    def test_chat_inference_with_stream(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            run_mcp.chat_inference(
                "my-llm",
                [{"role": "user", "content": "Hi"}],
                stream=True,
            )
            cmd = mock.call_args[0][0]
            assert "--stream" in cmd
            data_arg_idx = cmd.index("-d") + 1
            import json

            body = json.loads(cmd[data_arg_idx])
            assert body["stream"] is True

    def test_inference_health(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            run_mcp.inference_health("my-llm")
            cmd = mock.call_args[0][0]
            assert "health" in cmd
            assert "my-llm" in cmd

    def test_inference_health_with_region(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            run_mcp.inference_health("my-llm", region="us-east-1")
            cmd = mock.call_args[0][0]
            assert "-r" in cmd
            assert "us-east-1" in cmd

    def test_list_endpoint_models(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            run_mcp.list_endpoint_models("my-llm")
            cmd = mock.call_args[0][0]
            assert "models" in cmd
            assert "my-llm" in cmd

    def test_list_endpoint_models_with_region(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            run_mcp.list_endpoint_models("my-llm", region="eu-west-1")
            cmd = mock.call_args[0][0]
            assert "-r" in cmd
            assert "eu-west-1" in cmd


class TestCostTools:
    """Tests for cost tracking tools."""

    def test_cost_summary(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            run_mcp.cost_summary(days=7)
            cmd = mock.call_args[0][0]
            assert "summary" in cmd
            assert "7" in cmd

    def test_cost_by_region(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            run_mcp.cost_by_region()
            cmd = mock.call_args[0][0]
            assert "regions" in cmd

    def test_cost_trend(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            run_mcp.cost_trend(days=7)
            cmd = mock.call_args[0][0]
            assert "trend" in cmd
            assert "7" in cmd

    def test_cost_forecast(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            run_mcp.cost_forecast(days_ahead=60)
            cmd = mock.call_args[0][0]
            assert "forecast" in cmd
            assert "60" in cmd


class TestInfraTools:
    """Tests for infrastructure tools."""

    def test_list_stacks(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="stacks", stderr="")
            run_mcp.list_stacks()
            cmd = mock.call_args[0][0]
            assert "stacks" in cmd
            assert "list" in cmd

    def test_stack_status(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            run_mcp.stack_status("gco-us-east-1", "us-east-1")
            cmd = mock.call_args[0][0]
            assert "gco-us-east-1" in cmd
            assert "us-east-1" in cmd

    def test_fsx_status(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            run_mcp.fsx_status()
            cmd = mock.call_args[0][0]
            assert "fsx" in cmd
            assert "status" in cmd


class TestStorageTools:
    """Tests for storage tools."""

    def test_list_storage_contents(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            run_mcp.list_storage_contents("us-east-1")
            cmd = mock.call_args[0][0]
            assert "ls" in cmd
            assert "us-east-1" in cmd

    def test_list_storage_contents_with_path(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            run_mcp.list_storage_contents("us-east-1", path="/outputs")
            cmd = mock.call_args[0][0]
            assert "/outputs" in cmd

    def test_list_file_systems(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            run_mcp.list_file_systems()
            cmd = mock.call_args[0][0]
            assert "list" in cmd


class TestModelTools:
    """Tests for model weight tools."""

    def test_list_models(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
            run_mcp.list_models()
            cmd = mock.call_args[0][0]
            assert "models" in cmd
            assert "list" in cmd

    def test_get_model_uri(self):
        with patch("run_mcp.subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="s3://bucket/model", stderr="")
            result = run_mcp.get_model_uri("llama3-8b")
            cmd = mock.call_args[0][0]
            assert "uri" in cmd
            assert "llama3-8b" in cmd
            assert result == "s3://bucket/model"


class TestResourceRegistration:
    """Verify documentation and source code resources are registered."""

    def test_static_resource_count(self):
        resources = asyncio.run(run_mcp.mcp.list_resources())
        # docs://index, docs://README, docs://QUICKSTART, docs://CONTRIBUTING,
        # docs://examples/README, source://index,
        # k8s://manifests/index, iam://policies/index,
        # infra://index, infra://helm/charts.yaml,
        # demos://index, clients://index, scripts://index
        assert len(resources) == 13

    def test_static_resource_uris(self):
        resources = asyncio.run(run_mcp.mcp.list_resources())
        uris = {str(r.uri) for r in resources}
        assert "docs://gco/index" in uris
        assert "docs://gco/README" in uris
        assert "docs://gco/QUICKSTART" in uris
        assert "docs://gco/CONTRIBUTING" in uris
        assert "docs://gco/examples/README" in uris
        assert "source://gco/index" in uris
        assert "k8s://gco/manifests/index" in uris
        assert "iam://gco/policies/index" in uris
        assert "infra://gco/index" in uris
        assert "demos://gco/index" in uris
        assert "clients://gco/index" in uris
        assert "scripts://gco/index" in uris

    def test_resource_template_count(self):
        templates = asyncio.run(run_mcp.mcp.list_resource_templates())
        # docs/{doc_name}, examples/{example_name}, config/{filename}, file/{filepath},
        # k8s/manifests/{filename}, iam/policies/{filename}, infra/dockerfiles/{filename},
        # demos/{filename}, clients/{filename}, scripts/{filename}
        assert len(templates) == 10

    def test_resource_template_uris(self):
        templates = asyncio.run(run_mcp.mcp.list_resource_templates())
        uris = {t.uri_template for t in templates}
        assert "docs://gco/docs/{doc_name}" in uris
        assert "docs://gco/examples/{example_name}" in uris
        assert "source://gco/config/{filename}" in uris
        assert "source://gco/file/{filepath*}" in uris
        assert "demos://gco/{filename}" in uris
        assert "clients://gco/{filename}" in uris
        assert "scripts://gco/{filename}" in uris


class TestDocResources:
    """Tests for documentation resources."""

    def test_docs_index_contains_sections(self):
        result = asyncio.run(run_mcp.mcp.read_resource("docs://gco/index"))
        content = result.contents[0].content
        assert "Resource Index" in content
        assert "docs://gco/docs/ARCHITECTURE" in content
        assert "docs://gco/examples/" in content

    def test_docs_index_contains_new_resource_groups(self):
        result = asyncio.run(run_mcp.mcp.read_resource("docs://gco/index"))
        content = result.contents[0].content
        assert "demos://gco/index" in content
        assert "clients://gco/index" in content
        assert "scripts://gco/index" in content

    def test_docs_index_categorizes_examples(self):
        result = asyncio.run(run_mcp.mcp.read_resource("docs://gco/index"))
        content = result.contents[0].content
        assert "### Jobs & Training" in content
        assert "### Inference" in content
        assert "### Schedulers" in content

    def test_docs_index_contains_quickstart_and_contributing(self):
        result = asyncio.run(run_mcp.mcp.read_resource("docs://gco/index"))
        content = result.contents[0].content
        assert "docs://gco/QUICKSTART" in content
        assert "docs://gco/CONTRIBUTING" in content
        assert "docs://gco/examples/README" in content

    def test_readme_resource_returns_content(self):
        result = asyncio.run(run_mcp.mcp.read_resource("docs://gco/README"))
        content = result.contents[0].content
        assert "GCO" in content
        assert len(content) > 100

    def test_quickstart_resource_returns_content(self):
        result = asyncio.run(run_mcp.mcp.read_resource("docs://gco/QUICKSTART"))
        content = result.contents[0].content
        assert len(content) > 100

    def test_contributing_resource_returns_content(self):
        result = asyncio.run(run_mcp.mcp.read_resource("docs://gco/CONTRIBUTING"))
        content = result.contents[0].content
        assert len(content) > 100

    def test_examples_readme_returns_content(self):
        result = asyncio.run(run_mcp.mcp.read_resource("docs://gco/examples/README"))
        content = result.contents[0].content
        assert "Example" in content or "example" in content
        assert len(content) > 100

    def test_doc_resource_reads_existing_doc(self):
        result = asyncio.run(run_mcp.mcp.read_resource("docs://gco/docs/CLI"))
        content = result.contents[0].content
        assert len(content) > 100

    def test_doc_resource_missing_returns_available(self):
        result = asyncio.run(run_mcp.mcp.read_resource("docs://gco/docs/NONEXISTENT"))
        content = result.contents[0].content
        assert "not found" in content
        assert "Available" in content

    def test_example_resource_reads_existing(self):
        result = asyncio.run(run_mcp.mcp.read_resource("docs://gco/examples/simple-job"))
        content = result.contents[0].content
        assert len(content) > 10

    def test_example_resource_missing_returns_available(self):
        result = asyncio.run(run_mcp.mcp.read_resource("docs://gco/examples/nonexistent"))
        content = result.contents[0].content
        assert "not found" in content


class TestSourceResources:
    """Tests for source code resources."""

    def test_source_index_contains_packages(self):
        result = asyncio.run(run_mcp.mcp.read_resource("source://gco/index"))
        content = result.contents[0].content
        assert "Source Code Index" in content
        assert "gco/" in content
        assert "cli/" in content

    def test_source_index_contains_new_packages(self):
        result = asyncio.run(run_mcp.mcp.read_resource("source://gco/index"))
        content = result.contents[0].content
        assert "scripts/" in content
        assert "demo/" in content
        assert "dockerfiles/" in content

    def test_source_index_lists_config_files(self):
        result = asyncio.run(run_mcp.mcp.read_resource("source://gco/index"))
        content = result.contents[0].content
        assert "source://gco/config/pyproject.toml" in content
        assert "source://gco/config/.gitlab-ci.yml" in content
        assert "source://gco/config/.pre-commit-config.yaml" in content

    def test_source_index_lists_files(self):
        result = asyncio.run(run_mcp.mcp.read_resource("source://gco/index"))
        content = result.contents[0].content
        assert "source://gco/file/" in content

    def test_config_resource_reads_pyproject(self):
        result = asyncio.run(run_mcp.mcp.read_resource("source://gco/config/pyproject.toml"))
        content = result.contents[0].content
        assert "gco-cli" in content

    def test_config_resource_reads_gitlab_ci(self):
        result = asyncio.run(run_mcp.mcp.read_resource("source://gco/config/.gitlab-ci.yml"))
        content = result.contents[0].content
        assert len(content) > 10

    def test_config_resource_reads_pre_commit(self):
        result = asyncio.run(
            run_mcp.mcp.read_resource("source://gco/config/.pre-commit-config.yaml")
        )
        content = result.contents[0].content
        assert len(content) > 10

    def test_config_resource_reads_checkov(self):
        result = asyncio.run(run_mcp.mcp.read_resource("source://gco/config/.checkov.yaml"))
        content = result.contents[0].content
        assert len(content) > 5

    def test_config_resource_rejects_unknown(self):
        result = asyncio.run(run_mcp.mcp.read_resource("source://gco/config/secret.env"))
        content = result.contents[0].content
        assert "Not available" in content

    def test_file_resource_reads_source(self):
        result = asyncio.run(run_mcp.mcp.read_resource("source://gco/file/cli/main.py"))
        content = result.contents[0].content
        assert "import" in content
        assert len(content) > 50

    def test_file_resource_reads_shell_script(self):
        result = asyncio.run(
            run_mcp.mcp.read_resource("source://gco/file/scripts/setup-cluster-access.sh")
        )
        content = result.contents[0].content
        assert len(content) > 50

    def test_file_resource_reads_markdown(self):
        result = asyncio.run(run_mcp.mcp.read_resource("source://gco/file/demo/README.md"))
        content = result.contents[0].content
        assert len(content) > 50

    def test_file_resource_missing_file(self):
        result = asyncio.run(run_mcp.mcp.read_resource("source://gco/file/cli/nonexistent.py"))
        content = result.contents[0].content
        assert "not found" in content

    def test_file_resource_rejects_bad_extension(self):
        result = asyncio.run(run_mcp.mcp.read_resource("source://gco/file/images/README.png"))
        content = result.contents[0].content
        assert "not served" in content or "not found" in content

    def test_file_resource_blocks_skipped_dirs(self):
        result = asyncio.run(
            run_mcp.mcp.read_resource("source://gco/file/cli/__pycache__/anything")
        )
        content = result.contents[0].content
        assert "denied" in content.lower()

    def test_file_resource_blocks_path_traversal(self):
        """Path traversal is blocked: FastMCP normalises URI segments so ../../
        collapses before reaching the handler, and the handler's resolve() +
        startswith check catches anything that still escapes."""
        import pytest
        from fastmcp.exceptions import NotFoundError

        # FastMCP normalises the URI, collapsing ../.. so the path no longer
        # matches the file/{filepath*} template — raises NotFoundError.
        with pytest.raises(NotFoundError):
            asyncio.run(run_mcp.mcp.read_resource("source://gco/file/../../etc/passwd"))


class TestDemoResources:
    """Tests for demo and walkthrough resources."""

    def test_demos_index_contains_walkthroughs(self):
        result = asyncio.run(run_mcp.mcp.read_resource("demos://gco/index"))
        content = result.contents[0].content
        assert "Walkthrough" in content
        assert "demos://gco/DEMO_WALKTHROUGH" in content
        assert "demos://gco/INFERENCE_WALKTHROUGH" in content
        assert "demos://gco/LIVE_DEMO" in content

    def test_demos_index_contains_scripts(self):
        result = asyncio.run(run_mcp.mcp.read_resource("demos://gco/index"))
        content = result.contents[0].content
        assert "demos://gco/live_demo.sh" in content
        assert "demos://gco/lib_demo.sh" in content

    def test_demo_reads_walkthrough_with_md_fallback(self):
        result = asyncio.run(run_mcp.mcp.read_resource("demos://gco/DEMO_WALKTHROUGH"))
        content = result.contents[0].content
        assert len(content) > 100

    def test_demo_reads_inference_walkthrough(self):
        result = asyncio.run(run_mcp.mcp.read_resource("demos://gco/INFERENCE_WALKTHROUGH"))
        content = result.contents[0].content
        assert len(content) > 100

    def test_demo_reads_live_demo_docs(self):
        result = asyncio.run(run_mcp.mcp.read_resource("demos://gco/LIVE_DEMO"))
        content = result.contents[0].content
        assert len(content) > 100

    def test_demo_reads_shell_script(self):
        result = asyncio.run(run_mcp.mcp.read_resource("demos://gco/live_demo.sh"))
        content = result.contents[0].content
        assert len(content) > 50

    def test_demo_reads_lib_script(self):
        result = asyncio.run(run_mcp.mcp.read_resource("demos://gco/lib_demo.sh"))
        content = result.contents[0].content
        assert len(content) > 50

    def test_demo_reads_readme(self):
        result = asyncio.run(run_mcp.mcp.read_resource("demos://gco/README.md"))
        content = result.contents[0].content
        assert "Demo" in content

    def test_demo_missing_returns_available(self):
        result = asyncio.run(run_mcp.mcp.read_resource("demos://gco/nonexistent.sh"))
        content = result.contents[0].content
        assert "not found" in content
        assert "Available" in content


class TestClientResources:
    """Tests for API client example resources."""

    def test_clients_index_contains_examples(self):
        result = asyncio.run(run_mcp.mcp.read_resource("clients://gco/index"))
        content = result.contents[0].content
        assert "Client Examples" in content
        assert "clients://gco/README" in content
        assert "python_boto3_example.py" in content
        assert "aws_cli_examples.sh" in content
        assert "curl_sigv4_proxy_example.sh" in content

    def test_client_reads_python_example(self):
        result = asyncio.run(run_mcp.mcp.read_resource("clients://gco/python_boto3_example.py"))
        content = result.contents[0].content
        assert len(content) > 50

    def test_client_reads_shell_example(self):
        result = asyncio.run(run_mcp.mcp.read_resource("clients://gco/aws_cli_examples.sh"))
        content = result.contents[0].content
        assert len(content) > 50

    def test_client_reads_curl_example(self):
        result = asyncio.run(run_mcp.mcp.read_resource("clients://gco/curl_sigv4_proxy_example.sh"))
        content = result.contents[0].content
        assert len(content) > 50

    def test_client_reads_readme_with_fallback(self):
        result = asyncio.run(run_mcp.mcp.read_resource("clients://gco/README"))
        content = result.contents[0].content
        assert "Client" in content or "API" in content
        assert len(content) > 100

    def test_client_missing_returns_available(self):
        result = asyncio.run(run_mcp.mcp.read_resource("clients://gco/nonexistent.py"))
        content = result.contents[0].content
        assert "not found" in content
        assert "Available" in content


class TestScriptResources:
    """Tests for utility script resources."""

    def test_scripts_index_contains_scripts(self):
        result = asyncio.run(run_mcp.mcp.read_resource("scripts://gco/index"))
        content = result.contents[0].content
        assert "Utility Scripts" in content
        assert "scripts://gco/README" in content
        assert "setup-cluster-access.sh" in content
        assert "bump_version.py" in content

    def test_script_reads_shell_script(self):
        result = asyncio.run(run_mcp.mcp.read_resource("scripts://gco/setup-cluster-access.sh"))
        content = result.contents[0].content
        assert len(content) > 50

    def test_script_reads_python_script(self):
        result = asyncio.run(run_mcp.mcp.read_resource("scripts://gco/bump_version.py"))
        content = result.contents[0].content
        assert len(content) > 50

    def test_script_reads_readme_with_fallback(self):
        result = asyncio.run(run_mcp.mcp.read_resource("scripts://gco/README"))
        content = result.contents[0].content
        assert "Scripts" in content or "scripts" in content
        assert len(content) > 50

    def test_script_missing_returns_available(self):
        result = asyncio.run(run_mcp.mcp.read_resource("scripts://gco/nonexistent.sh"))
        content = result.contents[0].content
        assert "not found" in content
        assert "Available" in content


class TestInfraResources:
    """Tests for infrastructure resources."""

    def test_infra_index_contains_security_section(self):
        result = asyncio.run(run_mcp.mcp.read_resource("infra://gco/index"))
        content = result.contents[0].content
        assert "Security" in content or "Linting" in content
        assert ".checkov.yaml" in content
        assert ".gitleaks.toml" in content

    def test_infra_index_contains_dockerfile_readme(self):
        result = asyncio.run(run_mcp.mcp.read_resource("infra://gco/index"))
        content = result.contents[0].content
        assert "infra://gco/dockerfiles/README.md" in content

    def test_infra_index_references_related_resources(self):
        result = asyncio.run(run_mcp.mcp.read_resource("infra://gco/index"))
        content = result.contents[0].content
        assert "scripts://gco/index" in content
        assert "demos://gco/index" in content

    def test_infra_dockerfile_readme(self):
        result = asyncio.run(run_mcp.mcp.read_resource("infra://gco/dockerfiles/README.md"))
        content = result.contents[0].content
        assert len(content) > 10
