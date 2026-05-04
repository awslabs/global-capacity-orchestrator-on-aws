"""
User management helpers for the GCO analytics environment.

This module holds the pieces of the ``gco analytics`` CLI that are worth
exercising in isolation from Click:

* :func:`discover_cognito_pool_id` / :func:`discover_cognito_client_id`
  / :func:`discover_api_endpoint` — single-stack CloudFormation output
  lookups used by every sub-command to avoid forcing operators to hand a
  pool id / api url on the command line.
* :func:`srp_authenticate` — a minimal, stdlib-only implementation of
  the Amazon Cognito SRP (Secure Remote Password) flow used by
  ``gco analytics studio login``.

The SRP helper is intentionally kept small (~150 lines including the
static protocol constants) so we don't pull in a third-party
``pycognito`` / ``warrant`` dependency just for one call site. The math
follows the AWS-documented Cognito SRP recipe: ``A = g^a mod N``;
``k = H(N | g)``; ``u = H(A | B)``; ``x = H(salt | H(poolId.split('_')[1] | username | ':' | password))``;
``S = (B - k * g^x) ^ (a + u*x) mod N``; ``hkdf = HKDF(S, u, "Caldera Derived Key", 16)``;
the password claim signature is then ``HMAC-SHA256(hkdf, poolId.split('_')[1] | username | secret_block | timestamp)``.
"""

from __future__ import annotations

import base64
import datetime as _dt
import hashlib
import hmac
import logging
import secrets
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CloudFormation output discovery
# ---------------------------------------------------------------------------


def _describe_stack_outputs(region: str, stack_name: str) -> list[dict[str, str]] | None:
    """Return the ``Outputs`` list for ``stack_name`` in ``region``.

    Returns ``None`` if the stack does not exist or the call fails.
    Any non-transient error surfaces as ``None`` — callers raise the
    user-facing error message themselves so the error copy can mention
    ``gco analytics enable`` / ``gco stacks deploy gco-analytics``.
    """
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError

    try:
        cfn = boto3.client("cloudformation", region_name=region)
        response = cfn.describe_stacks(StackName=stack_name)
    except (ClientError, BotoCoreError) as exc:
        logger.debug("describe_stacks(%s) in %s failed: %s", stack_name, region, exc)
        return None

    stacks = response.get("Stacks", [])
    if not stacks:
        return None
    outputs = stacks[0].get("Outputs", [])
    return list(outputs) if isinstance(outputs, list) else []


def _find_output(outputs: list[dict[str, str]], key: str) -> str | None:
    """Return the ``OutputValue`` for ``key`` in a CloudFormation outputs list."""
    for output in outputs:
        if output.get("OutputKey") == key:
            value = output.get("OutputValue")
            return value if isinstance(value, str) else None
    return None


def discover_cognito_pool_id(region: str, project_name: str = "gco") -> str | None:
    """Return the Cognito user pool id published by ``gco-analytics``.

    Returns ``None`` when the ``gco-analytics`` stack does not exist or
    when the stack exists but the ``CognitoUserPoolId`` output is
    missing. The CLI callers translate ``None`` into the documented
    "gco-analytics stack not deployed" error message.
    """
    stack_name = f"{project_name}-analytics"
    outputs = _describe_stack_outputs(region, stack_name)
    if outputs is None:
        return None
    return _find_output(outputs, "CognitoUserPoolId")


def discover_cognito_client_id(region: str, project_name: str = "gco") -> str | None:
    """Return the Cognito SRP client id published by ``gco-analytics``.

    Looked up on the same stack as :func:`discover_cognito_pool_id`.
    Returns ``None`` when the stack or output is missing.
    """
    stack_name = f"{project_name}-analytics"
    outputs = _describe_stack_outputs(region, stack_name)
    if outputs is None:
        return None
    return _find_output(outputs, "CognitoUserPoolClientId")


