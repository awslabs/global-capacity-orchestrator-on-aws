"""
Tests for the secret-rotation and alb-header-validator Lambda handlers.

Drives the four-step Secrets Manager rotation state machine
(createSecret/setSecret/testSecret/finishSecret) — including the
createSecret idempotency check, testSecret's assertion that the
payload contains a non-empty `token` field, and rejection of invalid
step names — plus the ALB header validator Lambda. The rotation_module
fixture pops/re-imports the handler under patched boto3 so each test
starts with a clean Secrets Manager client mock.
"""

import json
import sys
from unittest.mock import patch

import pytest

# ============================================================================
# Secret Rotation Lambda
# ============================================================================


@pytest.fixture
def rotation_module():
    """Import the secret-rotation handler with mocked boto3."""
    with patch("boto3.client") as mock_client:
        # Remove cached module if present
        sys.modules.pop("handler", None)
        sys.path.insert(0, "lambda/secret-rotation")
        try:
            import handler

            yield handler, mock_client
        finally:
            sys.path.pop(0)
            sys.modules.pop("handler", None)


class TestSecretRotationHandler:
    def test_dispatches_create_secret(self, rotation_module):
        handler, mock_client = rotation_module
        client = mock_client.return_value
        # Simulate version doesn't exist yet
        client.get_secret_value.side_effect = client.exceptions.ResourceNotFoundException(
            {"Error": {"Code": "ResourceNotFoundException", "Message": ""}}, "GetSecretValue"
        )
        # Mock exceptions class
        client.exceptions.ResourceNotFoundException = type(
            "ResourceNotFoundException", (Exception,), {}
        )
        client.get_secret_value.side_effect = client.exceptions.ResourceNotFoundException()

        event = {
            "SecretId": "arn:aws:secretsmanager:us-east-1:123:secret:test",
            "ClientRequestToken": "token-123",
            "Step": "createSecret",
        }
        handler.lambda_handler(event, None)
        client.put_secret_value.assert_called_once()

    def test_dispatches_set_secret_noop(self, rotation_module):
        handler, mock_client = rotation_module
        event = {
            "SecretId": "arn:aws:secretsmanager:us-east-1:123:secret:test",
            "ClientRequestToken": "token-123",
            "Step": "setSecret",
        }
        handler.lambda_handler(event, None)
        # setSecret is a no-op, no calls expected

    def test_dispatches_test_secret(self, rotation_module):
        handler, mock_client = rotation_module
        client = mock_client.return_value
        client.get_secret_value.return_value = {"SecretString": json.dumps({"token": "a" * 64})}
        event = {
            "SecretId": "arn:aws:secretsmanager:us-east-1:123:secret:test",
            "ClientRequestToken": "token-123",
            "Step": "testSecret",
        }
        handler.lambda_handler(event, None)
        client.get_secret_value.assert_called_once()

    def test_test_secret_fails_on_missing_token(self, rotation_module):
        handler, mock_client = rotation_module
        client = mock_client.return_value
        client.get_secret_value.return_value = {
            "SecretString": json.dumps({"description": "no token field"})
        }
        event = {
            "SecretId": "test-secret",
            "ClientRequestToken": "token-123",
            "Step": "testSecret",
        }
        with pytest.raises(ValueError, match="missing 'token' field"):
            handler.lambda_handler(event, None)

    def test_invalid_step_raises(self, rotation_module):
        handler, _ = rotation_module
        event = {
            "SecretId": "test-secret",
            "ClientRequestToken": "token-123",
            "Step": "invalidStep",
        }
        with pytest.raises(ValueError, match="Invalid rotation step"):
            handler.lambda_handler(event, None)


# ============================================================================
# ALB Header Validator Lambda
# ============================================================================


@pytest.fixture
def alb_validator_module():
    """Import the alb-header-validator handler with mocked boto3 and env."""
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
        sys.modules.pop("handler", None)
        sys.path.insert(0, "lambda/alb-header-validator")
        try:
            import handler

            # Reset module-level cache
            handler._cached_tokens = set()
            handler._cache_timestamp = 0.0

            mock_sm = mock_client.return_value
            mock_sm.get_secret_value.return_value = {
                "SecretString": json.dumps({"token": "valid-secret-token"})
            }
            # AWSPENDING not found by default (no rotation in progress)
            mock_sm.exceptions.ResourceNotFoundException = type(
                "ResourceNotFoundException", (Exception,), {}
            )
            yield handler, mock_sm
        finally:
            sys.path.pop(0)
            sys.modules.pop("handler", None)


