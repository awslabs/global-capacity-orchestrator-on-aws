"""
Extended unit coverage for ``cli/images.py``.

The base ``tests/test_images_cli.py`` covers the property-based
validation surface plus build/push happy paths. This module fills in
the read-only methods (``list_repos``, ``list_tags``, ``describe``,
``get_uri``, ``replication_get``, ``replication_status``),
administrative methods (``init`` retry paths, ``lifecycle_get/set``,
``replication_sync``), and the destructive methods (``delete_tag``,
``delete_repo``, ``cleanup``, ``prune``, ``orphans``). Each test
mocks the ECR boto3 client and asserts the request shape and the
manager-side aggregation logic.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from cli.images import ImageManager, _isoformat, get_image_manager

# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_config() -> Any:
    config = MagicMock()
    config.global_region = "us-east-2"
    config.project_name = "gco"
    config.regions = ["us-east-2", "us-west-2", "eu-west-1"]
    return config


@pytest.fixture
def manager(mock_config: Any) -> ImageManager:
    with patch("cli.images.get_config", return_value=mock_config):
        mgr = ImageManager(config=mock_config, region="us-east-2")
    mgr._account_id_cache = "123456789012"
    return mgr


def _ecr_mock() -> Any:
    """Build a mock ECR client whose exception types are real subclasses of ClientError."""
    mock_ecr = MagicMock()

    class _RepoExists(ClientError):
        pass

    class _LifecycleNotFound(ClientError):
        pass

    class _RepoNotFound(ClientError):
        pass

    class _ImageNotFound(ClientError):
        pass

    mock_ecr.exceptions.RepositoryAlreadyExistsException = _RepoExists
    mock_ecr.exceptions.LifecyclePolicyNotFoundException = _LifecycleNotFound
    mock_ecr.exceptions.RepositoryNotFoundException = _RepoNotFound
    mock_ecr.exceptions.ImageNotFoundException = _ImageNotFound
    return mock_ecr


def _make_paginator(pages: list[dict[str, Any]]) -> Any:
    """Return a paginator stand-in whose ``paginate`` yields ``pages``."""
    paginator = MagicMock()
    paginator.paginate.return_value = iter(pages)
    return paginator


# ---------------------------------------------------------------------------
# Region resolution
# ---------------------------------------------------------------------------


class TestRegionResolution:
    def test_explicit_region_argument_wins(self, mock_config: Any) -> None:
        with patch("cli.images.get_config", return_value=mock_config):
            mgr = ImageManager(config=mock_config, region="ap-south-1")
        assert mgr.region == "ap-south-1"

    def test_falls_through_to_global_region_when_no_regions(
        self, mock_config: Any, monkeypatch: Any
    ) -> None:
        # Strip both config.regions and AWS_DEFAULT_REGION; only
        # config.global_region remains.
        bare = MagicMock(spec=["global_region", "project_name"])
        bare.global_region = "us-east-2"
        bare.project_name = "gco"
        monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
        with patch("cli.images.get_config", return_value=bare):
            mgr = ImageManager(config=bare)
        assert mgr.region == "us-east-2"


# ---------------------------------------------------------------------------
# Account / registry / ARN helpers
# ---------------------------------------------------------------------------


class TestAccountAndRegistry:
    def test_account_id_caches_result(self, mock_config: Any) -> None:
        sts_client = MagicMock()
        sts_client.get_caller_identity.return_value = {"Account": "999999999999"}
        with (
            patch("cli.images.get_config", return_value=mock_config),
            patch("cli.images.boto3.client", return_value=sts_client) as mock_boto,
        ):
            mgr = ImageManager(config=mock_config, region="us-east-2")
            first = mgr._account_id()
            second = mgr._account_id()
        assert first == second == "999999999999"
        # Second call must hit the cache, not boto3.
        assert mock_boto.call_count == 1

    def test_registry_host_combines_account_and_region(self, manager: ImageManager) -> None:
        assert manager._registry_host() == "123456789012.dkr.ecr.us-east-2.amazonaws.com"

    def test_repo_arn_includes_prefix(self, manager: ImageManager) -> None:
        assert manager._repo_arn("svc") == "arn:aws:ecr:us-east-2:123456789012:repository/gco/svc"


# ---------------------------------------------------------------------------
# Validation negative cases
# ---------------------------------------------------------------------------


class TestValidation:
    def test_validate_name_accepts_single_char(self, manager: ImageManager) -> None:
        assert manager._validate_name("a") == "a"

    def test_validate_tag_accepts_underscore_prefix(self, manager: ImageManager) -> None:
        assert manager._validate_tag("_init") == "_init"

    def test_validate_context_rejects_path_traversal(self, manager: ImageManager) -> None:
        with pytest.raises(ValueError, match="path traversal"):
            manager._validate_context("foo/../bar")

    def test_validate_context_missing_dir_raises(
        self, manager: ImageManager, tmp_path: Any
    ) -> None:
        with pytest.raises(FileNotFoundError):
            manager._validate_context(str(tmp_path / "missing"))

    def test_validate_context_file_path_rejected(
        self, manager: ImageManager, tmp_path: Any
    ) -> None:
        f = tmp_path / "Dockerfile"
        f.write_text("FROM scratch\n")
        with pytest.raises(ValueError, match="not a directory"):
            manager._validate_context(str(f))


# ---------------------------------------------------------------------------
# Default tag (git short SHA fallback)
# ---------------------------------------------------------------------------


class TestDefaultTag:
    def test_default_tag_uses_git_sha(self, manager: ImageManager) -> None:
        result = MagicMock()
        result.returncode = 0
        result.stdout = "abc1234\n"
        with patch("cli.images.subprocess.run", return_value=result):
            assert manager._default_tag() == "abc1234"

    def test_default_tag_falls_back_to_latest_when_git_missing(self, manager: ImageManager) -> None:
        with patch("cli.images.subprocess.run", side_effect=FileNotFoundError("git")):
            assert manager._default_tag() == "latest"

    def test_default_tag_falls_back_when_git_returns_non_zero(self, manager: ImageManager) -> None:
        result = MagicMock()
        result.returncode = 128
        result.stdout = ""
        with patch("cli.images.subprocess.run", return_value=result):
            assert manager._default_tag() == "latest"


# ---------------------------------------------------------------------------
# init() — exception paths
# ---------------------------------------------------------------------------


class TestInit:
    def test_init_re_raises_unrelated_create_client_error(self, manager: ImageManager) -> None:
        ecr = _ecr_mock()
        err = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "denied"}},
            "CreateRepository",
        )
        ecr.create_repository.side_effect = err
        with patch.object(manager, "_ecr_client", return_value=ecr):
            with pytest.raises(ClientError) as excinfo:
                manager.init("svc")
            assert "AccessDeniedException" in str(excinfo.value)

    def test_init_lifecycle_error_is_swallowed(self, manager: ImageManager) -> None:
        """A failing lifecycle policy doesn't surface to callers."""
        ecr = _ecr_mock()
        ecr.create_repository.return_value = {}
        ecr.put_lifecycle_policy.side_effect = ClientError(
            {"Error": {"Code": "ValidationException", "Message": "bad policy"}},
            "PutLifecyclePolicy",
        )
        with patch.object(manager, "_ecr_client", return_value=ecr):
            result = manager.init("svc")
        assert result["created"] is True

    def test_init_with_retain_applies_resource_tag(self, manager: ImageManager) -> None:
        ecr = _ecr_mock()
        ecr.create_repository.return_value = {}
        ecr.put_lifecycle_policy.return_value = {}
        with patch.object(manager, "_ecr_client", return_value=ecr):
            result = manager.init("svc", retain=True)
        assert result["retain"] is True
        ecr.tag_resource.assert_called_once()
        kwargs = ecr.tag_resource.call_args.kwargs
        assert kwargs["tags"] == [{"Key": "gco:retain", "Value": "true"}]

    def test_init_with_retain_swallows_tag_failure(self, manager: ImageManager) -> None:
        ecr = _ecr_mock()
        ecr.create_repository.return_value = {}
        ecr.put_lifecycle_policy.return_value = {}
        ecr.tag_resource.side_effect = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "no tag perms"}},
            "TagResource",
        )
        with patch.object(manager, "_ecr_client", return_value=ecr):
            # Should not raise.
            result = manager.init("svc", retain=True)
        assert result["retain"] is True


