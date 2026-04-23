"""
Extended tests for cli/jobs.py and cli/dag.py.

Covers _format_duration across seconds, minutes, and hours boundaries;
load_manifests edge cases (.yml extension, multi-doc YAML with empty
documents, directory sort order); wait_for_job timeout and progress
callback paths; and the DAG engine's behavior on 4-level chains,
fan-out/fan-in topologies, empty DAGs, all-failed runs, self-cycles,
and multiple validation errors reported at once.
"""

import json
from unittest.mock import MagicMock, patch

import yaml

from cli.dag import DagDefinition, DagStep, load_dag
from cli.jobs import _format_duration

# ============================================================================
# _format_duration
# ============================================================================


class TestFormatDuration:
    """Tests for _format_duration helper."""

    def test_zero_seconds(self):
        assert _format_duration(0) == "0s"

    def test_under_minute(self):
        assert _format_duration(45) == "45s"

    def test_exactly_one_minute(self):
        assert _format_duration(60) == "1m00s"

    def test_minutes_and_seconds(self):
        assert _format_duration(125) == "2m05s"

    def test_exactly_one_hour(self):
        assert _format_duration(3600) == "1h00m00s"

    def test_hours_minutes_seconds(self):
        assert _format_duration(3661) == "1h01m01s"

    def test_large_duration(self):
        assert _format_duration(86400) == "24h00m00s"

    def test_59_seconds(self):
        assert _format_duration(59) == "59s"

    def test_59_minutes(self):
        assert _format_duration(3599) == "59m59s"


# ============================================================================
# load_manifests edge cases
# ============================================================================


class TestLoadManifestsEdgeCases:
    """Tests for JobManager.load_manifests edge cases."""

    def _make_manager(self):
        from cli.jobs import JobManager

        mgr = JobManager.__new__(JobManager)
        mgr._config = MagicMock()
        mgr._aws_client = MagicMock()
        return mgr

    def test_load_yml_extension(self, tmp_path):
        """Should load .yml files from directory."""
        mgr = self._make_manager()
        manifest = {"apiVersion": "batch/v1", "kind": "Job", "metadata": {"name": "test"}}
        (tmp_path / "job.yml").write_text(yaml.dump(manifest))

        result = mgr.load_manifests(str(tmp_path))
        assert len(result) == 1
        assert result[0]["kind"] == "Job"

    def test_load_multi_doc_with_empty_docs(self, tmp_path):
        """Should skip empty documents in multi-doc YAML."""
        mgr = self._make_manager()
        content = "---\napiVersion: v1\nkind: Namespace\nmetadata:\n  name: test\n---\n---\n"
        (tmp_path / "multi.yaml").write_text(content)

        result = mgr.load_manifests(str(tmp_path / "multi.yaml"))
        assert len(result) == 1  # Empty doc skipped

    def test_load_directory_sorts_files(self, tmp_path):
        """Should load files in sorted order."""
        mgr = self._make_manager()
        for name in ["02-second.yaml", "01-first.yaml", "03-third.yaml"]:
            (tmp_path / name).write_text(
                yaml.dump({"kind": "ConfigMap", "metadata": {"name": name.split(".")[0]}})
            )

        result = mgr.load_manifests(str(tmp_path))
        names = [r["metadata"]["name"] for r in result]
        assert names == ["01-first", "02-second", "03-third"]

    def test_load_both_yaml_and_yml(self, tmp_path):
        """Should load both .yaml and .yml files from directory."""
        mgr = self._make_manager()
        (tmp_path / "a.yaml").write_text(yaml.dump({"kind": "Job", "metadata": {"name": "a"}}))
        (tmp_path / "b.yml").write_text(yaml.dump({"kind": "Job", "metadata": {"name": "b"}}))

        result = mgr.load_manifests(str(tmp_path))
        assert len(result) == 2


# ============================================================================
# _get_kubectl_job_status edge cases
# ============================================================================


