"""
DynamoDB-backed storage for job templates, webhooks, and job records.

This module provides persistent storage for:
- Job templates: Reusable job configurations with parameter substitution
- Webhooks: Event notification registrations
- Job records: Centralized job tracking with status updates

Tables are created in the global stack and accessed from all regional services.

Region Configuration:
    DynamoDB tables are deployed in the global region (e.g., us-east-2) but
    accessed from regional services (e.g., us-east-1). The region is determined
    by checking environment variables in this order:
    1. DYNAMODB_REGION - Explicitly set for DynamoDB access
    2. GLOBAL_REGION - The global stack's region
    3. AWS_REGION - Fallback to current region

Job Queue Architecture:
    1. Jobs are submitted to the jobs table with target_region and status="queued"
    2. Regional manifest processors poll for jobs targeting their region
    3. Processor claims job (status="claimed"), applies to K8s, updates status
    4. Status updates flow back to DynamoDB for global visibility
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    """Return current UTC time in ISO format with Z suffix."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class JobStatus(StrEnum):
    """Job status values for the centralized job store."""

    QUEUED = "queued"  # Submitted, waiting for regional pickup
    CLAIMED = "claimed"  # Claimed by a regional processor
    APPLYING = "applying"  # Being applied to Kubernetes
    PENDING = "pending"  # Applied, waiting for pod scheduling
    RUNNING = "running"  # Pod(s) running
    SUCCEEDED = "succeeded"  # Job completed successfully
    FAILED = "failed"  # Job failed
    CANCELLED = "cancelled"  # Job was cancelled