# ---------------------------------------------------------------------------
# Read-only surface
# ---------------------------------------------------------------------------


class TestListRepos:
    def test_filters_to_project_prefix(self, manager: ImageManager) -> None:
        ecr = _ecr_mock()
        pages = [
            {
                "repositories": [
                    {
                        "repositoryName": "gco/my-app",
                        "repositoryArn": "arn:aws:ecr:us-east-2:123456789012:repository/gco/my-app",
                        "repositoryUri": "123456789012.dkr.ecr.us-east-2.amazonaws.com/gco/my-app",
                        "createdAt": datetime(2026, 1, 1, tzinfo=UTC),
                        "imageTagMutability": "MUTABLE",
                    },
                    # Out of scope — must be skipped.
                    {"repositoryName": "other/svc"},
                ]
            }
        ]
        ecr.get_paginator.side_effect = lambda op: _make_paginator(
            pages if op == "describe_repositories" else [{"imageDetails": []}]
        )
        with patch.object(manager, "_ecr_client", return_value=ecr):
            repos = manager.list_repos()
        assert len(repos) == 1
        repo = repos[0]
        assert repo["name"] == "gco/my-app"
        assert repo["tag_mutability"] == "MUTABLE"
        assert repo["image_count"] == 0
        assert repo["created_at"].startswith("2026-01-01")

    def test_image_count_swallows_describe_errors(self, manager: ImageManager) -> None:
        ecr = _ecr_mock()
        ecr.get_paginator.return_value = _make_paginator([])
        # Force describe_images to blow up so we exercise the except path.

        def paginator_side(op: str) -> Any:
            if op == "describe_images":
                raise ClientError(
                    {"Error": {"Code": "InternalFailure", "Message": "x"}},
                    "GetPaginator",
                )
            return _make_paginator(
                [
                    {
                        "repositories": [
                            {"repositoryName": "gco/svc"},
                        ]
                    }
                ]
            )

        ecr.get_paginator.side_effect = paginator_side
        with patch.object(manager, "_ecr_client", return_value=ecr):
            repos = manager.list_repos()
        assert repos[0]["image_count"] == 0


