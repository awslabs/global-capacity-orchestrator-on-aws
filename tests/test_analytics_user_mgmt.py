"""Focused unit tests for ``cli/analytics_user_mgmt.py``.

The happy-path flows are already covered through the Click-level tests in
``tests/test_analytics_cmd.py`` — this module adds targeted tests for the
pure helpers (SRP math, timestamp formatting, CloudFormation output lookups)
and for the error branches of the AWS-facing functions so the module keeps
pace with the >=90% coverage target.
"""

from __future__ import annotations

import base64
import datetime as _dt
import json
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

from cli import analytics_user_mgmt as aum

# ---------------------------------------------------------------------------
# CloudFormation output discovery
# ---------------------------------------------------------------------------


class TestDiscoverFunctions:
    """Covers the three ``discover_*`` helpers."""

    @mock_aws
    def test_discover_returns_none_when_stack_absent(self):
        # No stack has been created in the mock account.
        assert aum.discover_cognito_pool_id("us-east-2") is None
        assert aum.discover_cognito_client_id("us-east-2") is None
        assert aum.discover_api_endpoint("us-east-2") is None

    @mock_aws
    def test_discover_returns_output_values_when_present(self):
        cfn = boto3.client("cloudformation", region_name="us-east-2")
        template = json.dumps(
            {
                "AWSTemplateFormatVersion": "2010-09-09",
                "Resources": {"Topic": {"Type": "AWS::SNS::Topic"}},
                "Outputs": {
                    "CognitoUserPoolId": {"Value": "pool-123"},
                    "CognitoUserPoolClientId": {"Value": "client-456"},
                },
            }
        )
        cfn.create_stack(StackName="gco-analytics", TemplateBody=template)

        assert aum.discover_cognito_pool_id("us-east-2") == "pool-123"
        assert aum.discover_cognito_client_id("us-east-2") == "client-456"

    @mock_aws
    def test_discover_returns_none_when_output_key_absent(self):
        """The stack exists but the output name we want isn't there."""
        cfn = boto3.client("cloudformation", region_name="us-east-2")
        template = json.dumps(
            {
                "AWSTemplateFormatVersion": "2010-09-09",
                "Resources": {"Topic": {"Type": "AWS::SNS::Topic"}},
                "Outputs": {"SomeOther": {"Value": "x"}},
            }
        )
        cfn.create_stack(StackName="gco-analytics", TemplateBody=template)
        assert aum.discover_cognito_pool_id("us-east-2") is None

    @mock_aws
    def test_discover_api_endpoint_returns_value(self):
        cfn = boto3.client("cloudformation", region_name="us-east-2")
        template = json.dumps(
            {
                "AWSTemplateFormatVersion": "2010-09-09",
                "Resources": {"Topic": {"Type": "AWS::SNS::Topic"}},
                "Outputs": {"ApiEndpoint": {"Value": "https://api.example/prod"}},
            }
        )
        cfn.create_stack(StackName="gco-api-gateway", TemplateBody=template)
        assert aum.discover_api_endpoint("us-east-2") == "https://api.example/prod"

    def test_describe_stack_outputs_handles_boto_error(self):
        """The helper swallows ClientError/BotoCoreError and returns None."""
        from botocore.exceptions import BotoCoreError

        class _Boom(BotoCoreError):
            fmt = "boom"

        fake_client = MagicMock()
        fake_client.describe_stacks.side_effect = _Boom()
        with patch("boto3.client", return_value=fake_client):
            assert aum._describe_stack_outputs("us-east-2", "gco-analytics") is None

    def test_describe_stack_outputs_returns_empty_list_when_outputs_missing(self):
        """Stack present but no Outputs key at all."""
        fake_client = MagicMock()
        fake_client.describe_stacks.return_value = {
            "Stacks": [{"StackName": "gco-analytics"}]  # no Outputs
        }
        with patch("boto3.client", return_value=fake_client):
            outputs = aum._describe_stack_outputs("us-east-2", "gco-analytics")
            assert outputs == []

    def test_find_output_returns_none_when_value_not_a_string(self):
        """A nonstring OutputValue is treated as absent."""
        outputs = [{"OutputKey": "Foo", "OutputValue": None}]
        assert aum._find_output(outputs, "Foo") is None


