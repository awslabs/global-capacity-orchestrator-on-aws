"""
Extended tests for secret-rotation and alb-header-validator Lambdas.

Fills gaps in test_lambda_handlers.py: finish_secret when the target
version is already AWSCURRENT (no-op), wrong-length token in
testSecret, createSecret idempotency when the version already exists,
plus the ALB validator's handling of empty header lists and malformed
request events. Both fixtures pop/re-import their handler module
under patched boto3 and environment variables so test state stays
isolated.
"""

import json
import time
from unittest.mock import patch

import pytest

from tests._lambda_imports import load_lambda_module


@pytest.fixture
def rotation_module():
    """Import the secret-rotation handler with mocked boto3.

    See ``tests/_lambda_imports.py`` for why this uses
    :func:`load_lambda_module` rather than the
    ``sys.path.insert + import handler`` pattern.
    """
    with patch("boto3.client") as mock_client:
        handler = load_lambda_module("secret-rotation")
        yield handler, mock_client


@pytest.fixture
def alb_validator_module():
    """Import the alb-header-validator handler with mocked boto3 and env.

    See ``tests/_lambda_imports.py`` for the load pattern rationale.
    """
    with (
        patch("boto3.client") as mock_client,
        patch.dict(
            "os.environ",
            {
                "SECRET_ARN": "arn:aws:secretsmanager:us-east-1:123:secret:test",
                "SECRET_CACHE_TTL_SECONDS": "300",
            },
        ),
    ):
        handler = load_lambda_module("alb-header-validator")

        handler._cached_tokens = set()
        handler._cache_timestamp = 0.0

        mock_sm = mock_client.return_value
        mock_sm.exceptions.ResourceNotFoundException = type(
            "ResourceNotFoundException", (Exception,), {}
        )
        yield handler, mock_sm


class TestFinishSecret:
    """Tests for finish_secret function."""

    def test_finish_secret_moves_pending_to_current(self, rotation_module):
        """finish_secret should move AWSPENDING to AWSCURRENT."""
        handler, mock_client = rotation_module
        client = mock_client.return_value

        client.describe_secret.return_value = {
            "VersionIdsToStages": {
                "old-version": ["AWSCURRENT"],
                "new-version": ["AWSPENDING"],
            }
        }

        handler.finish_secret(client, "test-secret", "new-version")

        client.update_secret_version_stage.assert_called_once_with(
            SecretId="test-secret",
            VersionStage="AWSCURRENT",
            MoveToVersionId="new-version",
            RemoveFromVersionId="old-version",
        )

    def test_finish_secret_already_current(self, rotation_module):
        """finish_secret should be a no-op if version is already AWSCURRENT."""
        handler, mock_client = rotation_module
        client = mock_client.return_value

        client.describe_secret.return_value = {
            "VersionIdsToStages": {
                "token-123": ["AWSCURRENT"],
            }
        }

        handler.finish_secret(client, "test-secret", "token-123")

        # Should not call update_secret_version_stage
        client.update_secret_version_stage.assert_not_called()


class TestTestSecretValidation:
    """Tests for test_secret validation."""

    def test_test_secret_wrong_token_length(self, rotation_module):
        """test_secret should fail if token length doesn't match."""
        handler, mock_client = rotation_module
        client = mock_client.return_value

        client.get_secret_value.return_value = {"SecretString": json.dumps({"token": "short"})}

        with pytest.raises(ValueError, match="Token length mismatch"):
            handler.test_secret(client, "test-secret", "token-123")

    def test_test_secret_valid_token(self, rotation_module):
        """test_secret should succeed with valid token."""
        handler, mock_client = rotation_module
        client = mock_client.return_value

        client.get_secret_value.return_value = {"SecretString": json.dumps({"token": "a" * 64})}

        # Should not raise
        handler.test_secret(client, "test-secret", "token-123")

        client.get_secret_value.assert_called_once_with(
            SecretId="test-secret",
            VersionId="token-123",
            VersionStage="AWSPENDING",
        )