class TestListTags:
    def test_emits_one_row_per_tag_and_handles_untagged(self, manager: ImageManager) -> None:
        ecr = _ecr_mock()
        pages = [
            {
                "imageDetails": [
                    {
                        "imageTags": ["v1", "latest"],
                        "imageDigest": "sha256:" + "a" * 64,
                        "imagePushedAt": datetime(2026, 5, 1, tzinfo=UTC),
                        "imageSizeInBytes": 1234,
                    },
                    {
                        # Untagged image: imageTags missing → list_tags emits a row with tag=None.
                        "imageDigest": "sha256:" + "b" * 64,
                        "imagePushedAt": datetime(2026, 5, 2, tzinfo=UTC),
                        "imageSizeInBytes": 4321,
                    },
                ]
            }
        ]
        ecr.get_paginator.return_value = _make_paginator(pages)
        with patch.object(manager, "_ecr_client", return_value=ecr):
            rows = manager.list_tags("svc")
        # 2 tags from the first detail + 1 untagged row from the second.
        assert len(rows) == 3
        tags = [r["tag"] for r in rows]
        assert "v1" in tags
        assert "latest" in tags
        assert None in tags


class TestDescribe:
    def test_returns_empty_when_no_details(self, manager: ImageManager) -> None:
        ecr = _ecr_mock()
        ecr.describe_images.return_value = {"imageDetails": []}
        with patch.object(manager, "_ecr_client", return_value=ecr):
            assert manager.describe("svc", "v1") == {}

    def test_unpacks_detail_into_documented_shape(self, manager: ImageManager) -> None:
        ecr = _ecr_mock()
        ecr.describe_images.return_value = {
            "imageDetails": [
                {
                    "imageTags": ["v1"],
                    "imageDigest": "sha256:" + "a" * 64,
                    "imagePushedAt": datetime(2026, 5, 1, tzinfo=UTC),
                    "imageSizeInBytes": 1234,
                    "imageScanFindingsSummary": {"findingSeverityCounts": {}},
                }
            ]
        }
        with patch.object(manager, "_ecr_client", return_value=ecr):
            result = manager.describe("svc", "v1")
        assert result["name"] == "gco/svc"
        assert result["tag"] == "v1"
        assert result["digest"].startswith("sha256:")
        assert result["pushed_at"].startswith("2026-05-01")
        assert result["scan_findings_summary"] == {"findingSeverityCounts": {}}