def discover_api_endpoint(region: str, project_name: str = "gco") -> str | None:
    """Return the API Gateway base URL published by ``gco-api-gateway``.

    The returned value is the ``ApiEndpoint`` CloudFormation output,
    typically of the form ``https://<id>.execute-api.<region>.amazonaws.com/prod/``.
    Returns ``None`` when the stack or output is missing.
    """
    stack_name = f"{project_name}-api-gateway"
    outputs = _describe_stack_outputs(region, stack_name)
    if outputs is None:
        return None
    return _find_output(outputs, "ApiEndpoint")


# ---------------------------------------------------------------------------
# SRP authentication
# ---------------------------------------------------------------------------

# The RFC 5054 / Cognito SRP group parameters (N, g) as published by AWS.
# N is the 3072-bit safe prime from RFC 5054 appendix A; g is 2.
_SRP_N_HEX = (
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74"
    "020BBEA63B139B22514A08798E3404DDEF9519B3CD3A431B302B0A6DF25F1437"
    "4FE1356D6D51C245E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7ED"
    "EE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3DC2007CB8A163BF05"
    "98DA48361C55D39A69163FA8FD24CF5F83655D23DCA3AD961C62F356208552BB"
    "9ED529077096966D670C354E4ABC9804F1746C08CA18217C32905E462E36CE3B"
    "E39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9DE2BCBF695581718"
    "3995497CEA956AE515D2261898FA051015728E5A8AAAC42DAD33170D04507A33"
    "A85521ABDF1CBA64ECFB850458DBEF0A8AEA71575D060C7DB3970F85A6E1E4C7"
    "ABF5AE8CDB0933D71E8C94E04A25619DCEE3D2261AD2EE6BF12FFA06D98A0864"
    "D87602733EC86A64521F2B18177B200CBBE117577A615D6C770988C0BAD946E2"
    "08E24FA074E5AB3143DB5BFCE0FD108E4B82D120A93AD2CAFFFFFFFFFFFFFFFF"
)
_SRP_G_HEX = "2"
_SRP_INFO_BITS = b"Caldera Derived Key"


def _hex_to_int(value: str) -> int:
    return int(value, 16)


def _int_to_bytes(value: int) -> bytes:
    """Convert an integer to a big-endian bytes value padded to even length."""
    hex_value = format(value, "x")
    if len(hex_value) % 2 == 1:
        hex_value = "0" + hex_value
    return bytes.fromhex(hex_value)


def _pad_hex(value: int | str) -> str:
    """Pad a hex string so that its first nibble is not 8..f (avoids sign bit)."""
    hex_value = value if isinstance(value, str) else format(value, "x")
    if len(hex_value) % 2 == 1:
        hex_value = "0" + hex_value
    elif hex_value[0] in "89abcdefABCDEF":
        hex_value = "00" + hex_value
    return hex_value


def _hash_sha256(data: bytes) -> str:
    """SHA-256 of ``data``, returned as lowercase hex.

    **Not** a password hash — this is the SRP message-digest primitive
    required by RFC 5054 / the AWS Cognito SRP authentication spec.
    Callers compose SRP message structures
    (``H(g | N)``, ``H(A | B)``, ``H(salt | H(username : password))``)
    and feed them through here; using PBKDF2 / bcrypt would break SRP
    verification server-side.

    Password storage is handled entirely by Cognito — the protocol
    holds the password-equivalent verifier ``v = g^x mod N`` on the
    server, never the password itself, and SRP guarantees the password
    never leaves this process in any form.

    CodeQL's ``py/weak-sensitive-data-hashing`` rule flags this call
    because the input is reached from a parameter named ``password``.
    The rule is suppressed in ``.github/codeql/codeql-config.yml`` for
    this file with the SRP rationale documented there; see RFC 5054 §2.6
    for the protocol-level justification.
    """
    return hashlib.sha256(data).hexdigest()