class TestAlbHeaderValidator:
    def _make_event(self, token_value=None):
        headers = {}
        if token_value is not None:
            headers["x-gco-auth-token"] = [{"value": token_value}]
        return {"Records": [{"cf": {"request": {"headers": headers, "uri": "/api/v1/health"}}}]}

    def test_valid_token_passes_request(self, alb_validator_module):
        handler, mock_sm = alb_validator_module
        # AWSPENDING raises ResourceNotFoundException (no rotation)
        mock_sm.get_secret_value.side_effect = [
            {"SecretString": json.dumps({"token": "valid-secret-token"})},
            mock_sm.exceptions.ResourceNotFoundException(),
        ]
        event = self._make_event("valid-secret-token")
        result = handler.lambda_handler(event, None)
        # Should return the original request (not a 403)
        assert "status" not in result
        assert result["uri"] == "/api/v1/health"

    def test_invalid_token_returns_403(self, alb_validator_module):
        handler, mock_sm = alb_validator_module
        mock_sm.get_secret_value.side_effect = [
            {"SecretString": json.dumps({"token": "valid-secret-token"})},
            mock_sm.exceptions.ResourceNotFoundException(),
        ]
        event = self._make_event("wrong-token")
        result = handler.lambda_handler(event, None)
        assert result["status"] == "403"

    def test_missing_token_returns_403(self, alb_validator_module):
        handler, mock_sm = alb_validator_module
        mock_sm.get_secret_value.side_effect = [
            {"SecretString": json.dumps({"token": "valid-secret-token"})},
            mock_sm.exceptions.ResourceNotFoundException(),
        ]
        event = self._make_event()
        result = handler.lambda_handler(event, None)
        assert result["status"] == "403"

    def test_caches_tokens_within_ttl(self, alb_validator_module):
        """Tokens are cached — second call doesn't hit Secrets Manager."""
        handler, mock_sm = alb_validator_module
        mock_sm.get_secret_value.side_effect = [
            {"SecretString": json.dumps({"token": "valid-secret-token"})},
            mock_sm.exceptions.ResourceNotFoundException(),
        ]
        event = self._make_event("valid-secret-token")
        handler.lambda_handler(event, None)
        # Reset side_effect so second call would fail if it hit SM
        mock_sm.get_secret_value.side_effect = Exception("should not be called")
        result = handler.lambda_handler(event, None)
        assert "status" not in result  # Still passes from cache

    def test_accepts_pending_token_during_rotation(self, alb_validator_module):
        """During rotation, both AWSCURRENT and AWSPENDING tokens are accepted."""
        handler, mock_sm = alb_validator_module
        mock_sm.get_secret_value.side_effect = [
            {"SecretString": json.dumps({"token": "current-token"})},
            {"SecretString": json.dumps({"token": "pending-token"})},
        ]
        # Both tokens should work
        assert "status" not in handler.lambda_handler(self._make_event("current-token"), None)
        assert "status" not in handler.lambda_handler(self._make_event("pending-token"), None)

    def test_refreshes_after_ttl_expires(self, alb_validator_module):
        """Cache is refreshed after TTL expires."""
        import time

        handler, mock_sm = alb_validator_module
        mock_sm.get_secret_value.side_effect = [
            {"SecretString": json.dumps({"token": "old-token"})},
            mock_sm.exceptions.ResourceNotFoundException(),
        ]
        handler.lambda_handler(self._make_event("old-token"), None)

        # Expire the cache
        handler._cache_timestamp = time.time() - 400

        # New token after rotation
        mock_sm.get_secret_value.side_effect = [
            {"SecretString": json.dumps({"token": "new-token"})},
            mock_sm.exceptions.ResourceNotFoundException(),
        ]
        result = handler.lambda_handler(self._make_event("new-token"), None)
        assert "status" not in result

        # Old token should now be rejected
        result = handler.lambda_handler(self._make_event("old-token"), None)
        assert result["status"] == "403"