class TestReplicationGet:
    def test_returns_empty_when_policy_missing(self, manager: ImageManager) -> None:
        ecr = _ecr_mock()
        ecr.get_registry_policy.side_effect = ClientError(
            {"Error": {"Code": "RegistryPolicyNotFoundException", "Message": ""}},
            "GetRegistryPolicy",
        )
        with patch.object(manager, "_ecr_client", return_value=ecr):
            assert manager.replication_get() == {}

    def test_re_raises_unexpected_error(self, manager: ImageManager) -> None:
        ecr = _ecr_mock()
        ecr.get_registry_policy.side_effect = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": ""}},
            "GetRegistryPolicy",
        )
        with (
            patch.object(manager, "_ecr_client", return_value=ecr),
            pytest.raises(ClientError),
        ):
            manager.replication_get()

    def test_unpacks_policy_text(self, manager: ImageManager) -> None:
        ecr = _ecr_mock()
        ecr.get_registry_policy.return_value = {
            "registryId": "123456789012",
            "policyText": json.dumps({"rules": []}),
        }
        with patch.object(manager, "_ecr_client", return_value=ecr):
            result = manager.replication_get()
        assert result["registryId"] == "123456789012"
        assert result["policy"] == {"rules": []}


class TestReplicationStatus:
    def test_aggregates_status_per_image_per_region(self, manager: ImageManager) -> None:
        ecr = _ecr_mock()

        def paginator_side(op: str) -> Any:
            if op == "describe_repositories":
                return _make_paginator(
                    [
                        {
                            "repositories": [
                                {"repositoryName": "gco/svc"},
                            ]
                        }
                    ]
                )
            if op == "describe_images":
                return _make_paginator(
                    [
                        {
                            "imageDetails": [
                                {
                                    "imageDigest": "sha256:" + "a" * 64,
                                    "imageTags": ["v1"],
                                }
                            ]
                        }
                    ]
                )
            raise AssertionError(f"unexpected paginator: {op}")

        ecr.get_paginator.side_effect = paginator_side
        ecr.describe_image_replication_status.return_value = {
            "replicationStatuses": [
                {"region": "us-west-2", "status": "COMPLETE", "registryId": "123456789012"},
                {"region": "eu-west-1", "status": "IN_PROGRESS", "registryId": "123456789012"},
            ]
        }
        with patch.object(manager, "_ecr_client", return_value=ecr):
            rows = manager.replication_status()
        assert {r["region"] for r in rows} == {"us-west-2", "eu-west-1"}
        assert all(r["repository"] == "gco/svc" for r in rows)


# ---------------------------------------------------------------------------
# Lifecycle policy
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_get_returns_empty_when_not_found(self, manager: ImageManager) -> None:
        ecr = _ecr_mock()
        err = ecr.exceptions.LifecyclePolicyNotFoundException(
            {"Error": {"Code": "LifecyclePolicyNotFoundException", "Message": ""}},
            "GetLifecyclePolicy",
        )
        ecr.get_lifecycle_policy.side_effect = err
        with patch.object(manager, "_ecr_client", return_value=ecr):
            assert manager.lifecycle_get("svc") == {}

    def test_get_re_raises_other_errors(self, manager: ImageManager) -> None:
        ecr = _ecr_mock()
        ecr.get_lifecycle_policy.side_effect = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": ""}},
            "GetLifecyclePolicy",
        )
        with (
            patch.object(manager, "_ecr_client", return_value=ecr),
            pytest.raises(ClientError),
        ):
            manager.lifecycle_get("svc")

    def test_get_decodes_policy_text(self, manager: ImageManager) -> None:
        ecr = _ecr_mock()
        ecr.get_lifecycle_policy.return_value = {
            "lifecyclePolicyText": json.dumps({"rules": [{"rulePriority": 1}]})
        }
        with patch.object(manager, "_ecr_client", return_value=ecr):
            result = manager.lifecycle_get("svc")
        assert result["name"] == "gco/svc"
        assert result["policy"] == {"rules": [{"rulePriority": 1}]}

    def test_set_round_trips_policy(self, manager: ImageManager) -> None:
        ecr = _ecr_mock()
        ecr.put_lifecycle_policy.return_value = {"registryId": "123456789012"}
        policy = {"rules": [{"rulePriority": 1}]}
        with patch.object(manager, "_ecr_client", return_value=ecr):
            result = manager.lifecycle_set("svc", policy)
        ecr.put_lifecycle_policy.assert_called_once()
        kwargs = ecr.put_lifecycle_policy.call_args.kwargs
        assert kwargs["repositoryName"] == "gco/svc"
        assert json.loads(kwargs["lifecyclePolicyText"]) == policy
        assert result["registry_id"] == "123456789012"


# ---------------------------------------------------------------------------
# Replication sync
# ---------------------------------------------------------------------------


