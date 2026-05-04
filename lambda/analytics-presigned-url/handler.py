"""Presigned-URL Lambda for SageMaker Studio (analytics environment).

Exchanges a Cognito-authorized API Gateway event for a time-limited
``sagemaker:CreatePresignedDomainUrl`` link.

Flow (happy path):

1. Extract ``claims = event["requestContext"]["authorizer"]["claims"]`` and
   read ``cognito:username`` (falling back to ``username`` if the token
   shape doesn't namespace Cognito claims).
2. Resolve the Studio ``DomainId`` by calling ``sagemaker:ListDomains`` and
   filtering on ``DomainName`` equality against ``STUDIO_DOMAIN_NAME``.
3. ``sagemaker:DescribeUserProfile`` — if the profile doesn't exist yet
   (``ValidationException`` / ``ResourceNotFound``), create it via
   ``sagemaker:CreateUserProfile`` with a ``CustomFileSystemConfigs``
   entry mounting ``STUDIO_EFS_ID`` at ``/home/<username>``.
4. Ensure the per-user EFS access point exists (lazy creation — not
   eager-from-CLI so the stack doesn't need to know about user profiles
   at deploy time).
5. ``sagemaker:CreatePresignedDomainUrl`` and return the URL.

All failures funnel through the outer ``try/except`` in
:func:`lambda_handler`; the response body is always JSON and never
leaks an exception string.

Environment variables (set by ``GCOAnalyticsStack._create_presigned_url_lambda``):

- ``STUDIO_DOMAIN_NAME`` — the Studio domain's human-readable name.
- ``SAGEMAKER_EXECUTION_ROLE_ARN`` — passed on ``CreateUserProfile``.
- ``STUDIO_EFS_ID`` — used by ``_ensure_access_point``.
- ``URL_EXPIRES_SECONDS`` — default ``300`` (5 minutes).
- ``SESSION_EXPIRES_SECONDS`` — default ``43200`` (12 hours).

The module-level boto3 clients (``sagemaker`` and ``efs``) are created
once at cold start so repeat invocations inside a warm container reuse
the same HTTP connection pools.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any

import boto3
from botocore.exceptions import ClientError

# ---------------------------------------------------------------------------
# Module-level logger + boto3 clients
# ---------------------------------------------------------------------------
# Created once per cold start. boto3 clients are thread-safe for the
# method calls this Lambda makes (list/describe/create presigned URL).

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

sagemaker = boto3.client("sagemaker")
efs = boto3.client("efs")

# ---------------------------------------------------------------------------
# Environment variables (read at module import, i.e. cold start)
# ---------------------------------------------------------------------------

STUDIO_DOMAIN_NAME = os.environ.get("STUDIO_DOMAIN_NAME", "")
SAGEMAKER_EXECUTION_ROLE_ARN = os.environ.get("SAGEMAKER_EXECUTION_ROLE_ARN", "")
STUDIO_EFS_ID = os.environ.get("STUDIO_EFS_ID", "")
URL_EXPIRES_SECONDS = int(os.environ.get("URL_EXPIRES_SECONDS", "300"))
SESSION_EXPIRES_SECONDS = int(os.environ.get("SESSION_EXPIRES_SECONDS", "43200"))

# ---------------------------------------------------------------------------
# Error tokens — opaque strings returned in the ``error`` body key.
# ---------------------------------------------------------------------------
# Keep these short, stable, and free of implementation details so clients
# can switch on them without parsing exception messages.

_ERR_MISSING_CLAIM = "MissingCognitoClaim"
_ERR_DOMAIN_NOT_FOUND = "SagemakerDomainNotFound"
_ERR_GENERIC = "PresignedUrlGenerationFailed"

# POSIX id derivation constants. 2**31 - 19 keeps the result comfortably
# within the 32-bit signed-int range EFS accepts; the 100000 offset pushes
# the uid/gid out of the system-user range reserved for the base image.
_POSIX_ID_MODULUS = 2147483629
_POSIX_ID_OFFSET = 100000


# ==========================================================================
# Pure helpers (unit-testable without mocking)
# ==========================================================================


def _parse_claims(event: dict[str, Any]) -> dict[str, Any]:
    """Extract the Cognito claims dict from an API Gateway proxy event.

    Returns an empty dict if ``event["requestContext"]["authorizer"]["claims"]``
    is not present or not a dict. The caller decides whether an empty
    result warrants a 401 — see :func:`lambda_handler`.
    """
    if not isinstance(event, dict):
        return {}
    request_context = event.get("requestContext")
    if not isinstance(request_context, dict):
        return {}
    authorizer = request_context.get("authorizer")
    if not isinstance(authorizer, dict):
        return {}
    claims = authorizer.get("claims")
    if not isinstance(claims, dict):
        return {}
    return claims


def _derive_posix_ids(username: str) -> tuple[int, int]:
    """Derive a deterministic POSIX ``(uid, gid)`` pair from a username.

    Uses SHA-256 over the UTF-8 encoded username; the first four bytes
    are interpreted as a big-endian unsigned int, reduced modulo
    ``_POSIX_ID_MODULUS``, and shifted by ``_POSIX_ID_OFFSET`` so the
    result is always ``>= 100000`` and fits within the 32-bit signed
    range EFS expects.

    The gid is always equal to the uid — per-user home directories own
    a single-user group, matching the ``0700`` permissions on
    ``/home/<username>`` access points.
    """
    digest = hashlib.sha256(username.encode("utf-8")).digest()
    raw = int.from_bytes(digest[:4], byteorder="big", signed=False)
    uid = (raw % _POSIX_ID_MODULUS) + _POSIX_ID_OFFSET
    return uid, uid


def _format_success(url: str, expires: int) -> dict[str, Any]:
    """Format an HTTP 200 API Gateway proxy response with the presigned URL."""
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"url": url, "expires_in": expires}),
    }


def _format_error(status: int, token: str) -> dict[str, Any]:
    """Format an HTTP error API Gateway proxy response.

    ``token`` is one of the module-level ``_ERR_*`` constants; ``status``
    is the HTTP status code. The body never contains an exception
    message — callers log the underlying exception via :data:`logger`
    before returning.
    """
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"error": token}),
    }


# ==========================================================================
# Effectful helpers (wrap boto3 calls)
# ==========================================================================


def _resolve_domain_id(domain_name: str) -> str | None:
    """Return the Studio ``DomainId`` for ``domain_name`` or ``None``.

    Paginates ``sagemaker:ListDomains`` by following ``NextToken``. A
    ``None`` return signals the caller to emit HTTP 404 ``SagemakerDomainNotFound``.
    """
    next_token: str | None = None
    while True:
        kwargs: dict[str, Any] = {}
        if next_token is not None:
            kwargs["NextToken"] = next_token
        response = sagemaker.list_domains(**kwargs)
        for domain in response.get("Domains", []):
            if domain.get("DomainName") == domain_name:
                return domain.get("DomainId")
        next_token = response.get("NextToken")
        if not next_token:
            return None


def _ensure_user_profile(domain_id: str, username: str, efs_id: str) -> None:
    """Ensure a ``sagemaker:UserProfile`` exists for ``username`` in ``domain_id``.

    If ``DescribeUserProfile`` raises a ``ValidationException`` /
    ``ResourceNotFound`` (the two boto3 error codes SageMaker returns for
    missing profiles), the profile is created via ``CreateUserProfile``
    with ``ExecutionRole`` set to :data:`SAGEMAKER_EXECUTION_ROLE_ARN`
    and a ``CustomFileSystemConfigs`` entry mounting ``efs_id`` at
    ``/home/<username>``.
    """
    try:
        sagemaker.describe_user_profile(
            DomainId=domain_id,
            UserProfileName=username,
        )
        return
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code not in {"ValidationException", "ResourceNotFound"}:
            raise

    sagemaker.create_user_profile(
        DomainId=domain_id,
        UserProfileName=username,
        UserSettings={
            "ExecutionRole": SAGEMAKER_EXECUTION_ROLE_ARN,
            "CustomFileSystemConfigs": [
                {
                    "EFSFileSystemConfig": {
                        "FileSystemId": efs_id,
                        "FileSystemPath": f"/home/{username}",
                    }
                }
            ],
        },
    )


def _ensure_access_point(username: str, efs_id: str) -> str:
    """Ensure a per-user EFS access point at ``/home/<username>`` exists.

    Searches existing access points on ``efs_id`` for one whose
    ``RootDirectory.Path`` equals ``/home/<username>``; creates one if
    absent with a POSIX ``(uid, gid)`` derived from
    :func:`_derive_posix_ids` and ``0700`` permissions.

    Returns the ``AccessPointArn`` so the caller can associate it with
    the user profile if needed.
    """
    uid, gid = _derive_posix_ids(username)
    target_path = f"/home/{username}"

    # Paginate describe_access_points. EFS returns at most 100 APs per
    # page by default; we explicitly cap at 1000 to stay inside a single
    # request for typical deployments.
    next_token: str | None = None
    while True:
        kwargs: dict[str, Any] = {"FileSystemId": efs_id, "MaxResults": 1000}
        if next_token is not None:
            kwargs["NextToken"] = next_token
        response = efs.describe_access_points(**kwargs)
        for ap in response.get("AccessPoints", []):
            root_dir = ap.get("RootDirectory", {})
            if root_dir.get("Path") == target_path:
                return ap.get("AccessPointArn", "")
        next_token = response.get("NextToken")
        if not next_token:
            break

    created = efs.create_access_point(
        FileSystemId=efs_id,
        PosixUser={"Uid": uid, "Gid": gid},
        RootDirectory={
            "Path": target_path,
            "CreationInfo": {
                "OwnerUid": uid,
                "OwnerGid": gid,
                "Permissions": "0700",
            },
        },
        Tags=[
            {"Key": "gco:analytics:user", "Value": username},
            {"Key": "gco:analytics:managed", "Value": "true"},
        ],
    )
    return created.get("AccessPointArn", "")


# ==========================================================================
# Entry point
# ==========================================================================


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Exchange a Cognito-authorized event for a presigned Studio URL.

    Wraps the full happy-path flow in an outer ``try/except`` so any
    unexpected failure returns HTTP 500 with the opaque
    ``PresignedUrlGenerationFailed`` token. The underlying exception is
    logged at ``ERROR`` level via :data:`logger` but never included in
    the response body.
    """
    try:
        # Step 1: extract + validate the Cognito username claim.
        claims = _parse_claims(event)
        username = claims.get("cognito:username") or claims.get("username")
        if not isinstance(username, str) or not username:
            logger.warning("Request missing Cognito username claim")
            return _format_error(401, _ERR_MISSING_CLAIM)

        # Step 2: resolve the Studio DomainId.
        domain_id = _resolve_domain_id(STUDIO_DOMAIN_NAME)
        if domain_id is None:
            logger.error(
                "SageMaker domain %r not found via ListDomains",
                STUDIO_DOMAIN_NAME,
            )
            return _format_error(404, _ERR_DOMAIN_NOT_FOUND)

        # Step 3: describe-or-create the user profile.
        _ensure_user_profile(domain_id, username, STUDIO_EFS_ID)

        # Step 4: lazy per-user EFS access point.
        _ensure_access_point(username, STUDIO_EFS_ID)

        # Step 5: mint the presigned URL.
        response = sagemaker.create_presigned_domain_url(
            DomainId=domain_id,
            UserProfileName=username,
            SessionExpirationDurationInSeconds=SESSION_EXPIRES_SECONDS,
            ExpiresInSeconds=URL_EXPIRES_SECONDS,
        )
        url = response.get("AuthorizedUrl", "")
        return _format_success(url, URL_EXPIRES_SECONDS)

    except (
        Exception
    ) as exc:  # noqa: BLE001 — outer catch-all so every failure returns an opaque error token
        # Log with exception info so CloudWatch captures the stack trace,
        # but never leak the message to the HTTP response body.
        logger.error("Presigned URL generation failed: %s", exc, exc_info=True)
        return _format_error(500, _ERR_GENERIC)