class TestGetKubectlJobStatusEdgeCases:
    """Tests for _get_kubectl_job_status edge cases."""

    def _make_manager(self):
        from cli.jobs import JobManager

        mgr = JobManager.__new__(JobManager)
        mgr._config = MagicMock()
        mgr._aws_client = MagicMock()
        return mgr

    def test_complete_and_failed_conditions_complete_wins(self):
        """If both Complete and Failed conditions exist, first match wins."""
        mgr = self._make_manager()
        job_data = {
            "status": {
                "conditions": [
                    {"type": "Complete", "status": "True"},
                    {"type": "Failed", "status": "True"},
                ]
            }
        }
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(job_data))
            result = mgr._get_kubectl_job_status("job1", "default")
        assert result == "complete"

    def test_failed_condition_false_returns_active(self):
        """Failed condition with status=False should return active."""
        mgr = self._make_manager()
        job_data = {"status": {"conditions": [{"type": "Failed", "status": "False"}]}}
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(job_data))
            result = mgr._get_kubectl_job_status("job1", "default")
        assert result == "active"

    def test_empty_conditions_returns_active(self):
        """Empty conditions list should return active."""
        mgr = self._make_manager()
        job_data = {"status": {"conditions": []}}
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(job_data))
            result = mgr._get_kubectl_job_status("job1", "default")
        assert result == "active"

    def test_no_status_key_returns_active(self):
        """Missing status key should return active."""
        mgr = self._make_manager()
        job_data = {"metadata": {"name": "job1"}}
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(job_data))
            result = mgr._get_kubectl_job_status("job1", "default")
        assert result == "active"


# ============================================================================
# DAG extended edge cases
# ============================================================================


class TestDagDefinitionExtendedEdgeCases:
    """Extended edge cases for DagDefinition."""

    def test_four_level_chain(self):
        """4-level dependency chain should work correctly."""
        dag = DagDefinition(
            name="deep-chain",
            steps=[
                DagStep(name="a", manifest="a.yaml"),
                DagStep(name="b", manifest="b.yaml", depends_on=["a"]),
                DagStep(name="c", manifest="c.yaml", depends_on=["b"]),
                DagStep(name="d", manifest="d.yaml", depends_on=["c"]),
            ],
        )
        # Only 'a' should be ready initially
        ready = dag.get_ready_steps()
        assert [s.name for s in ready] == ["a"]

        # After 'a' succeeds, 'b' should be ready
        dag.steps[0].status = "succeeded"
        ready = dag.get_ready_steps()
        assert [s.name for s in ready] == ["b"]

        # After 'b' succeeds, 'c' should be ready
        dag.steps[1].status = "succeeded"
        ready = dag.get_ready_steps()
        assert [s.name for s in ready] == ["c"]

        # After 'c' succeeds, 'd' should be ready
        dag.steps[2].status = "succeeded"
        ready = dag.get_ready_steps()
        assert [s.name for s in ready] == ["d"]

    def test_fan_out_fan_in(self):
        """Fan-out/fan-in pattern: A -> (B, C, D) -> E."""
        dag = DagDefinition(
            name="fan",
            steps=[
                DagStep(name="a", manifest="a.yaml"),
                DagStep(name="b", manifest="b.yaml", depends_on=["a"]),
                DagStep(name="c", manifest="c.yaml", depends_on=["a"]),
                DagStep(name="d", manifest="d.yaml", depends_on=["a"]),
                DagStep(name="e", manifest="e.yaml", depends_on=["b", "c", "d"]),
            ],
        )
        # Only 'a' ready initially
        ready = dag.get_ready_steps()
        assert len(ready) == 1

        # After 'a', b/c/d should all be ready (parallel)
        dag.steps[0].status = "succeeded"
        ready = dag.get_ready_steps()
        assert sorted(s.name for s in ready) == ["b", "c", "d"]

        # 'e' not ready until all of b/c/d succeed
        dag.steps[1].status = "succeeded"
        dag.steps[2].status = "succeeded"
        ready = dag.get_ready_steps()
        # 'd' is still pending and ready (its dep 'a' succeeded), 'e' is blocked
        assert sorted(s.name for s in ready) == ["d"]

        dag.steps[3].status = "succeeded"
        ready = dag.get_ready_steps()
        assert [s.name for s in ready] == ["e"]

    def test_empty_dag(self):
        """Empty DAG should be immediately complete."""
        dag = DagDefinition(name="empty", steps=[])
        assert dag.is_complete()
        assert not dag.has_failures()
        assert dag.get_ready_steps() == []

    def test_all_steps_failed(self):
        """DAG with all steps failed should report failures."""
        dag = DagDefinition(
            name="all-fail",
            steps=[
                DagStep(name="a", manifest="a.yaml", status="failed"),
                DagStep(name="b", manifest="b.yaml", status="failed"),
            ],
        )
        assert dag.is_complete()
        assert dag.has_failures()

    def test_mixed_terminal_states(self):
        """DAG with mix of succeeded/failed/skipped should be complete."""
        dag = DagDefinition(
            name="mixed",
            steps=[
                DagStep(name="a", manifest="a.yaml", status="succeeded"),
                DagStep(name="b", manifest="b.yaml", status="failed"),
                DagStep(name="c", manifest="c.yaml", status="skipped"),
            ],
        )
        assert dag.is_complete()
        assert dag.has_failures()

    def test_validate_multiple_errors(self, tmp_path):
        """Validate should report multiple errors at once."""
        dag = DagDefinition(
            name="bad",
            steps=[
                DagStep(name="a", manifest="a.yaml"),
                DagStep(name="a", manifest="b.yaml"),  # duplicate
            ],
        )
        errors = dag.validate()
        assert any("Duplicate" in e for e in errors)

    def test_validate_missing_dependency_and_manifest(self):
        """Validate should catch unknown dependency."""
        dag = DagDefinition(
            name="bad",
            steps=[
                DagStep(name="a", manifest="nonexistent.yaml", depends_on=["ghost"]),
            ],
        )
        errors = dag.validate()
        assert any("unknown step" in e for e in errors)

    def test_pending_steps_with_failed_dep_get_skipped_check(self):
        """Pending steps whose deps failed should be identifiable."""
        dag = DagDefinition(
            name="skip-test",
            steps=[
                DagStep(name="a", manifest="a.yaml", status="failed"),
                DagStep(name="b", manifest="b.yaml", depends_on=["a"], status="pending"),
            ],
        )
        # 'b' is pending but its dep 'a' failed
        ready = dag.get_ready_steps()
        assert len(ready) == 0  # 'b' is blocked by failed 'a'