class TestReplicationSync:
    def test_writes_rule_to_every_other_region(self, manager: ImageManager) -> None:
        ecr = _ecr_mock()
        ecr.put_replication_configuration.return_value = {"replicationConfiguration": {}}
        with patch.object(manager, "_ecr_client", return_value=ecr):
            result = manager.replication_sync()
        ecr.put_replication_configuration.assert_called_once()
        cfg = ecr.put_replication_configuration.call_args.kwargs["replicationConfiguration"]
        rule = cfg["rules"][0]
        regions = {d["region"] for d in rule["destinations"]}
        assert "us-east-2" not in regions  # source region elided
        assert {"us-west-2", "eu-west-1"} <= regions
        assert rule["repositoryFilters"][0]["filter"] == "gco/"
        assert result["destinations"] == ["us-west-2", "eu-west-1"]

    def test_no_destinations_writes_empty_rules(self, mock_config: Any) -> None:
        single_region = MagicMock()
        single_region.global_region = "us-east-2"
        single_region.project_name = "gco"
        single_region.regions = ["us-east-2"]
        with patch("cli.images.get_config", return_value=single_region):
            mgr = ImageManager(config=single_region, region="us-east-2")
        mgr._account_id_cache = "123456789012"

        ecr = _ecr_mock()
        ecr.put_replication_configuration.return_value = {"replicationConfiguration": {}}
        with patch.object(mgr, "_ecr_client", return_value=ecr):
            result = mgr.replication_sync()
        cfg = ecr.put_replication_configuration.call_args.kwargs["replicationConfiguration"]
        assert cfg == {"rules": []}
        assert result["destinations"] == []


# ---------------------------------------------------------------------------
# Destructive — single-tag and full-repo deletes
# ---------------------------------------------------------------------------


class TestDelete:
    def test_delete_tag_returns_summary(self, manager: ImageManager) -> None:
        ecr = _ecr_mock()
        ecr.batch_delete_image.return_value = {
            "imageIds": [{"imageDigest": "sha256:abc", "imageTag": "v1"}],
            "failures": [],
        }
        with patch.object(manager, "_ecr_client", return_value=ecr):
            result = manager.delete_tag("svc", "v1")
        assert result["tag"] == "v1"
        assert result["deleted"] == [{"digest": "sha256:abc", "tag": "v1"}]

    def test_delete_repo_force_passes_through(self, manager: ImageManager) -> None:
        ecr = _ecr_mock()
        ecr.delete_repository.return_value = {
            "repository": {"registryId": "123456789012"},
        }
        with patch.object(manager, "_ecr_client", return_value=ecr):
            result = manager.delete_repo("svc", force=True)
        kwargs = ecr.delete_repository.call_args.kwargs
        assert kwargs["force"] is True
        assert result["registry_id"] == "123456789012"


# ---------------------------------------------------------------------------
# cleanup() / prune()
# ---------------------------------------------------------------------------


class TestCleanup:
    def test_cleanup_requires_name_or_all(self, manager: ImageManager) -> None:
        with pytest.raises(ValueError, match="cleanup\\(\\) requires"):
            manager.cleanup()

    def test_cleanup_one_repo_aggregates_untagged(self, manager: ImageManager) -> None:
        ecr = _ecr_mock()
        ecr.get_paginator.return_value = _make_paginator(
            [
                {
                    "imageDetails": [
                        {"imageDigest": "sha256:a", "imageSizeInBytes": 100},
                        {"imageDigest": "sha256:b", "imageSizeInBytes": 200},
                        {"imageSizeInBytes": 50},  # missing digest — skipped
                    ]
                }
            ]
        )
        ecr.batch_delete_image.return_value = {
            "imageIds": [{"imageDigest": "sha256:a"}, {"imageDigest": "sha256:b"}]
        }
        with patch.object(manager, "_ecr_client", return_value=ecr):
            result = manager.cleanup(name="svc")
        assert result["repos_touched"] == 1
        assert result["tags_deleted"] == 2
        assert result["bytes_freed"] == 300

    def test_cleanup_all_iterates_every_repo(self, manager: ImageManager) -> None:
        ecr = _ecr_mock()
        # describe_repositories returns 2 repos; describe_images returns
        # untagged for each.
        repos_pages = [
            {
                "repositories": [
                    {"repositoryName": "gco/a"},
                    {"repositoryName": "gco/b"},
                ]
            }
        ]
        untagged_pages = [
            {"imageDetails": [{"imageDigest": "sha256:" + "a" * 64, "imageSizeInBytes": 10}]}
        ]

        def paginator_side(op: str) -> Any:
            if op == "describe_repositories":
                return _make_paginator(list(repos_pages))
            return _make_paginator(list(untagged_pages))

        ecr.get_paginator.side_effect = paginator_side
        ecr.batch_delete_image.return_value = {"imageIds": [{"imageDigest": "x"}]}
        with patch.object(manager, "_ecr_client", return_value=ecr):
            result = manager.cleanup(all=True)
        assert result["repos_touched"] == 2

    def test_cleanup_skips_repo_on_describe_error(self, manager: ImageManager) -> None:
        ecr = _ecr_mock()
        ecr.get_paginator.side_effect = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": ""}},
            "GetPaginator",
        )
        with patch.object(manager, "_ecr_client", return_value=ecr):
            result = manager.cleanup(name="svc")
        assert result["repos_touched"] == 0

    def test_cleanup_handles_chunks_over_100(self, manager: ImageManager) -> None:
        ecr = _ecr_mock()
        details = [{"imageDigest": f"sha256:{i:064x}", "imageSizeInBytes": 1} for i in range(150)]
        ecr.get_paginator.return_value = _make_paginator([{"imageDetails": details}])
        # Each batch_delete_image returns whatever was passed in.
        ecr.batch_delete_image.side_effect = lambda **kwargs: {"imageIds": kwargs["imageIds"]}
        with patch.object(manager, "_ecr_client", return_value=ecr):
            result = manager.cleanup(name="svc")
        # 150 ids → 100 + 50 → 2 calls.
        assert ecr.batch_delete_image.call_count == 2
        assert result["tags_deleted"] == 150