# ---------------------------------------------------------------------------
# Pure SRP math helpers
# ---------------------------------------------------------------------------


class TestSRPHelpers:
    """SRP protocol constants and math helpers — no AWS calls."""

    def test_pad_hex_prepends_zero_when_sign_bit_set(self):
        # "ff" has high nibble 'f', so the helper prepends "00".
        assert aum._pad_hex("ff") == "00ff"

    def test_pad_hex_prepends_zero_for_odd_length(self):
        assert aum._pad_hex("abc") == "0abc"
        # "7" is odd and high nibble <= 7, so just gets zero-prefix for length.
        assert aum._pad_hex("7") == "07"

    def test_pad_hex_accepts_int(self):
        assert aum._pad_hex(0xF0) == "00f0"
        assert aum._pad_hex(0x70) == "70"

    def test_int_to_bytes_pads_odd_nibble_length(self):
        # 0x1 -> "1" (odd length) -> "01"
        assert aum._int_to_bytes(1) == b"\x01"
        assert aum._int_to_bytes(0x10) == b"\x10"

    def test_hash_sha256_returns_hex_string(self):
        h = aum._hash_sha256(b"abc")
        assert isinstance(h, str)
        assert len(h) == 64  # SHA-256 hex digest

    def test_hkdf_returns_expected_length(self):
        key = aum._hkdf(b"secret", b"salt")
        assert len(key) == 16
        # Deterministic for a given IKM/salt.
        assert aum._hkdf(b"secret", b"salt") == key

    def test_cognito_timestamp_strips_leading_zero_on_day(self):
        # February 7, 2024, 01:02:03 UTC — day-of-month is "07" and should
        # become "7" in the final string.
        fake = _dt.datetime(2024, 2, 7, 1, 2, 3, tzinfo=_dt.UTC)
        stamp = aum._cognito_timestamp(now=fake)
        # Expect: "Wed Feb 7 01:02:03 UTC 2024"
        assert "Feb 7" in stamp
        assert "UTC" in stamp
        assert stamp.endswith("2024")

    def test_cognito_timestamp_defaults_to_now(self):
        # No args — just assert the format shape.
        stamp = aum._cognito_timestamp()
        parts = stamp.split(" ")
        assert len(parts) == 6  # Day, Mon, D, HH:MM:SS, UTC, YYYY
        assert parts[4] == "UTC"

    def test_calculate_a_is_g_pow_a_mod_n(self):
        assert aum._calculate_a(5, 23, 2) == pow(2, 5, 23)

    def test_calculate_u_is_deterministic(self):
        a, b = 0x1234ABCD, 0xCAFEBABE
        assert aum._calculate_u(a, b) == aum._calculate_u(a, b)

    def test_calculate_x_matches_manual_hash(self):
        import hashlib

        salt = "0123"
        pool_user = "poolidabc"
        pwd = "pw"
        pwhash = hashlib.sha256(f"{pool_user}:{pwd}".encode()).hexdigest()
        combined = bytes.fromhex(aum._pad_hex(salt) + pwhash)
        expected = int(hashlib.sha256(combined).hexdigest(), 16)
        assert aum._calculate_x(salt, pool_user, pwd) == expected

    def test_build_password_claim_signature_is_deterministic(self):
        secret_block = base64.b64encode(b"xyz").decode()
        timestamp = "Wed Feb 7 01:02:03 UTC 2024"
        sig1 = aum._build_password_claim_signature(
            pool_id="us-east-2_abc",
            username="alice",
            hkdf_key=b"\x00" * 16,
            secret_block_b64=secret_block,
            timestamp=timestamp,
        )
        sig2 = aum._build_password_claim_signature(
            pool_id="us-east-2_abc",
            username="alice",
            hkdf_key=b"\x00" * 16,
            secret_block_b64=secret_block,
            timestamp=timestamp,
        )
        assert sig1 == sig2
        # Decoded signature is HMAC-SHA256 => 32 bytes
        assert len(base64.b64decode(sig1)) == 32

    def test_build_password_claim_signature_handles_pool_id_without_underscore(self):
        # Coverage: the branch that uses pool_id as-is when there's no underscore.
        secret_block = base64.b64encode(b"xyz").decode()
        sig = aum._build_password_claim_signature(
            pool_id="nounderscore",
            username="alice",
            hkdf_key=b"\x00" * 16,
            secret_block_b64=secret_block,
            timestamp="Wed Feb 7 01:02:03 UTC 2024",
        )
        assert len(base64.b64decode(sig)) == 32


