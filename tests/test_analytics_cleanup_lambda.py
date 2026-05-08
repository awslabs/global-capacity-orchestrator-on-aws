"""Tests for the analytics-cleanup Lambda (lambda/analytics-cleanup/handler.py).

Covers:
- Create/Update events are no-ops (return SUCCESS immediately)
- Delete event deletes all user profiles from the domain
- Delete event deletes all EFS access points
- Errors during deletion are logged but don't fail the custom resource
  (always returns SUCCESS so stack destroy isn't blocked)
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Import the handler module from lambda/analytics-cleanup/
# ---------------------------------------------------------------------------

_HANDLER_PATH = (
    Path(__file__).resolve().parent.parent / "lambda" / "analytics-cleanup" / "handler.py"
)
_SPEC = importlib.util.spec_from_file_location("analytics_cleanup_handler", _HANDLER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_module = importlib.util.module_from_spec(_SPEC)
sys.modules.setdefault("analytics_cleanup_handler", _module)
_SPEC.loader.exec_module(_module)

handler = _module.handler
_delete_user_profiles = _module._delete_user_profiles
_delete_access_points = _module._delete_access_points


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_ENV = {
    "DOMAIN_ID": "d-test123",
    "EFS_ID": "fs-abc123",
    "REGION": "us-east-2",
    "VPC_ID": "vpc-test123",
}


@pytest.fixture(autouse=True)
def _set_env(monkeypatch):
    for key, value in _ENV.items():
        monkeypatch.setenv(key, value)


@pytest.fixture(autouse=True)
def _fast_sleep(monkeypatch):
    """Skip real waiting inside the handler so tests stay fast.

    ``_delete_user_profiles`` and ``_delete_spaces`` now poll and sleep
    while the SageMaker delete calls drain asynchronously. Tests use
    paginators that return empty on the first re-list, so the loop exits
    on the first iteration — but only if ``time.sleep`` is a no-op.
    """
    monkeypatch.setattr(_module.time, "sleep", lambda _: None)


# ---------------------------------------------------------------------------
# handler() top-level tests
# ---------------------------------------------------------------------------


class TestHandler:
    def test_create_event_is_noop(self):
        result = handler({"RequestType": "Create"}, None)
        assert result["Status"] == "SUCCESS"

    def test_update_event_is_noop(self):
        result = handler({"RequestType": "Update"}, None)
        assert result["Status"] == "SUCCESS"

    @patch("analytics_cleanup_handler._delete_sagemaker_security_groups", return_value=[])
    @patch("analytics_cleanup_handler._delete_sagemaker_managed_efs", return_value=[])
    @patch("analytics_cleanup_handler._get_sagemaker_home_efs_id", return_value="fs-sm-123")
    @patch("analytics_cleanup_handler._delete_efs_resource_policy")
    @patch("analytics_cleanup_handler._delete_user_profiles", return_value=[])
    @patch("analytics_cleanup_handler._delete_spaces", return_value=[])
    @patch("analytics_cleanup_handler._delete_apps", return_value=[])
    def test_delete_event_calls_cleanup(
        self,
        mock_apps,
        mock_spaces,
        mock_profiles,
        mock_efs_policy,
        mock_get_sm_efs,
        mock_efs,
        mock_sgs,
    ):
        result = handler({"RequestType": "Delete"}, None)
        assert result["Status"] == "SUCCESS"
        mock_apps.assert_called_once_with("us-east-2", "d-test123")
        mock_spaces.assert_called_once_with("us-east-2", "d-test123")
        mock_profiles.assert_called_once_with("us-east-2", "d-test123")
        mock_efs.assert_called_once_with("us-east-2", "d-test123")

    @patch("analytics_cleanup_handler._delete_sagemaker_security_groups", return_value=["err0"])
    @patch("analytics_cleanup_handler._delete_sagemaker_managed_efs", return_value=[])
    @patch("analytics_cleanup_handler._get_sagemaker_home_efs_id", return_value="")
    @patch("analytics_cleanup_handler._delete_efs_resource_policy")
    @patch("analytics_cleanup_handler._delete_user_profiles", return_value=["err2"])
    @patch("analytics_cleanup_handler._delete_spaces", return_value=[])
    @patch("analytics_cleanup_handler._delete_apps", return_value=[])
    def test_delete_raises_on_critical_errors(
        self,
        mock_apps,
        mock_spaces,
        mock_profiles,
        mock_efs_policy,
        mock_get_sm_efs,
        mock_efs,
        mock_sgs,
    ):
        """Errors draining apps/spaces/user-profiles must fail the custom
        resource so CloudFormation doesn't proceed to a guaranteed-fail
        domain delete.
        """
        with pytest.raises(RuntimeError, match="Analytics cleanup failed"):
            handler({"RequestType": "Delete"}, None)

    @patch("analytics_cleanup_handler._delete_sagemaker_security_groups", return_value=["sg-err"])
    @patch("analytics_cleanup_handler._delete_sagemaker_managed_efs", return_value=["efs-err"])
    @patch("analytics_cleanup_handler._get_sagemaker_home_efs_id", return_value="")
    @patch("analytics_cleanup_handler._delete_efs_resource_policy")
    @patch("analytics_cleanup_handler._delete_user_profiles", return_value=[])
    @patch("analytics_cleanup_handler._delete_spaces", return_value=[])
    @patch("analytics_cleanup_handler._delete_apps", return_value=[])
    def test_delete_tolerates_non_critical_errors(
        self,
        mock_apps,
        mock_spaces,
        mock_profiles,
        mock_efs_policy,
        mock_get_sm_efs,
        mock_efs,
        mock_sgs,
    ):
        """EFS and SG cleanup errors are best-effort and must not block the
        domain delete — they're logged but the handler still returns SUCCESS.
        """
        result = handler({"RequestType": "Delete"}, None)
        assert result["Status"] == "SUCCESS"


# ---------------------------------------------------------------------------
# _delete_user_profiles tests
# ---------------------------------------------------------------------------


class TestDeleteUserProfiles:
    def test_deletes_all_profiles(self):
        mock_sm = MagicMock()
        mock_paginator = MagicMock()
        # First paginate() call: enumerate profiles for deletion.
        # Subsequent paginate() calls: poll loop — return empty to exit.
        mock_paginator.paginate.side_effect = [
            [
                {
                    "UserProfiles": [
                        {"UserProfileName": "alice"},
                        {"UserProfileName": "bob"},
                    ]
                }
            ],
            [{"UserProfiles": []}],
        ]
        mock_sm.get_paginator.return_value = mock_paginator

        with patch("boto3.client", return_value=mock_sm):
            errors = _delete_user_profiles("us-east-2", "d-test123")

        assert errors == []
        assert mock_sm.delete_user_profile.call_count == 2
        mock_sm.delete_user_profile.assert_any_call(DomainId="d-test123", UserProfileName="alice")
        mock_sm.delete_user_profile.assert_any_call(DomainId="d-test123", UserProfileName="bob")

    def test_empty_domain_returns_no_errors(self):
        mock_sm = MagicMock()
        mock_paginator = MagicMock()
        mock_paginator.paginate.return_value = [{"UserProfiles": []}]
        mock_sm.get_paginator.return_value = mock_paginator

        with patch("boto3.client", return_value=mock_sm):
            errors = _delete_user_profiles("us-east-2", "d-test123")

        assert errors == []
        mock_sm.delete_user_profile.assert_not_called()

    def test_waits_for_profiles_to_drain(self):
        """Profiles in Deleting state are skipped for the delete call but
        still gate the wait loop — we must not return until they're gone.
        """
        mock_sm = MagicMock()
        mock_paginator = MagicMock()
        # Initial list has 1 profile to delete.
        # Wait loop sees it still Deleting, then gone.
        mock_paginator.paginate.side_effect = [
            [{"UserProfiles": [{"UserProfileName": "alice", "Status": "InService"}]}],
            [{"UserProfiles": [{"UserProfileName": "alice", "Status": "Deleting"}]}],
            [{"UserProfiles": []}],
        ]
        mock_sm.get_paginator.return_value = mock_paginator

        with patch("boto3.client", return_value=mock_sm):
            errors = _delete_user_profiles("us-east-2", "d-test123")

        assert errors == []
        mock_sm.delete_user_profile.assert_called_once_with(
            DomainId="d-test123", UserProfileName="alice"
        )

    def test_timeout_reports_error(self, monkeypatch):
        """If profiles never drain, the function must report an error so
        the top-level handler can raise.
        """
        mock_sm = MagicMock()
        mock_paginator = MagicMock()
        # Always return a lingering profile — the wait loop will time out.
        mock_paginator.paginate.return_value = [
            {"UserProfiles": [{"UserProfileName": "alice", "Status": "Deleting"}]}
        ]
        mock_sm.get_paginator.return_value = mock_paginator

        with patch("boto3.client", return_value=mock_sm):
            errors = _delete_user_profiles("us-east-2", "d-test123")

        assert len(errors) == 1
        assert "Timed out" in errors[0]
        assert "alice" in errors[0]

    def test_delete_failure_is_captured(self):
        from botocore.exceptions import ClientError

        mock_sm = MagicMock()
        mock_paginator = MagicMock()
        mock_paginator.paginate.side_effect = [
            [{"UserProfiles": [{"UserProfileName": "alice"}]}],
            [{"UserProfiles": []}],
        ]
        mock_sm.get_paginator.return_value = mock_paginator
        mock_sm.delete_user_profile.side_effect = ClientError(
            {"Error": {"Code": "ValidationException", "Message": "in use"}},
            "DeleteUserProfile",
        )

        with patch("boto3.client", return_value=mock_sm):
            errors = _delete_user_profiles("us-east-2", "d-test123")

        assert len(errors) >= 1
        assert any("alice" in e for e in errors)

    def test_list_failure_is_captured(self):
        from botocore.exceptions import ClientError

        mock_sm = MagicMock()
        mock_sm.get_paginator.side_effect = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "denied"}},
            "ListUserProfiles",
        )

        with patch("boto3.client", return_value=mock_sm):
            errors = _delete_user_profiles("us-east-2", "d-test123")

        assert len(errors) == 1
        assert "list" in errors[0].lower() or "List" in errors[0]


# ---------------------------------------------------------------------------
# _delete_access_points tests
# ---------------------------------------------------------------------------


class TestDeleteAccessPoints:
    def test_deletes_all_access_points(self):
        mock_efs = MagicMock()
        mock_paginator = MagicMock()
        mock_paginator.paginate.return_value = [
            {
                "AccessPoints": [
                    {"AccessPointId": "fsap-001"},
                    {"AccessPointId": "fsap-002"},
                ]
            }
        ]
        mock_efs.get_paginator.return_value = mock_paginator

        with patch("boto3.client", return_value=mock_efs):
            errors = _delete_access_points("us-east-2", "fs-abc123")

        assert errors == []
        assert mock_efs.delete_access_point.call_count == 2

    def test_empty_filesystem_returns_no_errors(self):
        mock_efs = MagicMock()
        mock_paginator = MagicMock()
        mock_paginator.paginate.return_value = [{"AccessPoints": []}]
        mock_efs.get_paginator.return_value = mock_paginator

        with patch("boto3.client", return_value=mock_efs):
            errors = _delete_access_points("us-east-2", "fs-abc123")

        assert errors == []

    def test_delete_failure_is_captured(self):
        from botocore.exceptions import ClientError

        mock_efs = MagicMock()
        mock_paginator = MagicMock()
        mock_paginator.paginate.return_value = [{"AccessPoints": [{"AccessPointId": "fsap-001"}]}]
        mock_efs.get_paginator.return_value = mock_paginator
        mock_efs.delete_access_point.side_effect = ClientError(
            {"Error": {"Code": "InternalError", "Message": "oops"}},
            "DeleteAccessPoint",
        )

        with patch("boto3.client", return_value=mock_efs):
            errors = _delete_access_points("us-east-2", "fs-abc123")

        assert len(errors) == 1
        assert "fsap-001" in errors[0]


# ---------------------------------------------------------------------------
# _delete_sagemaker_managed_efs tests
# ---------------------------------------------------------------------------

_delete_sagemaker_managed_efs = _module._delete_sagemaker_managed_efs


class TestDeleteSagemakerManagedEfs:
    def test_deletes_efs_matching_domain_id(self):
        mock_sm = MagicMock()
        mock_efs = MagicMock()
        # DescribeDomain returns the HomeEfsFileSystemId.
        mock_sm.describe_domain.return_value = {
            "HomeEfsFileSystemId": "fs-target",
        }
        # After deletion, no mount targets remain
        mock_efs.describe_mount_targets.side_effect = [
            {"MountTargets": [{"MountTargetId": "fsmt-001"}]},
            {"MountTargets": []},
        ]

        def client_factory(service, **kwargs):
            if service == "sagemaker":
                return mock_sm
            return mock_efs

        with patch("boto3.client", side_effect=client_factory):
            errors = _delete_sagemaker_managed_efs("us-east-2", "d-test123")

        assert errors == []
        mock_sm.describe_domain.assert_called_once_with(DomainId="d-test123")
        mock_efs.delete_mount_target.assert_called_once_with(MountTargetId="fsmt-001")
        mock_efs.delete_file_system.assert_called_once_with(FileSystemId="fs-target")

    def test_no_matching_efs_returns_empty(self):
        mock_sm = MagicMock()
        mock_efs = MagicMock()
        mock_sm.describe_domain.return_value = {}

        def client_factory(service, **kwargs):
            if service == "sagemaker":
                return mock_sm
            return mock_efs

        with patch("boto3.client", side_effect=client_factory):
            errors = _delete_sagemaker_managed_efs("us-east-2", "d-test123")

        assert errors == []
        mock_efs.delete_mount_target.assert_not_called()
        mock_efs.delete_file_system.assert_not_called()

    def test_mount_target_delete_failure_captured(self):
        from botocore.exceptions import ClientError

        mock_efs = MagicMock()
        mock_efs.describe_file_systems.return_value = {
            "FileSystems": [
                {"FileSystemId": "fs-target", "CreationToken": "d-test123"},
            ]
        }
        mock_efs.describe_mount_targets.return_value = {
            "MountTargets": [{"MountTargetId": "fsmt-001"}]
        }
        mock_efs.delete_mount_target.side_effect = ClientError(
            {"Error": {"Code": "MountTargetNotFound", "Message": "gone"}},
            "DeleteMountTarget",
        )

        with patch("boto3.client", return_value=mock_efs):
            errors = _delete_sagemaker_managed_efs("us-east-2", "d-test123")

        assert len(errors) == 1
        assert "fsmt-001" in errors[0]