def _hkdf(ikm: bytes, salt: bytes, length: int = 16) -> bytes:
    """HKDF-SHA256 (RFC 5869) with a single 1-byte counter block.

    Cognito uses a 16-byte output so a single expand step suffices.
    """
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    info = _SRP_INFO_BITS + b"\x01"
    return hmac.new(prk, info, hashlib.sha256).digest()[:length]


def _calculate_u(big_a: int, big_b: int) -> int:
    u_hex = _hash_sha256(bytes.fromhex(_pad_hex(big_a) + _pad_hex(big_b)))
    return _hex_to_int(u_hex)


def _calculate_x(salt_hex: str, pool_user_id: str, password: str) -> int:
    password_hash = _hash_sha256(f"{pool_user_id}:{password}".encode())
    combined = bytes.fromhex(_pad_hex(salt_hex) + password_hash)
    return _hex_to_int(_hash_sha256(combined))


def _calculate_a(a_priv: int, n_int: int, g_int: int) -> int:
    return pow(g_int, a_priv, n_int)


def _calculate_s(
    b_int: int, k_int: int, g_int: int, x_int: int, a_priv: int, u_int: int, n_int: int
) -> int:
    # S = (B - k * g^x) ^ (a + u*x) mod N
    intermediate = (b_int - k_int * pow(g_int, x_int, n_int)) % n_int
    return pow(intermediate, a_priv + u_int * x_int, n_int)


def _derive_hkdf_key(u_int: int, s_int: int) -> bytes:
    return _hkdf(_int_to_bytes(s_int), _int_to_bytes(u_int))


def _cognito_timestamp(now: _dt.datetime | None = None) -> str:
    """Return the Cognito SRP timestamp format.

    Cognito requires ``EEE MMM d HH:mm:ss z yyyy`` in the ``UTC`` zone,
    with the day-of-month **not** zero-padded. ``strftime`` pads the
    day, so we strip the zero ourselves.
    """
    now = now or _dt.datetime.now(tz=_dt.UTC)
    stamp = now.strftime("%a %b %d %H:%M:%S UTC %Y")
    # Replace "Feb 07" with "Feb 7" — strftime pads %d unconditionally.
    parts = stamp.split(" ")
    if len(parts) >= 3 and parts[2].startswith("0"):
        parts[2] = parts[2][1:]
    return " ".join(parts)


def _build_password_claim_signature(
    pool_id: str,
    username: str,
    hkdf_key: bytes,
    secret_block_b64: str,
    timestamp: str,
) -> str:
    pool_short = pool_id.split("_", 1)[1] if "_" in pool_id else pool_id
    secret_block = base64.b64decode(secret_block_b64)
    message = pool_short.encode() + username.encode() + secret_block + timestamp.encode()
    signature = hmac.new(hkdf_key, message, hashlib.sha256).digest()
    return base64.b64encode(signature).decode()