# ---------------------------------------------------------------------------
# srp_authenticate end-to-end with stubbed Cognito
# ---------------------------------------------------------------------------


class TestSRPAuthenticate:
    """Exercise the orchestration logic of srp_authenticate.

    We mock the cognito-idp client so the test is fully offline — the
    math itself is covered by TestSRPHelpers.
    """

    def _build_fake_cognito(self, *, srp_b_nonzero: bool = True):
        """Construct a MagicMock cognito-idp client with plausible responses."""
        # Use N-1 as SRP_B so b_int % n != 0 when srp_b_nonzero is True; use
        # 0 otherwise (triggers the "SRP_B is zero" guard).
        n_int = aum._hex_to_int(aum._SRP_N_HEX)
        srp_b = format(n_int - 1 if srp_b_nonzero else 0, "x")
        fake = MagicMock()
        fake.initiate_auth.return_value = {
            "ChallengeParameters": {
                "SALT": "abcd",
                "SRP_B": srp_b,
                "SECRET_BLOCK": base64.b64encode(b"secret-block").decode(),
                "USER_ID_FOR_SRP": "alice",
            }
        }
        fake.respond_to_auth_challenge.return_value = {
            "AuthenticationResult": {
                "IdToken": "id-token",
                "AccessToken": "access-token",
                "RefreshToken": "refresh-token",
            }
        }
        return fake

    def test_srp_authenticate_returns_tokens_on_success(self):
        fake = self._build_fake_cognito()
        with patch("boto3.client", return_value=fake):
            tokens = aum.srp_authenticate(
                pool_id="us-east-2_abc",
                client_id="client-id",
                username="alice",
                password="hunter2",
                region="us-east-2",
            )
        assert tokens == {
            "IdToken": "id-token",
            "AccessToken": "access-token",
            "RefreshToken": "refresh-token",
        }
        fake.initiate_auth.assert_called_once()
        fake.respond_to_auth_challenge.assert_called_once()

    def test_srp_authenticate_rejects_zero_srp_b(self):
        fake = self._build_fake_cognito(srp_b_nonzero=False)
        with (
            patch("boto3.client", return_value=fake),
            pytest.raises(ValueError, match="SRP_B is zero"),
        ):
            aum.srp_authenticate(
                pool_id="us-east-2_abc",
                client_id="client-id",
                username="alice",
                password="hunter2",
                region="us-east-2",
            )

    def test_srp_authenticate_returns_empty_strings_when_authresult_missing(self):
        fake = self._build_fake_cognito()
        fake.respond_to_auth_challenge.return_value = {}
        with patch("boto3.client", return_value=fake):
            tokens = aum.srp_authenticate(
                pool_id="us-east-2_abc",
                client_id="client-id",
                username="alice",
                password="hunter2",
                region="us-east-2",
            )
        assert tokens == {"IdToken": "", "AccessToken": "", "RefreshToken": ""}


# ---------------------------------------------------------------------------
# admin_create_user / list_users / admin_delete_user
# ---------------------------------------------------------------------------