class TestPrune:
    def _untagged(self, age_days: int, digest: str = "sha256:" + "a" * 64) -> dict[str, Any]:
        return {
            "imageDigest": digest,
            "imagePushedAt": datetime.now(UTC) - timedelta(days=age_days),
            "imageSizeInBytes": 1000,
        }

    def test_prune_dry_run_does_not_delete(self, manager: ImageManager) -> None:
        ecr = _ecr_mock()

        def paginator_side(op: str) -> Any:
            if op == "describe_repositories":
                return _make_paginator([{"repositories": [{"repositoryName": "gco/svc"}]}])
            return _make_paginator([{"imageDetails": [self._untagged(60)]}])

        ecr.get_paginator.side_effect = paginator_side
        with patch.object(manager, "_ecr_client", return_value=ecr):
            result = manager.prune(dry_run=True)
        ecr.batch_delete_image.assert_not_called()
        assert result["dry_run"] is True
        assert result["tags_deleted"] == 1
        assert result["bytes_freed"] == 1000

    def test_prune_skips_recent_images(self, manager: ImageManager) -> None:
        ecr = _ecr_mock()

        def paginator_side(op: str) -> Any:
            if op == "describe_repositories":
                return _make_paginator([{"repositories": [{"repositoryName": "gco/svc"}]}])
            return _make_paginator(
                [{"imageDetails": [self._untagged(2)]}]
            )  # 2 days old, well within 30d cutoff

        ecr.get_paginator.side_effect = paginator_side
        with patch.object(manager, "_ecr_client", return_value=ecr):
            result = manager.prune(dry_run=False)
        assert result["repos_touched"] == 0
        assert result["tags_deleted"] == 0

    def test_prune_actually_deletes_when_not_dry_run(self, manager: ImageManager) -> None:
        ecr = _ecr_mock()

        def paginator_side(op: str) -> Any:
            if op == "describe_repositories":
                return _make_paginator([{"repositories": [{"repositoryName": "gco/svc"}]}])
            return _make_paginator([{"imageDetails": [self._untagged(60)]}])

        ecr.get_paginator.side_effect = paginator_side
        with patch.object(manager, "_ecr_client", return_value=ecr):
            result = manager.prune(dry_run=False)
        ecr.batch_delete_image.assert_called_once()
        assert result["repos_touched"] == 1
        assert result["tags_deleted"] == 1


# ---------------------------------------------------------------------------
# orphans()
# ---------------------------------------------------------------------------