class TemplateStore:
    """DynamoDB-backed store for job templates."""

    def __init__(self, table_name: str | None = None, region: str | None = None):
        """Initialize the template store.

        Args:
            table_name: DynamoDB table name. Defaults to env var TEMPLATES_TABLE_NAME.
            region: AWS region for DynamoDB. Defaults to env var DYNAMODB_REGION,
                    then GLOBAL_REGION, then AWS_REGION.
        """
        self.table_name = table_name or os.getenv("TEMPLATES_TABLE_NAME", "gco-job-templates")
        # DynamoDB tables are in the global region, not the regional cluster region
        self.region = (
            region
            or os.getenv("DYNAMODB_REGION")
            or os.getenv("GLOBAL_REGION")
            or os.getenv("AWS_REGION", "us-east-1")
        )
        self._dynamodb = boto3.resource("dynamodb", region_name=self.region)
        self._table = self._dynamodb.Table(self.table_name)

    def list_templates(self) -> list[dict[str, Any]]:
        """List all templates."""
        try:
            response = self._table.scan(
                ProjectionExpression="template_name, description, created_at, updated_at"
            )
            items = response.get("Items", [])

            # Handle pagination
            while "LastEvaluatedKey" in response:
                response = self._table.scan(
                    ProjectionExpression="template_name, description, created_at, updated_at",
                    ExclusiveStartKey=response["LastEvaluatedKey"],
                )
                items.extend(response.get("Items", []))

            return [
                {
                    "name": item["template_name"],
                    "description": item.get("description"),
                    "created_at": item.get("created_at"),
                    "updated_at": item.get("updated_at"),
                }
                for item in items
            ]
        except ClientError as e:
            logger.error(f"Failed to list templates: {e}")
            raise

    def get_template(self, name: str) -> dict[str, Any] | None:
        """Get a template by name."""
        try:
            response = self._table.get_item(Key={"template_name": name})
            item = response.get("Item")
            if not item:
                return None

            return {
                "name": item["template_name"],
                "description": item.get("description"),
                "manifest": json.loads(item["manifest"]),
                "parameters": json.loads(item.get("parameters", "{}")),
                "created_at": item.get("created_at"),
                "updated_at": item.get("updated_at"),
            }
        except ClientError as e:
            logger.error(f"Failed to get template {name}: {e}")
            raise

    def create_template(
        self,
        name: str,
        manifest: dict[str, Any],
        description: str | None = None,
        parameters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a new template."""
        now = _utc_now_iso()

        item = {
            "template_name": name,
            "manifest": json.dumps(manifest),
            "parameters": json.dumps(parameters or {}),
            "created_at": now,
            "updated_at": now,
        }
        if description:
            item["description"] = description

        try:
            self._table.put_item(
                Item=item,
                ConditionExpression="attribute_not_exists(template_name)",
            )
            return {
                "name": name,
                "description": description,
                "manifest": manifest,
                "parameters": parameters or {},
                "created_at": now,
            }
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ValueError(f"Template '{name}' already exists") from e
            logger.error(f"Failed to create template {name}: {e}")
            raise

    def update_template(
        self,
        name: str,
        manifest: dict[str, Any] | None = None,
        description: str | None = None,
        parameters: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Update an existing template."""
        now = _utc_now_iso()

        update_expr_parts = ["updated_at = :updated_at"]
        expr_values: dict[str, Any] = {":updated_at": now}

        if manifest is not None:
            update_expr_parts.append("manifest = :manifest")
            expr_values[":manifest"] = json.dumps(manifest)

        if description is not None:
            update_expr_parts.append("description = :description")
            expr_values[":description"] = description

        if parameters is not None:
            update_expr_parts.append("parameters = :parameters")
            expr_values[":parameters"] = json.dumps(parameters)

        try:
            response = self._table.update_item(
                Key={"template_name": name},
                UpdateExpression="SET " + ", ".join(update_expr_parts),
                ExpressionAttributeValues=expr_values,
                ConditionExpression="attribute_exists(template_name)",
                ReturnValues="ALL_NEW",
            )
            item = response.get("Attributes", {})
            return {
                "name": item["template_name"],
                "description": item.get("description"),
                "manifest": json.loads(item["manifest"]),
                "parameters": json.loads(item.get("parameters", "{}")),
                "created_at": item.get("created_at"),
                "updated_at": item.get("updated_at"),
            }
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return None
            logger.error(f"Failed to update template {name}: {e}")
            raise

    def delete_template(self, name: str) -> bool:
        """Delete a template."""
        try:
            self._table.delete_item(
                Key={"template_name": name},
                ConditionExpression="attribute_exists(template_name)",
            )
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            logger.error(f"Failed to delete template {name}: {e}")
            raise

    def template_exists(self, name: str) -> bool:
        """Check if a template exists."""
        try:
            response = self._table.get_item(
                Key={"template_name": name},
                ProjectionExpression="template_name",
            )
            return "Item" in response
        except ClientError as e:
            logger.error(f"Failed to check template existence {name}: {e}")
            raise


class WebhookStore:
    """DynamoDB-backed store for webhooks."""

    def __init__(self, table_name: str | None = None, region: str | None = None):
        """Initialize the webhook store.

        Args:
            table_name: DynamoDB table name. Defaults to env var WEBHOOKS_TABLE_NAME.
            region: AWS region for DynamoDB. Defaults to env var DYNAMODB_REGION,
                    then GLOBAL_REGION, then AWS_REGION.
        """
        self.table_name = table_name or os.getenv("WEBHOOKS_TABLE_NAME", "gco-webhooks")
        # DynamoDB tables are in the global region, not the regional cluster region
        self.region = (
            region
            or os.getenv("DYNAMODB_REGION")
            or os.getenv("GLOBAL_REGION")
            or os.getenv("AWS_REGION", "us-east-1")
        )
        self._dynamodb = boto3.resource("dynamodb", region_name=self.region)
        self._table = self._dynamodb.Table(self.table_name)

    def list_webhooks(self, namespace: str | None = None) -> list[dict[str, Any]]:
        """List webhooks, optionally filtered by namespace."""
        try:
            if namespace:
                response = self._table.query(
                    IndexName="namespace-index",
                    KeyConditionExpression="namespace = :ns",
                    ExpressionAttributeValues={":ns": namespace},
                )
                items = response.get("Items", [])
            else:
                response = self._table.scan()
                items = response.get("Items", [])

                while "LastEvaluatedKey" in response:
                    response = self._table.scan(
                        ExclusiveStartKey=response["LastEvaluatedKey"],
                    )
                    items.extend(response.get("Items", []))

            return [
                {
                    "id": item["webhook_id"],
                    "url": item["url"],
                    "events": json.loads(item.get("events", "[]")),
                    "namespace": item.get("namespace"),
                    "created_at": item.get("created_at"),
                }
                for item in items
            ]
        except ClientError as e:
            logger.error(f"Failed to list webhooks: {e}")
            raise

    def get_webhook(self, webhook_id: str) -> dict[str, Any] | None:
        """Get a webhook by ID."""
        try:
            response = self._table.get_item(Key={"webhook_id": webhook_id})
            item = response.get("Item")
            if not item:
                return None

            return {
                "id": item["webhook_id"],
                "url": item["url"],
                "events": json.loads(item.get("events", "[]")),
                "namespace": item.get("namespace"),
                "secret": item.get("secret"),
                "created_at": item.get("created_at"),
            }
        except ClientError as e:
            logger.error(f"Failed to get webhook {webhook_id}: {e}")
            raise

    def create_webhook(
        self,
        webhook_id: str,
        url: str,
        events: list[str],
        namespace: str | None = None,
        secret: str | None = None,
    ) -> dict[str, Any]:
        """Create a new webhook."""
        now = _utc_now_iso()

        item: dict[str, Any] = {
            "webhook_id": webhook_id,
            "url": url,
            "events": json.dumps(events),
            "created_at": now,
        }
        if namespace:
            item["namespace"] = namespace
        if secret:
            item["secret"] = secret

        try:
            self._table.put_item(Item=item)
            return {
                "id": webhook_id,
                "url": url,
                "events": events,
                "namespace": namespace,
                "created_at": now,
            }
        except ClientError as e:
            logger.error(f"Failed to create webhook: {e}")
            raise

    def delete_webhook(self, webhook_id: str) -> bool:
        """Delete a webhook."""
        try:
            self._table.delete_item(
                Key={"webhook_id": webhook_id},
                ConditionExpression="attribute_exists(webhook_id)",
            )
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            logger.error(f"Failed to delete webhook {webhook_id}: {e}")
            raise

    def get_webhooks_for_event(
        self, event: str, namespace: str | None = None
    ) -> list[dict[str, Any]]:
        """Get all webhooks subscribed to a specific event."""
        webhooks = self.list_webhooks(namespace=namespace)
        return [w for w in webhooks if event in w.get("events", [])]


class JobStore:
    """DynamoDB-backed store for centralized job tracking.

    This store enables:
    - Global job submission with region targeting
    - Real-time status tracking across all regions
    - Job history and audit trail
    - Cross-region job queries without hitting K8s APIs
    """

    def __init__(self, table_name: str | None = None, region: str | None = None):
        """Initialize the job store.

        Args:
            table_name: DynamoDB table name. Defaults to env var JOBS_TABLE_NAME.
            region: AWS region for DynamoDB. Defaults to env var DYNAMODB_REGION,
                    then GLOBAL_REGION, then AWS_REGION.
        """
        self.table_name = table_name or os.getenv("JOBS_TABLE_NAME", "gco-jobs")
        # DynamoDB tables are in the global region, not the regional cluster region
        self.region = (
            region
            or os.getenv("DYNAMODB_REGION")
            or os.getenv("GLOBAL_REGION")
            or os.getenv("AWS_REGION", "us-east-1")
        )
        self._dynamodb = boto3.resource("dynamodb", region_name=self.region)
        self._table = self._dynamodb.Table(self.table_name)

    def submit_job(
        self,
        job_id: str,
        manifest: dict[str, Any],
        target_region: str,
        namespace: str = "gco-jobs",
        priority: int = 0,
        labels: dict[str, str] | None = None,
        submitted_by: str | None = None,
    ) -> dict[str, Any]:
        """Submit a job to the centralized queue.

        Args:
            job_id: Unique job identifier
            manifest: Kubernetes job manifest
            target_region: Region where job should run
            namespace: Kubernetes namespace
            priority: Job priority (higher = more important)
            labels: Optional labels for filtering
            submitted_by: Optional submitter identifier

        Returns:
            Job record with submission details
        """
        now = _utc_now_iso()

        # Extract job name from manifest
        job_name = manifest.get("metadata", {}).get("name", job_id)

        item: dict[str, Any] = {
            "job_id": job_id,
            "job_name": job_name,
            "target_region": target_region,
            "namespace": namespace,
            "status": JobStatus.QUEUED.value,
            "priority": priority,
            "manifest": json.dumps(manifest),
            "submitted_at": now,
            "updated_at": now,
            "status_history": json.dumps(
                [{"status": JobStatus.QUEUED.value, "timestamp": now, "message": "Job submitted"}]
            ),
        }

        if labels:
            item["labels"] = json.dumps(labels)
        if submitted_by:
            item["submitted_by"] = submitted_by

        try:
            self._table.put_item(Item=item)
            return {
                "job_id": job_id,
                "job_name": job_name,
                "target_region": target_region,
                "namespace": namespace,
                "status": JobStatus.QUEUED.value,
                "priority": priority,
                "submitted_at": now,
            }
        except ClientError as e:
            logger.error(f"Failed to submit job {job_id}: {e}")
            raise

    def claim_job(self, job_id: str, claimed_by: str) -> dict[str, Any] | None:
        """Claim a queued job for processing.

        Uses conditional update to prevent race conditions between regions.

        Args:
            job_id: Job to claim
            claimed_by: Identifier of the claiming processor (e.g., region name)

        Returns:
            Job record if claimed successfully, None if already claimed
        """
        now = _utc_now_iso()

        try:
            response = self._table.update_item(
                Key={"job_id": job_id},
                UpdateExpression="SET #status = :new_status, claimed_by = :claimed_by, "
                "claimed_at = :claimed_at, updated_at = :updated_at",
                ConditionExpression="#status = :queued_status",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":new_status": JobStatus.CLAIMED.value,
                    ":queued_status": JobStatus.QUEUED.value,
                    ":claimed_by": claimed_by,
                    ":claimed_at": now,
                    ":updated_at": now,
                },
                ReturnValues="ALL_NEW",
            )
            item = response.get("Attributes", {})
            return self._parse_job_item(item)
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return None  # Job already claimed
            logger.error(f"Failed to claim job {job_id}: {e}")
            raise

    def update_job_status(
        self,
        job_id: str,
        status: JobStatus | str,
        message: str | None = None,
        k8s_job_uid: str | None = None,
        error: str | None = None,
    ) -> dict[str, Any] | None:
        """Update job status with history tracking.

        Args:
            job_id: Job to update
            status: New status
            message: Optional status message
            k8s_job_uid: Kubernetes job UID (set when job is applied)
            error: Error message if failed

        Returns:
            Updated job record
        """
        now = _utc_now_iso()
        status_value = status.value if isinstance(status, JobStatus) else status

        # Build update expression
        update_parts = ["#status = :status", "updated_at = :updated_at"]
        expr_values: dict[str, Any] = {
            ":status": status_value,
            ":updated_at": now,
        }

        if k8s_job_uid:
            update_parts.append("k8s_job_uid = :k8s_uid")
            expr_values[":k8s_uid"] = k8s_job_uid

        if error:
            update_parts.append("error_message = :error")
            expr_values[":error"] = error

        if status_value in [JobStatus.SUCCEEDED.value, JobStatus.FAILED.value]:
            update_parts.append("completed_at = :completed_at")
            expr_values[":completed_at"] = now

        try:
            response = self._table.update_item(
                Key={"job_id": job_id},
                UpdateExpression="SET " + ", ".join(update_parts),
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues=expr_values,
                ReturnValues="ALL_NEW",
            )
            item = response.get("Attributes", {})

            # Append to status history (separate update to avoid conflicts)
            history_entry = {"status": status_value, "timestamp": now}
            if message:
                history_entry["message"] = message
            if error:
                history_entry["error"] = error

            self._append_status_history(job_id, history_entry)

            return self._parse_job_item(item)
        except ClientError as e:
            logger.error(f"Failed to update job {job_id}: {e}")
            raise

    def _append_status_history(self, job_id: str, entry: dict[str, Any]) -> None:
        """Append an entry to job status history."""
        try:
            # Get current history
            response = self._table.get_item(
                Key={"job_id": job_id},
                ProjectionExpression="status_history",
            )
            current = json.loads(response.get("Item", {}).get("status_history", "[]"))
            current.append(entry)

            # Update with new history
            self._table.update_item(
                Key={"job_id": job_id},
                UpdateExpression="SET status_history = :history",
                ExpressionAttributeValues={":history": json.dumps(current)},
            )
        except ClientError as e:
            logger.warning(f"Failed to update status history for {job_id}: {e}")

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        """Get a job by ID."""
        try:
            response = self._table.get_item(Key={"job_id": job_id})
            item = response.get("Item")
            if not item:
                return None
            return self._parse_job_item(item)
        except ClientError as e:
            logger.error(f"Failed to get job {job_id}: {e}")
            raise

    def list_jobs(
        self,
        target_region: str | None = None,
        status: str | None = None,
        namespace: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List jobs with optional filters.

        Args:
            target_region: Filter by target region
            status: Filter by status
            namespace: Filter by namespace
            limit: Maximum results

        Returns:
            List of job records
        """
        try:
            # Build filter expression
            filter_parts = []
            expr_values: dict[str, Any] = {}
            expr_names: dict[str, str] = {}

            if target_region:
                filter_parts.append("target_region = :region")
                expr_values[":region"] = target_region

            if status:
                filter_parts.append("#status = :status")
                expr_values[":status"] = status
                expr_names["#status"] = "status"

            if namespace:
                filter_parts.append("#ns = :namespace")
                expr_values[":namespace"] = namespace
                expr_names["#ns"] = "namespace"

            scan_kwargs: dict[str, Any] = {"Limit": limit}
            if filter_parts:
                scan_kwargs["FilterExpression"] = " AND ".join(filter_parts)
                scan_kwargs["ExpressionAttributeValues"] = expr_values
            if expr_names:
                scan_kwargs["ExpressionAttributeNames"] = expr_names

            response = self._table.scan(**scan_kwargs)
            items = response.get("Items", [])

            return [self._parse_job_item(item) for item in items]
        except ClientError as e:
            logger.error(f"Failed to list jobs: {e}")
            raise

    def get_queued_jobs_for_region(self, region: str, limit: int = 10) -> list[dict[str, Any]]:
        """Get queued jobs targeting a specific region.

        Used by regional processors to poll for work.

        Args:
            region: Target region
            limit: Maximum jobs to return

        Returns:
            List of queued jobs sorted by priority (descending)
        """
        try:
            response = self._table.query(
                IndexName="region-status-index",
                KeyConditionExpression="target_region = :region AND #status = :status",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":region": region,
                    ":status": JobStatus.QUEUED.value,
                },
                Limit=limit,
                ScanIndexForward=False,  # Descending order
            )
            items = response.get("Items", [])

            # Sort by priority (higher first)
            jobs = [self._parse_job_item(item) for item in items]
            return sorted(jobs, key=lambda j: j.get("priority", 0), reverse=True)
        except ClientError as e:
            logger.error(f"Failed to get queued jobs for {region}: {e}")
            raise

    def get_job_counts_by_region(self) -> dict[str, dict[str, int]]:
        """Get job counts grouped by region and status.

        Returns:
            Dict mapping region -> status -> count
        """
        try:
            response = self._table.scan(
                ProjectionExpression="target_region, #status",
                ExpressionAttributeNames={"#status": "status"},
            )
            items = response.get("Items", [])

            # Handle pagination
            while "LastEvaluatedKey" in response:
                response = self._table.scan(
                    ProjectionExpression="target_region, #status",
                    ExpressionAttributeNames={"#status": "status"},
                    ExclusiveStartKey=response["LastEvaluatedKey"],
                )
                items.extend(response.get("Items", []))

            # Aggregate counts
            counts: dict[str, dict[str, int]] = {}
            for item in items:
                region = item.get("target_region", "unknown")
                status = item.get("status", "unknown")
                if region not in counts:
                    counts[region] = {}
                counts[region][status] = counts[region].get(status, 0) + 1

            return counts
        except ClientError as e:
            logger.error(f"Failed to get job counts: {e}")
            raise

    def cancel_job(self, job_id: str, reason: str | None = None) -> bool:
        """Cancel a job if it's still in a cancellable state.

        Args:
            job_id: Job to cancel
            reason: Optional cancellation reason

        Returns:
            True if cancelled, False if not cancellable
        """
        cancellable_statuses = [JobStatus.QUEUED.value, JobStatus.CLAIMED.value]

        try:
            self._table.update_item(
                Key={"job_id": job_id},
                UpdateExpression="SET #status = :cancelled, updated_at = :now, "
                "cancelled_at = :now, cancel_reason = :reason",
                ConditionExpression="#status IN (:s1, :s2)",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":cancelled": JobStatus.CANCELLED.value,
                    ":now": _utc_now_iso(),
                    ":reason": reason or "Cancelled by user",
                    ":s1": cancellable_statuses[0],
                    ":s2": cancellable_statuses[1],
                },
            )
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            logger.error(f"Failed to cancel job {job_id}: {e}")
            raise

    def _parse_job_item(self, item: dict[str, Any]) -> dict[str, Any]:
        """Parse a DynamoDB item into a job record."""
        return {
            "job_id": item.get("job_id"),
            "job_name": item.get("job_name"),
            "target_region": item.get("target_region"),
            "namespace": item.get("namespace"),
            "status": item.get("status"),
            "priority": int(item.get("priority", 0)),
            "manifest": json.loads(item.get("manifest", "{}")),
            "labels": json.loads(item.get("labels", "{}")),
            "submitted_at": item.get("submitted_at"),
            "submitted_by": item.get("submitted_by"),
            "claimed_by": item.get("claimed_by"),
            "claimed_at": item.get("claimed_at"),
            "completed_at": item.get("completed_at"),
            "updated_at": item.get("updated_at"),
            "k8s_job_uid": item.get("k8s_job_uid"),
            "error_message": item.get("error_message"),
            "status_history": json.loads(item.get("status_history", "[]")),
        }


# Singleton instances for use in the API
_template_store: TemplateStore | None = None
_webhook_store: WebhookStore | None = None
_job_store: JobStore | None = None


def get_template_store() -> TemplateStore:
    """Get or create the template store singleton."""
    global _template_store
    if _template_store is None:
        _template_store = TemplateStore()
    return _template_store


def get_webhook_store() -> WebhookStore:
    """Get or create the webhook store singleton."""
    global _webhook_store
    if _webhook_store is None:
        _webhook_store = WebhookStore()
    return _webhook_store


def get_job_store() -> JobStore:
    """Get or create the job store singleton."""
    global _job_store
    if _job_store is None:
        _job_store = JobStore()
    return _job_store