class TestUserMgmtHelpers:
    @mock_aws
    def test_admin_create_user_with_email_and_suppress(self):
        cognito = boto3.client("cognito-idp", region_name="us-east-2")
        pool = cognito.create_user_pool(PoolName="gco-studio")
        pool_id = pool["UserPool"]["Id"]

        resp, temp_pw = aum.admin_create_user(
            pool_id=pool_id,
            region="us-east-2",
            username="alice",
            email="alice@example.com",
            suppress_email=True,
        )
        assert resp.get("User", {}).get("Username") == "alice"
        # moto does not surface a temporary password — the helper returns None.
        assert temp_pw is None or isinstance(temp_pw, str)

    def test_admin_create_user_extracts_temp_pw_from_attributes(self):
        """Covers the TemporaryPassword-in-attributes branch (rare)."""
        fake = MagicMock()
        fake.admin_create_user.return_value = {
            "User": {
                "Username": "alice",
                "Attributes": [{"Name": "temporary_password", "Value": "Temp!123"}],
            }
        }
        with patch("boto3.client", return_value=fake):
            _, temp_pw = aum.admin_create_user(
                pool_id="pool-id", region="us-east-2", username="alice"
            )
        assert temp_pw == "Temp!123"

    def test_admin_create_user_extracts_temp_pw_from_top_level(self):
        """Covers the ``response.get('TemporaryPassword')`` fallback."""
        fake = MagicMock()
        fake.admin_create_user.return_value = {
            "User": {"Username": "alice", "Attributes": []},
            "TemporaryPassword": "FromTopLevel!1",
        }
        with patch("boto3.client", return_value=fake):
            _, temp_pw = aum.admin_create_user(
                pool_id="pool-id", region="us-east-2", username="alice"
            )
        assert temp_pw == "FromTopLevel!1"

    @mock_aws
    def test_list_users_returns_rows_with_email(self):
        cognito = boto3.client("cognito-idp", region_name="us-east-2")
        pool = cognito.create_user_pool(PoolName="gco-studio")
        pool_id = pool["UserPool"]["Id"]
        cognito.admin_create_user(
            UserPoolId=pool_id,
            Username="alice",
            MessageAction="SUPPRESS",
            UserAttributes=[
                {"Name": "email", "Value": "alice@example.com"},
                {"Name": "email_verified", "Value": "true"},
            ],
        )

        rows = aum.list_users(pool_id, "us-east-2")
        assert len(rows) == 1
        row = rows[0]
        assert row["username"] == "alice"
        assert row["email"] == "alice@example.com"
        assert row["status"]
        assert row["enabled"] in ("True", "False")

    @mock_aws
    def test_admin_delete_user_removes_user(self):
        cognito = boto3.client("cognito-idp", region_name="us-east-2")
        pool = cognito.create_user_pool(PoolName="gco-studio")
        pool_id = pool["UserPool"]["Id"]
        cognito.admin_create_user(UserPoolId=pool_id, Username="alice", MessageAction="SUPPRESS")

        aum.admin_delete_user(pool_id, "us-east-2", "alice")

        remaining = cognito.list_users(UserPoolId=pool_id).get("Users", [])
        assert all(u["Username"] != "alice" for u in remaining)


# ---------------------------------------------------------------------------
# fetch_studio_url (urllib-level)
# ---------------------------------------------------------------------------


