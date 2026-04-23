"""DynamoDB-backed global job queue endpoints."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, Response

from gco.models import ManifestSubmissionRequest
from gco.services.api_shared import QueuedJobRequest, _check_processor
from gco.services.template_store import JobStatus as JobStoreStatus

if TYPE_CHECKING:
    from gco.services.template_store import JobStore

router = APIRouter(prefix="/api/v1/queue", tags=["Job Queue"])
logger = logging.getLogger(__name__)


def _get_job_store() -> JobStore:
    from gco.services.manifest_api import job_store

    if job_store is None:
        raise HTTPException(status_code=503, detail="Job store not initialized")
    return job_store


@router.post("/jobs")
async def submit_job_to_queue(request: QueuedJobRequest) -> Response:
    """Submit a job to the global queue for regional pickup."""
    import uuid

    store = _get_job_store()
    job_id = str(uuid.uuid4())

    try:
        job = store.submit_job(
            job_id=job_id,
            manifest=request.manifest,
            target_region=request.target_region,
            namespace=request.namespace,
            priority=request.priority,
            labels=request.labels,
        )
        return JSONResponse(
            status_code=201,
            content={
                "timestamp": datetime.now(UTC).isoformat(),
                "message": "Job queued successfully",
                "job": job,
            },
        )
    except Exception as e:
        logger.error(f"Failed to queue job: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to queue job: {e!s}") from e


@router.get("/jobs")
async def list_queued_jobs(
    target_region: str | None = Query(None, description="Filter by target region"),
    status: str | None = Query(None, description="Filter by status"),
    namespace: str | None = Query(None, description="Filter by namespace"),
    limit: int = Query(100, description="Maximum results", ge=1, le=1000),
) -> Response:
    """List jobs in the global queue with optional filters."""
    store = _get_job_store()
    try:
        jobs = store.list_jobs(
            target_region=target_region, status=status, namespace=namespace, limit=limit
        )
        return JSONResponse(
            status_code=200,
            content={
                "timestamp": datetime.now(UTC).isoformat(),
                "count": len(jobs),
                "jobs": jobs,
            },
        )
    except Exception as e:
        logger.error(f"Failed to list queued jobs: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list jobs: {e!s}") from e


@router.get("/jobs/{job_id}")
async def get_queued_job(job_id: str) -> Response:
    """Get details of a specific queued job."""
    store = _get_job_store()
    try:
        job = store.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
        return JSONResponse(
            status_code=200,
            content={"timestamp": datetime.now(UTC).isoformat(), "job": job},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get job {job_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get job: {e!s}") from e


@router.delete("/jobs/{job_id}")
async def cancel_queued_job(
    job_id: str, reason: str | None = Query(None, description="Cancellation reason")
) -> Response:
    """Cancel a queued job (only works for jobs not yet running)."""
    store = _get_job_store()
    try:
        cancelled = store.cancel_job(job_id, reason=reason)
        if not cancelled:
            raise HTTPException(
                status_code=409,
                detail=f"Job '{job_id}' cannot be cancelled (already running or completed)",
            )
        return JSONResponse(
            status_code=200,
            content={
                "timestamp": datetime.now(UTC).isoformat(),
                "message": f"Job '{job_id}' cancelled successfully",
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to cancel job {job_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to cancel job: {e!s}") from e


@router.get("/stats")
async def get_queue_stats() -> Response:
    """Get job queue statistics by region and status."""
    store = _get_job_store()
    try:
        counts = store.get_job_counts_by_region()
        total_jobs = sum(sum(statuses.values()) for statuses in counts.values())
        total_queued = sum(statuses.get("queued", 0) for statuses in counts.values())
        total_running = sum(statuses.get("running", 0) for statuses in counts.values())

        return JSONResponse(
            status_code=200,
            content={
                "timestamp": datetime.now(UTC).isoformat(),
                "summary": {
                    "total_jobs": total_jobs,
                    "total_queued": total_queued,
                    "total_running": total_running,
                },
                "by_region": counts,
            },
        )
    except Exception as e:
        logger.error(f"Failed to get queue stats: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get stats: {e!s}") from e


@router.post("/poll")
async def poll_and_process_jobs(
    limit: int = Query(5, description="Maximum jobs to process", ge=1, le=20),
) -> Response:
    """Poll for queued jobs and process them (called by regional processors)."""
    processor = _check_processor()
    store = _get_job_store()

    region = processor.region
    processed_jobs = []

    try:
        queued_jobs = store.get_queued_jobs_for_region(region, limit=limit)

        for queued_job in queued_jobs:
            job_id = queued_job["job_id"]

            claimed = store.claim_job(job_id, claimed_by=region)
            if not claimed:
                continue

            try:
                store.update_job_status(
                    job_id, JobStoreStatus.APPLYING, message="Applying to Kubernetes"
                )

                manifest = queued_job["manifest"]
                namespace = queued_job["namespace"]

                submission_request = ManifestSubmissionRequest(
                    manifests=[manifest], namespace=namespace, dry_run=False, validate=True
                )

                result = await processor.process_manifest_submission(submission_request)

                if result.success:
                    k8s_uid = None
                    if result.resources:
                        k8s_uid = result.resources[0].uid

                    store.update_job_status(
                        job_id,
                        JobStoreStatus.PENDING,
                        message="Applied to Kubernetes, waiting for scheduling",
                        k8s_job_uid=k8s_uid,
                    )
                    processed_jobs.append(
                        {"job_id": job_id, "status": "applied", "k8s_uid": k8s_uid}
                    )
                else:
                    error_msg = "; ".join(result.errors) if result.errors else "Unknown error"
                    store.update_job_status(
                        job_id,
                        JobStoreStatus.FAILED,
                        message="Failed to apply to Kubernetes",
                        error=error_msg,
                    )
                    processed_jobs.append(
                        {"job_id": job_id, "status": "failed", "error": error_msg}
                    )

            except Exception as e:
                logger.error(f"Failed to process job {job_id}: {e}")
                store.update_job_status(
                    job_id, JobStoreStatus.FAILED, message="Processing error", error=str(e)
                )
                processed_jobs.append({"job_id": job_id, "status": "failed", "error": str(e)})

        return JSONResponse(
            status_code=200,
            content={
                "timestamp": datetime.now(UTC).isoformat(),
                "region": region,
                "jobs_polled": len(queued_jobs),
                "jobs_processed": len(processed_jobs),
                "results": processed_jobs,
            },
        )

    except Exception as e:
        logger.error(f"Failed to poll jobs: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to poll jobs: {e!s}") from e