def srp_authenticate(
    pool_id: str,
    client_id: str,
    username: str,
    password: str,
    region: str,
) -> dict[str, str]:
    """Authenticate a Cognito user via the USER_SRP_AUTH flow.

    Returns a dict with ``IdToken``, ``AccessToken``, and
    ``RefreshToken`` on success. Raises ``botocore.exceptions.ClientError``
    for Cognito-side failures (``NotAuthorizedException``,
    ``UserNotFoundException``, etc.) which CLI callers surface verbatim
    so the operator sees the Cognito error code.

    The algorithm is the Cognito SRP recipe documented by AWS; the
    protocol constants live in :data:`_SRP_N_HEX` / :data:`_SRP_G_HEX`.
    """
    import boto3

    n_int = _hex_to_int(_SRP_N_HEX)
    g_int = _hex_to_int(_SRP_G_HEX)
    k_int = _hex_to_int(_hash_sha256(bytes.fromhex(_pad_hex(_SRP_N_HEX) + _pad_hex(_SRP_G_HEX))))

    # Generate a client-side private value 'a' in [1, N-1). 128 random
    # bytes gives ~1024-bit entropy, plenty for a 3072-bit group.
    a_priv = int.from_bytes(secrets.token_bytes(128), "big") % (n_int - 1) or 1
    big_a = _calculate_a(a_priv, n_int, g_int)

    cognito = boto3.client("cognito-idp", region_name=region)
    init = cognito.initiate_auth(
        ClientId=client_id,
        AuthFlow="USER_SRP_AUTH",
        AuthParameters={
            "USERNAME": username,
            "SRP_A": format(big_a, "x"),
        },
    )

    challenge_params = init.get("ChallengeParameters", {})
    salt_hex = challenge_params["SALT"]
    srp_b_hex = challenge_params["SRP_B"]
    secret_block = challenge_params["SECRET_BLOCK"]
    # USER_ID_FOR_SRP is the Cognito-internal sub-attribute; callers use
    # it as the username component inside the x / signature hashes.
    user_id_for_srp = challenge_params.get("USER_ID_FOR_SRP", username)

    b_int = _hex_to_int(srp_b_hex)
    if b_int % n_int == 0:
        raise ValueError("SRP_B is zero mod N — invalid server response")

    u_int = _calculate_u(big_a, b_int)
    if u_int == 0:
        raise ValueError("SRP u value is zero — invalid server response")

    pool_short = pool_id.split("_", 1)[1] if "_" in pool_id else pool_id
    x_int = _calculate_x(salt_hex, f"{pool_short}{user_id_for_srp}", password)
    s_int = _calculate_s(b_int, k_int, g_int, x_int, a_priv, u_int, n_int)
    hkdf_key = _derive_hkdf_key(u_int, s_int)

    timestamp = _cognito_timestamp()
    signature = _build_password_claim_signature(
        pool_id=pool_id,
        username=user_id_for_srp,
        hkdf_key=hkdf_key,
        secret_block_b64=secret_block,
        timestamp=timestamp,
    )

    response = cognito.respond_to_auth_challenge(
        ClientId=client_id,
        ChallengeName="PASSWORD_VERIFIER",
        ChallengeResponses={
            "USERNAME": user_id_for_srp,
            "PASSWORD_CLAIM_SECRET_BLOCK": secret_block,
            "PASSWORD_CLAIM_SIGNATURE": signature,
            "TIMESTAMP": timestamp,
        },
    )
    tokens = response.get("AuthenticationResult") or {}
    return {
        "IdToken": str(tokens.get("IdToken", "")),
        "AccessToken": str(tokens.get("AccessToken", "")),
        "RefreshToken": str(tokens.get("RefreshToken", "")),
    }


__all__ = [
    "admin_create_user",
    "admin_delete_user",
    "check_ssm_parameter",
    "check_stack_complete",
    "discover_api_endpoint",
    "discover_cognito_client_id",
    "discover_cognito_pool_id",
    "fetch_studio_url",
    "list_users",
    "scan_orphan_analytics_resources",
    "srp_authenticate",
]


# ---------------------------------------------------------------------------
# Cognito user management helpers
# ---------------------------------------------------------------------------


def admin_create_user(
    pool_id: str,
    region: str,
    username: str,
    email: str | None = None,
    suppress_email: bool = False,
) -> tuple[dict[str, Any], str | None]:
    """Create a Cognito user via AdminCreateUser.

    Returns ``(response, temporary_password)``. The temporary password
    is only set when Cognito echoes it in the response (it does this
    on some versions of the API when ``MessageAction=SUPPRESS``); when
    absent the caller should direct the operator to
    ``admin-set-user-password`` out-of-band.
    """
    import boto3

    user_attributes: list[dict[str, str]] = []
    if email:
        user_attributes.append({"Name": "email", "Value": email})
        user_attributes.append({"Name": "email_verified", "Value": "true"})

    kwargs: dict[str, Any] = {
        "UserPoolId": pool_id,
        "Username": username,
        "UserAttributes": user_attributes,
    }
    if suppress_email:
        kwargs["MessageAction"] = "SUPPRESS"

    cognito = boto3.client("cognito-idp", region_name=region)
    response = cognito.admin_create_user(**kwargs)

    temporary_password: str | None = None
    user = response.get("User", {})
    for attr in user.get("Attributes", []) or []:
        if attr.get("Name") == "temporary_password":
            temporary_password = attr.get("Value")
            break
    if temporary_password is None:
        temporary_password = response.get("TemporaryPassword")

    return response, temporary_password