class TestFetchStudioUrl:
    def test_success_returns_url_and_correlation_id(self):
        class _Resp:
            status = 200

            def __init__(self, body: bytes) -> None:
                self._body = body
                self.headers = {"x-amzn-RequestId": "req-42"}

            def read(self) -> bytes:
                return self._body

            def __enter__(self):
                return self

            def __exit__(self, *exc) -> None:
                return None

        body = json.dumps({"url": "https://studio.aws/x", "expires_in": 180}).encode()
        with patch("urllib.request.urlopen", return_value=_Resp(body)):
            url, expires, corr = aum.fetch_studio_url("https://api.example/prod", "tok")
        assert url == "https://studio.aws/x"
        assert expires == 180
        assert corr == "req-42"

    def test_non_200_raises_httperror(self):
        class _Resp:
            status = 500

            def __init__(self) -> None:
                self.headers = {"x-amzn-RequestId": "req-X"}

            def read(self) -> bytes:
                return b"boom"

            def __enter__(self):
                return self

            def __exit__(self, *exc) -> None:
                return None

        import urllib.error

        with (
            patch("urllib.request.urlopen", return_value=_Resp()),
            pytest.raises(urllib.error.HTTPError),
        ):
            aum.fetch_studio_url("https://api.example/prod", "tok")

    def test_malformed_json_raises_valueerror(self):
        class _Resp:
            status = 200

            def __init__(self) -> None:
                self.headers = {}

            def read(self) -> bytes:
                return b"not json"

            def __enter__(self):
                return self

            def __exit__(self, *exc) -> None:
                return None

        with (
            patch("urllib.request.urlopen", return_value=_Resp()),
            pytest.raises(ValueError, match="malformed /studio/login response"),
        ):
            aum.fetch_studio_url("https://api.example/prod", "tok")

    def test_missing_url_key_raises_valueerror(self):
        class _Resp:
            status = 200

            def __init__(self) -> None:
                self.headers = {}

            def read(self) -> bytes:
                return b'{"expires_in": 180}'

            def __enter__(self):
                return self

            def __exit__(self, *exc) -> None:
                return None

        with (
            patch("urllib.request.urlopen", return_value=_Resp()),
            pytest.raises(ValueError, match="malformed /studio/login response"),
        ):
            aum.fetch_studio_url("https://api.example/prod", "tok")


# ---------------------------------------------------------------------------
# Doctor helpers
# ---------------------------------------------------------------------------


class TestDoctorHelpers:
    @mock_aws
    def test_check_stack_complete_succeeds_for_healthy_stack(self):
        cfn = boto3.client("cloudformation", region_name="us-east-2")
        cfn.create_stack(
            StackName="gco-global",
            TemplateBody=json.dumps(
                {
                    "AWSTemplateFormatVersion": "2010-09-09",
                    "Resources": {"T": {"Type": "AWS::SNS::Topic"}},
                }
            ),
        )
        ok, remediation = aum.check_stack_complete("us-east-2", "gco-global")
        assert ok is True
        assert remediation == ""

    @mock_aws
    def test_check_stack_complete_fails_for_missing_stack(self):
        ok, remediation = aum.check_stack_complete("us-east-2", "nonexistent")
        assert ok is False
        assert "nonexistent" in remediation or "describe_stacks failed" in remediation

    def test_check_stack_complete_returns_empty_when_no_stacks(self):
        fake = MagicMock()
        fake.describe_stacks.return_value = {"Stacks": []}
        with patch("boto3.client", return_value=fake):
            ok, remediation = aum.check_stack_complete("us-east-2", "any")
        assert ok is False
        assert "not found" in remediation

    def test_check_stack_complete_reports_non_complete_status(self):
        fake = MagicMock()
        fake.describe_stacks.return_value = {
            "Stacks": [{"StackName": "x", "StackStatus": "UPDATE_IN_PROGRESS"}]
        }
        with patch("boto3.client", return_value=fake):
            ok, remediation = aum.check_stack_complete("us-east-2", "x")
        assert ok is False
        assert "UPDATE_IN_PROGRESS" in remediation

    @mock_aws
    def test_check_ssm_parameter_returns_true_for_existing(self):
        ssm = boto3.client("ssm", region_name="us-east-2")
        ssm.put_parameter(Name="/foo/bar", Value="baz", Type="String")
        ok, remediation = aum.check_ssm_parameter("us-east-2", "/foo/bar")
        assert ok is True
        assert remediation == ""

    @mock_aws
    def test_check_ssm_parameter_returns_false_for_missing(self):
        ok, remediation = aum.check_ssm_parameter("us-east-2", "/nope")
        assert ok is False
        assert remediation  # any non-empty error string