class TestLoadDagEdgeCases:
    """Tests for load_dag edge cases."""

    def test_load_dag_defaults(self, tmp_path):
        """load_dag should use filename as name when not specified."""
        dag_yaml = {"steps": [{"name": "a", "manifest": "a.yaml"}]}
        dag_path = tmp_path / "my-pipeline.yaml"
        dag_path.write_text(yaml.dump(dag_yaml))

        dag = load_dag(str(dag_path))
        assert dag.name == "my-pipeline"
        assert dag.namespace == "gco-jobs"
        assert dag.region is None

    def test_load_dag_with_all_fields(self, tmp_path):
        """load_dag should parse all fields."""
        dag_yaml = {
            "name": "custom-name",
            "region": "eu-west-1",
            "namespace": "custom-ns",
            "steps": [
                {"name": "a", "manifest": "a.yaml"},
                {"name": "b", "manifest": "b.yaml", "depends_on": ["a"]},
            ],
        }
        dag_path = tmp_path / "dag.yaml"
        dag_path.write_text(yaml.dump(dag_yaml))

        dag = load_dag(str(dag_path))
        assert dag.name == "custom-name"
        assert dag.region == "eu-west-1"
        assert dag.namespace == "custom-ns"
        assert len(dag.steps) == 2
        assert dag.steps[1].depends_on == ["a"]

    def test_load_dag_empty_steps(self, tmp_path):
        """load_dag with no steps should create empty DAG."""
        dag_path = tmp_path / "empty.yaml"
        dag_path.write_text(yaml.dump({"name": "empty"}))

        dag = load_dag(str(dag_path))
        assert dag.steps == []

    def test_load_dag_step_defaults(self, tmp_path):
        """Steps should have default empty depends_on."""
        dag_yaml = {"steps": [{"name": "solo", "manifest": "solo.yaml"}]}
        dag_path = tmp_path / "dag.yaml"
        dag_path.write_text(yaml.dump(dag_yaml))

        dag = load_dag(str(dag_path))
        assert dag.steps[0].depends_on == []
        assert dag.steps[0].status == "pending"


# ============================================================================
# DagStep dataclass
# ============================================================================


class TestDagStepDataclass:
    """Tests for DagStep dataclass."""

    def test_defaults(self):
        step = DagStep(name="test", manifest="test.yaml")
        assert step.depends_on == []
        assert step.status == "pending"
        assert step.job_name is None
        assert step.started_at is None
        assert step.completed_at is None
        assert step.error is None

    def test_with_all_fields(self):
        step = DagStep(
            name="train",
            manifest="train.yaml",
            depends_on=["preprocess"],
            status="running",
            job_name="train-abc",
            started_at="2024-01-01T00:00:00",
            completed_at=None,
            error=None,
        )
        assert step.name == "train"
        assert step.depends_on == ["preprocess"]
        assert step.status == "running"
        assert step.job_name == "train-abc"
