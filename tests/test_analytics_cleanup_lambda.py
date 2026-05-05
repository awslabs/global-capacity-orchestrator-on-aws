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
    @patch("analytics_cleanup_handler._delete_access_points", return_value=[])
    @patch("analytics_cleanup_handler._delete_user_profiles", return_value=[])
    @patch("analytics_cleanup_handler._delete_spaces", return_value=[])
    @patch("analytics_cleanup_handler._delete_apps", return_value=[])
    def test_delete_event_calls_cleanup(
        self, mock_apps, mock_spaces, mock_profiles, mock_aps, mock_sgs
    ):
        result = handler({"RequestType": "Delete"}, None)
        assert result["Status"] == "SUCCESS"
        mock_apps.assert_called_once_with("us-east-2", "d-test123")
        mock_spaces.assert_called_once_with("us-east-2", "d-test123")
        mock_profiles.assert_called_once_with("us-east-2", "d-test123")
        mock_aps.assert_called_once_with("us-east-2", "fs-abc123")

    @patch("analytics_cleanup_handler._delete_sagemaker_security_groups", return_value=["err0"])
    @patch("analytics_cleanup_handler._delete_access_points", return_value=["err1"])
    @patch("analytics_cleanup_handler._delete_user_profiles", return_value=["err2"])
    @patch("analytics_cleanup_handler._delete_spaces", return_value=[])
    @patch("analytics_cleanup_handler._delete_apps", return_value=[])
    def test_delete_returns_success_even_on_errors(
        self, mock_apps, mock_spaces, mock_profiles, mock_aps, mock_sgs
    ):
        """Cleanup errors must not block stack deletion."""
        result = handler({"RequestType": "Delete"}, None)
        assert result["Status"] == "SUCCESS"


# ---------------------------------------------------------------------------
# _delete_user_profiles tests
# ---------------------------------------------------------------------------


class TestDeleteUserProfiles:
    def test_deletes_all_profiles(self):
        mock_sm = MagicMock()
        mock_paginator = MagicMock()
        mock_paginator.paginate.return_value = [
            {
                "UserProfiles": [
                    {"UserProfileName": "alice"},
                    {"UserProfileName": "bob"},
                ]
            }
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

    def test_delete_failure_is_captured(self):
        from botocore.exceptions import ClientError

        mock_sm = MagicMock()
        mock_paginator = MagicMock()
        mock_paginator.paginate.return_value = [{"UserProfiles": [{"UserProfileName": "alice"}]}]
        mock_sm.get_paginator.return_value = mock_paginator
        mock_sm.delete_user_profile.side_effect = ClientError(
            {"Error": {"Code": "ValidationException", "Message": "in use"}},
            "DeleteUserProfile",
        )

        with patch("boto3.client", return_value=mock_sm):
            errors = _delete_user_profiles("us-east-2", "d-test123")

        assert len(errors) == 1
        assert "alice" in errors[0]

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
