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
    vpc_id = os.environ.get("VPC_ID", "")

    errors: list[str] = []

    # Delete all apps (must be deleted before spaces/profiles)
    errors.extend(_delete_apps(region, domain_id))

    # Delete all spaces
    errors.extend(_delete_spaces(region, domain_id))

    # Delete all user profiles from the domain
    errors.extend(_delete_user_profiles(region, domain_id))

    # Delete all EFS access points
    errors.extend(_delete_access_points(region, efs_id))

    # Delete SageMaker-managed NFS security groups
    if vpc_id:
        errors.extend(_delete_sagemaker_security_groups(region, domain_id, vpc_id))

    if errors:
        logger.warning("Cleanup completed with %d errors: %s", len(errors), errors)
    else:
        logger.info("Cleanup completed successfully")

    # Always return SUCCESS so CloudFormation can proceed with deletion.
    # Failing here would block the entire stack destroy.
    return {"Status": "SUCCESS", "PhysicalResourceId": physical_id}


def _delete_apps(region: str, domain_id: str) -> list[str]:
    """Delete all apps in the domain. Apps must be deleted before spaces/profiles."""
    errors: list[str] = []
    sm = boto3.client("sagemaker", region_name=region)

    try:
        paginator = sm.get_paginator("list_apps")
        for page in paginator.paginate(DomainIdEquals=domain_id):
            for app in page.get("Apps", []):
                if app.get("Status") in ("Deleted", "Failed"):
                    continue
                app_name = app["AppName"]
                app_type = app["AppType"]
                space_name = app.get("SpaceName")
                user_profile = app.get("UserProfileName")
                try:
                    kwargs = {
                        "DomainId": domain_id,
                        "AppType": app_type,
                        "AppName": app_name,
                    }
                    if space_name:
                        kwargs["SpaceName"] = space_name
                    if user_profile:
                        kwargs["UserProfileName"] = user_profile
                    sm.delete_app(**kwargs)
                    logger.info("Deleted app: %s (%s)", app_name, app_type)
                except ClientError as e:
                    if "does not exist" not in str(e):
                        msg = f"Failed to delete app {app_name}: {e}"
                        logger.error(msg)
                        errors.append(msg)

        # Wait for apps to finish deleting (up to 2 minutes)
        for _ in range(24):
            time.sleep(5)
            active = []
            for page in paginator.paginate(DomainIdEquals=domain_id):
                for app in page.get("Apps", []):
                    if app.get("Status") not in ("Deleted", "Failed"):
                        active.append(app["AppName"])
            if not active:
                break
            logger.info("Waiting for %d app(s) to delete...", len(active))

    except ClientError as e:
        msg = f"Failed to list apps: {e}"
        logger.error(msg)
        errors.append(msg)

    return errors


def _delete_spaces(region: str, domain_id: str) -> list[str]:
    """Delete all spaces in the domain. Spaces must be deleted before profiles."""
    errors: list[str] = []
    sm = boto3.client("sagemaker", region_name=region)

    try:
        paginator = sm.get_paginator("list_spaces")
        for page in paginator.paginate(DomainIdEquals=domain_id):
            for space in page.get("Spaces", []):
                space_name = space["SpaceName"]
                try:
                    sm.delete_space(DomainId=domain_id, SpaceName=space_name)
                    logger.info("Deleted space: %s", space_name)
                    time.sleep(1)
                except ClientError as e:
                    if "does not exist" not in str(e):
                        msg = f"Failed to delete space {space_name}: {e}"
                        logger.error(msg)
                        errors.append(msg)
    except ClientError as e:
        msg = f"Failed to list spaces: {e}"
        logger.error(msg)
        errors.append(msg)

    return errors


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
                    sm.delete_user_profile(DomainId=domain_id, UserProfileName=name)
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


def _delete_sagemaker_security_groups(region: str, domain_id: str, vpc_id: str) -> list[str]:
    """Delete SageMaker-managed security groups for the domain.

    SageMaker creates security groups named
    ``security-group-for-outbound-nfs-<domain-id>`` and
    ``security-group-for-inbound-nfs-<domain-id>`` when the domain uses
    a custom EFS. These are tagged "[DO NOT DELETE]" but must be removed
    for the VPC to be deletable.
    """
    errors: list[str] = []
    ec2 = boto3.client("ec2", region_name=region)

    try:
        response = ec2.describe_security_groups(
            Filters=[
                {"Name": "vpc-id", "Values": [vpc_id]},
                {"Name": "group-name", "Values": [f"*{domain_id}*"]},
            ]
        )
        for sg in response.get("SecurityGroups", []):
            sg_id = sg["GroupId"]
            sg_name = sg.get("GroupName", "")
            try:
                ec2.delete_security_group(GroupId=sg_id)
                logger.info("Deleted SageMaker security group: %s (%s)", sg_id, sg_name)
            except ClientError as e:
                msg = f"Failed to delete security group {sg_id}: {e}"
                logger.error(msg)
                errors.append(msg)
    except ClientError as e:
        msg = f"Failed to list SageMaker security groups: {e}"
        logger.error(msg)
        errors.append(msg)

    return errors