class TestOrphans:
    def _seed_list_repos(self, ecr: Any) -> None:
        def paginator_side(op: str) -> Any:
            if op == "describe_repositories":
                return _make_paginator([{"repositories": [{"repositoryName": "gco/svc"}]}])
            # describe_images for image_count
            return _make_paginator([{"imageDetails": []}])

        ecr.get_paginator.side_effect = paginator_side

    def test_orphans_excludes_referenced(self, manager: ImageManager) -> None:
        old = (datetime.now(UTC) - timedelta(days=60)).isoformat()
        recent_uri = f"{manager._registry_host()}/gco/svc:in-use"
        ecr = _ecr_mock()
        self._seed_list_repos(ecr)
        # Stub list_tags directly to keep the test readable.
        with (
            patch.object(manager, "_ecr_client", return_value=ecr),
            patch.object(
                manager,
                "list_tags",
                return_value=[
                    {"tag": "in-use", "digest": "sha256:1", "pushed_at": old},
                    {"tag": "stale", "digest": "sha256:2", "pushed_at": old},
                ],
            ),
            patch.object(
                manager,
                "_collect_inference_image_refs",
                return_value={recent_uri},
            ),
        ):
            rows = manager.orphans(threshold_days=30)
        tags = [r["tag"] for r in rows]
        assert "in-use" not in tags
        assert "stale" in tags

    def test_orphans_excludes_recent(self, manager: ImageManager) -> None:
        recent = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        ecr = _ecr_mock()
        self._seed_list_repos(ecr)
        with (
            patch.object(manager, "_ecr_client", return_value=ecr),
            patch.object(
                manager,
                "list_tags",
                return_value=[{"tag": "fresh", "digest": "sha256:1", "pushed_at": recent}],
            ),
            patch.object(manager, "_collect_inference_image_refs", return_value=set()),
        ):
            rows = manager.orphans(threshold_days=30)
        assert rows == []


# ---------------------------------------------------------------------------
# Inference cross-reference lookup
# ---------------------------------------------------------------------------


class TestInferenceImageRefs:
    def test_collects_image_and_canary_uris(self, manager: ImageManager) -> None:
        fake_inference = MagicMock()
        fake_inference.list_endpoints.return_value = [
            {
                "spec": {
                    "image": "registry/img:v1",
                    "canary": {"image": "registry/img:v2"},
                }
            },
            {"spec": {"image": "registry/other:v1"}},
            # Endpoint with no image is skipped.
            {"spec": {}},
        ]
        with patch("cli.inference.InferenceManager", return_value=fake_inference):
            refs = manager._collect_inference_image_refs()
        assert refs == {"registry/img:v1", "registry/img:v2", "registry/other:v1"}

    def test_empty_when_inference_module_blows_up(self, manager: ImageManager) -> None:
        with patch("cli.inference.InferenceManager", side_effect=RuntimeError("boom")):
            assert manager._collect_inference_image_refs() == set()

    def test_empty_when_list_endpoints_blows_up(self, manager: ImageManager) -> None:
        fake_inference = MagicMock()
        fake_inference.list_endpoints.side_effect = RuntimeError("ddb down")
        with patch("cli.inference.InferenceManager", return_value=fake_inference):
            assert manager._collect_inference_image_refs() == set()

    def test_recent_job_image_refs_documented_empty(self, manager: ImageManager) -> None:
        assert manager._collect_recent_job_image_refs() == set()


# ---------------------------------------------------------------------------
# ECR auth + tag-collision wiring (build/push pre-flight)
# ---------------------------------------------------------------------------


class TestEcrLoginAndCollision:
    def test_ecr_login_command_shape(self, manager: ImageManager) -> None:
        ecr = _ecr_mock()
        # base64("AWS:abcdef")
        ecr.get_authorization_token.return_value = {
            "authorizationData": [{"authorizationToken": "QVdTOmFiY2RlZg=="}]
        }
        captured: dict[str, Any] = {}

        def fake_run(cmd: list[str], **kwargs: Any) -> Any:
            captured["cmd"] = cmd
            captured["input"] = kwargs.get("input")
            return MagicMock(returncode=0, stdout=b"", stderr=b"")

        with (
            patch.object(manager, "_ecr_client", return_value=ecr),
            patch("cli.images.subprocess.run", side_effect=fake_run),
        ):
            manager._ecr_login("docker")
        assert captured["cmd"][0] == "docker"
        assert captured["cmd"][1] == "login"
        assert "--password-stdin" in captured["cmd"]
        assert captured["input"] == b"abcdef"

    def test_ecr_login_failure_raises_runtime_error(self, manager: ImageManager) -> None:
        ecr = _ecr_mock()
        ecr.get_authorization_token.return_value = {
            "authorizationData": [{"authorizationToken": "QVdTOmFiY2RlZg=="}]
        }
        with (
            patch.object(manager, "_ecr_client", return_value=ecr),
            patch(
                "cli.images.subprocess.run",
                return_value=MagicMock(returncode=1, stderr=b"denied"),
            ),
            pytest.raises(RuntimeError, match="login.*failed"),
        ):
            manager._ecr_login("docker")

    def test_collision_check_skipped_for_mutable_repo(self, manager: ImageManager) -> None:
        ecr = _ecr_mock()
        ecr.describe_repositories.return_value = {
            "repositories": [{"imageTagMutability": "MUTABLE"}]
        }
        with patch.object(manager, "_ecr_client", return_value=ecr):
            # Should not raise; describe_images shouldn't even be consulted.
            manager._check_tag_immutable_collision("svc", "v1")
        ecr.describe_images.assert_not_called()

    def test_collision_check_passes_when_repo_missing(self, manager: ImageManager) -> None:
        ecr = _ecr_mock()
        err = ecr.exceptions.RepositoryNotFoundException(
            {"Error": {"Code": "RepositoryNotFoundException", "Message": ""}},
            "DescribeRepositories",
        )
        ecr.describe_repositories.side_effect = err
        with patch.object(manager, "_ecr_client", return_value=ecr):
            manager._check_tag_immutable_collision("svc", "v1")  # no raise

    def test_collision_check_passes_when_tag_missing(self, manager: ImageManager) -> None:
        ecr = _ecr_mock()
        ecr.describe_repositories.return_value = {
            "repositories": [{"imageTagMutability": "IMMUTABLE"}]
        }
        err = ecr.exceptions.ImageNotFoundException(
            {"Error": {"Code": "ImageNotFoundException", "Message": ""}},
            "DescribeImages",
        )
        ecr.describe_images.side_effect = err
        with patch.object(manager, "_ecr_client", return_value=ecr):
            manager._check_tag_immutable_collision("svc", "v1")  # no raise