def list_users(pool_id: str, region: str) -> list[dict[str, str]]:
    """Return a flat row-per-user list suitable for tabular output."""
    import boto3

    cognito = boto3.client("cognito-idp", region_name=region)
    response = cognito.list_users(UserPoolId=pool_id)

    rows: list[dict[str, str]] = []
    for user in response.get("Users", []) or []:
        row: dict[str, str] = {
            "username": user.get("Username", ""),
            "status": user.get("UserStatus", ""),
            "enabled": str(user.get("Enabled", "")),
        }
        for attr in user.get("Attributes", []) or []:
            if attr.get("Name") == "email":
                row["email"] = attr.get("Value", "")
        rows.append(row)
    return rows


def admin_delete_user(pool_id: str, region: str, username: str) -> None:
    """Delete a Cognito user via AdminDeleteUser."""
    import boto3

    cognito = boto3.client("cognito-idp", region_name=region)
    cognito.admin_delete_user(UserPoolId=pool_id, Username=username)


# ---------------------------------------------------------------------------
# HTTP helper for /studio/login
# ---------------------------------------------------------------------------


def fetch_studio_url(api_base: str, id_token: str) -> tuple[str, int, str]:
    """GET ``{api_base}/studio/login`` with the Cognito ID token.

    Returns ``(url, expires_in, correlation_id)`` on success. Raises
    :class:`urllib.error.HTTPError` / :class:`urllib.error.URLError`
    on transport or HTTP failure; raises ``ValueError`` on malformed
    response bodies (unexpected JSON shape / missing ``url`` key), or
    on non-``https://`` ``api_base`` values (guards urllib's
    ``file://`` / ``ftp://`` scheme support).
    """
    import email.message
    import json as _json
    import urllib.error
    import urllib.parse
    import urllib.request

    # Scheme allow-list — urllib happily dereferences ``file://`` and
    # ``ftp://`` URLs, which is the shape of the semgrep
    # ``dynamic-urllib-use-detected`` finding. We only ever call this with
    # the API Gateway endpoint (HTTPS by construction), so reject anything
    # else before the urlopen call.
    parsed = urllib.parse.urlparse(api_base)
    if parsed.scheme != "https":
        raise ValueError(
            f"api_base must use https:// scheme (got {parsed.scheme!r}). "
            "This guard rejects file:// / ftp:// schemes that urllib would "
            "otherwise follow."
        )
    if not parsed.netloc:
        raise ValueError(f"api_base is missing a hostname: {api_base!r}")

    login_url = api_base.rstrip("/") + "/studio/login"
    # Justification for the ``dynamic-urllib-use-detected`` / ``B310``
    # suppressions below: ``login_url`` is built from ``api_base`` + a
    # static ``/studio/login`` suffix. The scheme allow-list near the top
    # of this function rejects any ``api_base`` that isn't ``https://``
    # before we reach these lines, which closes the ``file://`` /
    # ``ftp://`` / ``custom`` scheme hole the rules are written to catch.
    # ``# fmt: off`` pins the block so black can't re-wrap the urlopen
    # call — wrapping moves the suppression comments to the wrong line
    # and bandit / semgrep attach findings to the first line of the call.
    # fmt: off
    request = urllib.request.Request(  # nosec B310  # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected  # noqa: S310
        login_url,
        headers={"Authorization": id_token, "Accept": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # nosec B310  # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected  # noqa: S310
        status = int(response.status)
        body = response.read().decode("utf-8")
        correlation_id = response.headers.get("x-amzn-RequestId") or "N/A"
    # fmt: on

    if status != 200:
        # HTTPError requires a Message (email.message.Message) as its
        # headers argument; build an empty one for determinism.
        headers_msg: email.message.Message = email.message.Message()
        headers_msg["x-amzn-RequestId"] = correlation_id
        raise urllib.error.HTTPError(
            login_url,
            status,
            f"Studio login returned HTTP {status}",
            headers_msg,
            None,
        )

    try:
        payload = _json.loads(body)
        url = str(payload["url"])
        expires_in = int(payload.get("expires_in", 0))
    except (ValueError, KeyError) as exc:
        raise ValueError(f"malformed /studio/login response: {exc!r}") from exc

    return url, expires_in, correlation_id


# ---------------------------------------------------------------------------
# Doctor helpers
# ---------------------------------------------------------------------------


def check_stack_complete(region: str, stack_name: str) -> tuple[bool, str]:
    """Return ``(True, "")`` iff ``stack_name`` is in a healthy state.

    Healthy states are ``CREATE_COMPLETE`` / ``UPDATE_COMPLETE`` /
    ``IMPORT_COMPLETE``. Any other status (or missing stack) returns
    ``(False, remediation_hint)``.
    """
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError

    try:
        cfn = boto3.client("cloudformation", region_name=region)
        resp = cfn.describe_stacks(StackName=stack_name)
    except (ClientError, BotoCoreError) as exc:
        return False, f"describe_stacks failed in {region}: {exc!s}"
    stacks = resp.get("Stacks", [])
    if not stacks:
        return False, f"{stack_name} not found in {region}"
    status = stacks[0].get("StackStatus", "")
    if status in ("CREATE_COMPLETE", "UPDATE_COMPLETE", "IMPORT_COMPLETE"):
        return True, ""
    return False, f"{stack_name} in {region} has status {status}"


def check_ssm_parameter(region: str, param_name: str) -> tuple[bool, str]:
    """Return ``(True, "")`` iff the SSM parameter exists in ``region``."""
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError

    try:
        ssm = boto3.client("ssm", region_name=region)
        ssm.get_parameter(Name=param_name)
        return True, ""
    except (ClientError, BotoCoreError) as exc:
        return False, str(exc)


def scan_orphan_analytics_resources(region: str) -> list[str]:
    """Return a list of copy-paste ``aws`` commands for retained resources.

    Scans EFS and Cognito for resources tagged
    ``gco:analytics:managed=true``. An empty list means no orphans
    were found.
    """
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError

    remediation: list[str] = []
    try:
        efs = boto3.client("efs", region_name=region)
        for fs in efs.describe_file_systems().get("FileSystems", []) or []:
            fs_id = fs.get("FileSystemId", "")
            if not fs_id:
                continue
            tag_resp = efs.list_tags_for_resource(ResourceId=fs_id)
            tags = {t.get("Key"): t.get("Value") for t in tag_resp.get("Tags", []) or []}
            if tags.get("gco:analytics:managed") == "true":
                remediation.append(f"aws efs delete-file-system --file-system-id {fs_id}")
    except (ClientError, BotoCoreError) as exc:
        remediation.append(f"(EFS orphan scan failed: {exc!s})")

    try:
        cognito = boto3.client("cognito-idp", region_name=region)
        pools = cognito.list_user_pools(MaxResults=60)
        for pool in pools.get("UserPools", []) or []:
            pool_id = pool.get("Id")
            if not pool_id:
                continue
            describe = cognito.describe_user_pool(UserPoolId=pool_id)
            tags = describe.get("UserPool", {}).get("UserPoolTags", {}) or {}
            if tags.get("gco:analytics:managed") == "true":
                remediation.append(f"aws cognito-idp delete-user-pool --user-pool-id {pool_id}")
    except (ClientError, BotoCoreError) as exc:
        remediation.append(f"(Cognito orphan scan failed: {exc!s})")

    return remediation