class TestCreateSecretIdempotency:
    """Tests for create_secret idempotency."""

    def test_create_secret_skips_if_already_exists(self, rotation_module):
        """create_secret should skip if version already exists as AWSPENDING."""
        handler, mock_client = rotation_module
        client = mock_client.return_value

        # Version already exists — no exception
        client.get_secret_value.return_value = {"SecretString": json.dumps({"token": "existing"})}
        client.exceptions.ResourceNotFoundException = type(
            "ResourceNotFoundException", (Exception,), {}
        )

        handler.create_secret(client, "test-secret", "token-123")

        # Should NOT call put_secret_value since version already exists
        client.put_secret_value.assert_not_called()

    def test_create_secret_generates_correct_length_token(self, rotation_module):
        """create_secret should generate a token of TOKEN_LENGTH."""
        handler, mock_client = rotation_module
        client = mock_client.return_value

        client.exceptions.ResourceNotFoundException = type(
            "ResourceNotFoundException", (Exception,), {}
        )
        client.get_secret_value.side_effect = client.exceptions.ResourceNotFoundException()

        handler.create_secret(client, "test-secret", "token-123")

        call_args = client.put_secret_value.call_args
        secret_value = json.loads(call_args[1]["SecretString"])
        assert len(secret_value["token"]) == handler.TOKEN_LENGTH
        assert call_args[1]["VersionStages"] == ["AWSPENDING"]


class TestAlbValidatorEdgeCases:
    """Tests for ALB header validator edge cases."""

    def _make_event(self, token_value=None):
        headers = {}
        if token_value is not None:
            headers["x-gco-auth-token"] = [{"value": token_value}]
        return {"Records": [{"cf": {"request": {"headers": headers, "uri": "/test"}}}]}

    def test_empty_header_value(self, alb_validator_module):
        """Empty auth header value should return 403."""
        handler, mock_sm = alb_validator_module
        mock_sm.get_secret_value.side_effect = [
            {"SecretString": json.dumps({"token": "valid-token"})},
            mock_sm.exceptions.ResourceNotFoundException(),
        ]

        event = self._make_event("")
        result = handler.lambda_handler(event, None)
        assert result["status"] == "403"

    def test_empty_header_list(self, alb_validator_module):
        """Empty x-gco-auth-token header list should return 403."""
        handler, mock_sm = alb_validator_module
        mock_sm.get_secret_value.side_effect = [
            {"SecretString": json.dumps({"token": "valid-token"})},
            mock_sm.exceptions.ResourceNotFoundException(),
        ]

        event = {
            "Records": [
                {
                    "cf": {
                        "request": {
                            "headers": {"x-gco-auth-token": [{}]},
                            "uri": "/test",
                        }
                    }
                }
            ]
        }
        result = handler.lambda_handler(event, None)
        assert result["status"] == "403"

    def test_cache_ttl_boundary(self, alb_validator_module):
        """Token cache should still be valid at exactly TTL - 1 second."""
        handler, mock_sm = alb_validator_module
        mock_sm.get_secret_value.side_effect = [
            {"SecretString": json.dumps({"token": "valid-token"})},
            mock_sm.exceptions.ResourceNotFoundException(),
        ]

        # Prime the cache
        handler.lambda_handler(self._make_event("valid-token"), None)

        # Set cache to just before expiry (299 seconds ago for 300s TTL)
        handler._cache_timestamp = time.time() - 299

        # Should still use cache (not call SM again)
        mock_sm.get_secret_value.side_effect = Exception("should not be called")
        result = handler.lambda_handler(self._make_event("valid-token"), None)
        assert "status" not in result

    def test_403_response_body_is_json(self, alb_validator_module):
        """403 response body should be valid JSON with error message."""
        handler, mock_sm = alb_validator_module
        mock_sm.get_secret_value.side_effect = [
            {"SecretString": json.dumps({"token": "valid-token"})},
            mock_sm.exceptions.ResourceNotFoundException(),
        ]

        result = handler.lambda_handler(self._make_event("wrong"), None)
        assert result["status"] == "403"
        body = json.loads(result["body"])
        assert "error" in body