# ---------------------------------------------------------------------------
# Push-only (no build) happy path
# ---------------------------------------------------------------------------


class TestPushOnly:
    def test_push_invokes_tag_then_push(self, manager: ImageManager) -> None:
        ecr = _ecr_mock()
        ecr.create_repository.return_value = {}
        ecr.put_lifecycle_policy.return_value = {}
        ecr.describe_repositories.return_value = {"repositories": []}
        ecr.describe_images.return_value = {"imageDetails": []}
        ecr.get_authorization_token.return_value = {
            "authorizationData": [{"authorizationToken": "QVdTOnRva2Vu"}]
        }

        captured: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: Any) -> Any:
            captured.append(list(cmd))
            return MagicMock(
                returncode=0,
                stdout="digest: sha256:" + "0" * 64,
                stderr="",
            )

        with (
            patch("cli.images.detect_container_runtime", return_value="docker"),
            patch.object(manager, "_ecr_client", return_value=ecr),
            patch("cli.images.subprocess.run", side_effect=fake_run),
        ):
            result = manager.push(name="my-app", tag="v1", local_image="local/my-app:scratch")

        assert any("tag" in cmd and "local/my-app:scratch" in cmd for cmd in captured)
        assert any("push" in cmd for cmd in captured)
        assert result["digest"].startswith("sha256:")
        assert result["repository"] == "gco/my-app"

    def test_push_rejects_empty_local_image(self, manager: ImageManager) -> None:
        with pytest.raises(ValueError, match="non-empty image reference"):
            manager.push(name="my-app", tag="v1", local_image="")


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


class TestIsoformat:
    def test_passes_through_none(self) -> None:
        assert _isoformat(None) is None

    def test_emits_iso_for_datetime(self) -> None:
        d = datetime(2026, 5, 1, tzinfo=UTC)
        assert _isoformat(d).startswith("2026-05-01")

    def test_passes_string_through(self) -> None:
        assert _isoformat("not a date") == "not a date"


class TestParseIso:
    def test_returns_aware_datetime(self) -> None:
        d = ImageManager._parse_iso("2026-05-01T00:00:00+00:00")
        assert d is not None and d.tzinfo is not None

    def test_naive_string_gets_utc(self) -> None:
        d = ImageManager._parse_iso("2026-05-01T00:00:00")
        assert d is not None and d.tzinfo is UTC

    def test_returns_none_for_garbage(self) -> None:
        assert ImageManager._parse_iso("not a date") is None

    def test_returns_none_for_other_types(self) -> None:
        assert ImageManager._parse_iso(123) is None

    def test_passes_through_aware_datetime(self) -> None:
        d = datetime(2026, 5, 1, tzinfo=UTC)
        assert ImageManager._parse_iso(d) is d


class TestFactory:
    def test_get_image_manager_constructs(self, mock_config: Any) -> None:
        with patch("cli.images.get_config", return_value=mock_config):
            mgr = get_image_manager(config=mock_config, region="us-east-2")
        assert isinstance(mgr, ImageManager)
        assert mgr.region == "us-east-2"