# ---------------------------------------------------------------------------
# scan_orphan_analytics_resources
# ---------------------------------------------------------------------------


class TestScanOrphanResources:
    def test_no_orphans_returns_empty_list(self):
        """Clean environment: EFS and Cognito return no matching resources."""
        efs = MagicMock()
        efs.describe_file_systems.return_value = {"FileSystems": []}
        cognito = MagicMock()
        cognito.list_user_pools.return_value = {"UserPools": []}

        def _fake_client(service, region_name=None):
            return {"efs": efs, "cognito-idp": cognito}[service]

        with patch("boto3.client", side_effect=_fake_client):
            remediation = aum.scan_orphan_analytics_resources("us-east-2")
        assert remediation == []

    def test_reports_efs_and_cognito_orphans(self):
        """Tagged resources produce copy-pasteable delete commands."""
        efs = MagicMock()
        efs.describe_file_systems.return_value = {"FileSystems": [{"FileSystemId": "fs-abc"}]}
        efs.list_tags_for_resource.return_value = {
            "Tags": [{"Key": "gco:analytics:managed", "Value": "true"}]
        }

        cognito = MagicMock()
        cognito.list_user_pools.return_value = {"UserPools": [{"Id": "pool-1"}]}
        cognito.describe_user_pool.return_value = {
            "UserPool": {"UserPoolTags": {"gco:analytics:managed": "true"}}
        }

        def _fake_client(service, region_name=None):
            return {"efs": efs, "cognito-idp": cognito}[service]

        with patch("boto3.client", side_effect=_fake_client):
            remediation = aum.scan_orphan_analytics_resources("us-east-2")
        assert any("fs-abc" in cmd for cmd in remediation)
        assert any("pool-1" in cmd for cmd in remediation)

    def test_skips_resources_without_matching_tag(self):
        efs = MagicMock()
        efs.describe_file_systems.return_value = {"FileSystems": [{"FileSystemId": "fs-abc"}]}
        efs.list_tags_for_resource.return_value = {"Tags": [{"Key": "Name", "Value": "irrelevant"}]}

        cognito = MagicMock()
        cognito.list_user_pools.return_value = {"UserPools": [{"Id": "pool-1"}]}
        cognito.describe_user_pool.return_value = {
            "UserPool": {"UserPoolTags": {"Name": "irrelevant"}}
        }

        def _fake_client(service, region_name=None):
            return {"efs": efs, "cognito-idp": cognito}[service]

        with patch("boto3.client", side_effect=_fake_client):
            remediation = aum.scan_orphan_analytics_resources("us-east-2")
        assert remediation == []

    def test_handles_efs_client_error_gracefully(self):
        """EFS failure emits a diagnostic line, does not raise."""
        from botocore.exceptions import ClientError

        efs = MagicMock()
        efs.describe_file_systems.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "no"}},
            "DescribeFileSystems",
        )
        cognito = MagicMock()
        cognito.list_user_pools.return_value = {"UserPools": []}

        def _fake_client(service, region_name=None):
            return {"efs": efs, "cognito-idp": cognito}[service]

        with patch("boto3.client", side_effect=_fake_client):
            remediation = aum.scan_orphan_analytics_resources("us-east-2")
        assert any("EFS orphan scan failed" in line for line in remediation)

    def test_handles_cognito_client_error_gracefully(self):
        from botocore.exceptions import ClientError

        efs = MagicMock()
        efs.describe_file_systems.return_value = {"FileSystems": []}
        cognito = MagicMock()
        cognito.list_user_pools.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "no"}},
            "ListUserPools",
        )

        def _fake_client(service, region_name=None):
            return {"efs": efs, "cognito-idp": cognito}[service]

        with patch("boto3.client", side_effect=_fake_client):
            remediation = aum.scan_orphan_analytics_resources("us-east-2")
        assert any("Cognito orphan scan failed" in line for line in remediation)
