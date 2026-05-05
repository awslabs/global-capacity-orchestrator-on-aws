"""Analytics stack cleanup handler.

Runs as a CloudFormation custom resource on stack deletion. Removes all
SageMaker user profiles from the Studio domain and all EFS access points
from the Studio file system so CloudFormation can delete the domain and
EFS cleanly.

Environment variables:
    DOMAIN_ID: SageMaker Studio domain ID
    EFS_ID: EFS file system ID
    REGION: AWS region
"""

from __future__ import annotations

import logging
import os
import time

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def handler(event: dict, context: object) -> dict:
    """CloudFormation custom resource handler."""
    request_type = event.get("RequestType", "")
    physical_id = event.get("PhysicalResourceId", "analytics-cleanup")

    if request_type != "Delete":
        logger.info("RequestType=%s, nothing to do", request_type)
        return {"Status": "SUCCESS", "PhysicalResourceId": physical_id}

    region = os.environ["REGION"]
    domain_id = os.environ["DOMAIN_ID"]
    efs_id = os.environ["EFS_ID"]

    errors: list[str] = []

    # Delete all user profiles from the domain
    errors.extend(_delete_user_profiles(region, domain_id))

    # Delete all EFS access points
    errors.extend(_delete_access_points(region, efs_id))

    if errors:
        logger.warning("Cleanup completed with %d errors: %s", len(errors), errors)
    else:
        logger.info("Cleanup completed successfully")

    # Always return SUCCESS so CloudFormation can proceed with deletion.
    # Failing here would block the entire stack destroy.
    return {"Status": "SUCCESS", "PhysicalResourceId": physical_id}


def _delete_user_profiles(region: str, domain_id: str) -> list[str]:
    """Delete all user profiles in the domain. Returns a list of error messages."""
    errors: list[str] = []
    sm = boto3.client("sagemaker", region_name=region)

    try:
        paginator = sm.get_paginator("list_user_profiles")
        for page in paginator.paginate(DomainIdEquals=domain_id):
            for profile in page.get("UserProfiles", []):
                name = profile["UserProfileName"]
                try:
                    sm.delete_user_profile(
                        DomainId=domain_id, UserProfileName=name
                    )
                    logger.info("Deleted user profile: %s", name)
                    # Brief pause to avoid throttling
                    time.sleep(1)
                except ClientError as e:
                    msg = f"Failed to delete profile {name}: {e}"
                    logger.error(msg)
                    errors.append(msg)
    except ClientError as e:
        msg = f"Failed to list user profiles: {e}"
        logger.error(msg)
        errors.append(msg)

    return errors


def _delete_access_points(region: str, efs_id: str) -> list[str]:
    """Delete all access points on the file system. Returns a list of error messages."""
    errors: list[str] = []
    efs = boto3.client("efs", region_name=region)

    try:
        paginator = efs.get_paginator("describe_access_points")
        for page in paginator.paginate(FileSystemId=efs_id):
            for ap in page.get("AccessPoints", []):
                ap_id = ap["AccessPointId"]
                try:
                    efs.delete_access_point(AccessPointId=ap_id)
                    logger.info("Deleted access point: %s", ap_id)
                except ClientError as e:
                    msg = f"Failed to delete access point {ap_id}: {e}"
                    logger.error(msg)
                    errors.append(msg)
    except ClientError as e:
        msg = f"Failed to list access points: {e}"
        logger.error(msg)
        errors.append(msg)

    return errors
