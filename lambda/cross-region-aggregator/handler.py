"""
Cross-Region Aggregator Lambda for GCO (Global Capacity Orchestrator on AWS).

This Lambda function aggregates data from all regional GCO clusters,
providing a unified view of jobs, metrics, and status across all regions.
It queries each regional ALB in parallel and merges the results.

Regional Endpoint Discovery:
    Regional ALB hostnames are stored in SSM Parameter Store by the
    GA registration Lambda when each regional stack is deployed.
    Parameters follow the pattern: /{project_name}/alb-hostname-{region}

Environment Variables:
    SECRET_ARN: ARN of the Secrets Manager secret containing the auth token
    PROJECT_NAME: Project name for SSM parameter paths (default: gco)
    GLOBAL_REGION: Region where SSM parameters are stored (default: us-east-2)

API Routes:
    GET /api/v1/global/jobs - List jobs across all regions
    GET /api/v1/global/health - Health status across all regions
    GET /api/v1/global/status - Cluster status across all regions
    DELETE /api/v1/global/jobs - Bulk delete across all regions
"""

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.parse import urlencode

import boto3
import urllib3

# Initialize clients
secrets_client = boto3.client("secretsmanager")
http = urllib3.PoolManager()

# Module-level cache
_cached_secret: str | None = None
_cached_endpoints: dict[str, str] | None = None
_endpoints_cache_time: float = 0
_ENDPOINTS_CACHE_TTL = 300  # 5 minutes — allows picking up new regions


def get_secret_token() -> str:
    """Retrieve the authentication token from Secrets Manager."""
    global _cached_secret
    if _cached_secret is None:
        secret_arn = os.environ["SECRET_ARN"]
        response = secrets_client.get_secret_value(SecretId=secret_arn)
        secret_data = json.loads(response["SecretString"])
        _cached_secret = secret_data["token"]
    return _cached_secret


def get_regional_endpoints() -> dict[str, str]:
    """Get the regional ALB endpoints from SSM Parameter Store.

    Discovers all regional ALB hostnames stored by the GA registration Lambda.
    Parameters are stored as /{project_name}/alb-hostname-{region}.

    Results are cached with a 5-minute TTL so new regions are picked up
    without requiring a Lambda cold start.
    """
    global _cached_endpoints, _endpoints_cache_time

    # Check cache with TTL
    if (
        _cached_endpoints is not None
        and (time.time() - _endpoints_cache_time) < _ENDPOINTS_CACHE_TTL
    ):
        return _cached_endpoints

    project_name = os.environ.get("PROJECT_NAME", "gco")
    global_region = os.environ.get("GLOBAL_REGION", "us-east-2")

    ssm_client = boto3.client("ssm", region_name=global_region)
    endpoints: dict[str, str] = {}

    try:
        # Get all ALB hostname parameters
        paginator = ssm_client.get_paginator("get_parameters_by_path")
        for page in paginator.paginate(Path=f"/{project_name}/", Recursive=False):
            for param in page.get("Parameters", []):
                name = param["Name"]
                # Extract region from parameter name like /gco/alb-hostname-us-east-1
                if "/alb-hostname-" in name:
                    region = name.split("/alb-hostname-")[-1]
                    endpoints[region] = param["Value"]
    except Exception as e:
        # Log error but don't fail - return empty dict
        print(f"Error fetching regional endpoints from SSM: {e}")

    _cached_endpoints = endpoints
    _endpoints_cache_time = time.time()
    return endpoints


def query_region(
    region: str,
    endpoint: str,
    path: str,
    method: str = "GET",
    body: str | None = None,
    query_params: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Query a single regional endpoint."""
    token = get_secret_token()

    # Build URL
    query_str = ""
    if query_params:
        query_str = "?" + urlencode(query_params)

    url = f"http://{endpoint}{path}{query_str}"

    headers = {
        "X-GCO-Auth-Token": token,
        "Content-Type": "application/json",
    }

    try:
        response = http.request(
            method,
            url,
            headers=headers,
            body=body.encode("utf-8") if body else None,
            timeout=10.0,
        )

        if response.status == 200:
            data: dict[str, Any] = json.loads(response.data.decode("utf-8"))
            data["_region"] = region
            data["_status"] = "success"
            return data
        elif response.status == 503:
            # 503 from health endpoint means degraded, not unreachable
            try:
                data = json.loads(response.data.decode("utf-8"))
                data["_region"] = region
                data["_status"] = "success"
                return data
            except json.JSONDecodeError, UnicodeDecodeError:
                return {
                    "_region": region,
                    "_status": "error",
                    "_error": f"HTTP {response.status}",
                }
        else:
            return {
                "_region": region,
                "_status": "error",
                "_error": f"HTTP {response.status}",
            }
    except Exception as e:
        return {
            "_region": region,
            "_status": "error",
            "_error": str(e),
        }


def aggregate_jobs(
    namespace: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Aggregate jobs from all regions."""
    endpoints = get_regional_endpoints()

    query_params: dict[str, str] = {"limit": str(limit * 2)}  # Get more per region, then trim
    if namespace:
        query_params["namespace"] = namespace
    if status:
        query_params["status"] = status

    all_jobs: list[dict[str, Any]] = []
    region_summaries: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    # Query all regions in parallel
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(
                query_region, region, endpoint, "/api/v1/jobs", "GET", None, query_params
            ): region
            for region, endpoint in endpoints.items()
        }

        for future in as_completed(futures):
            region = futures[future]
            try:
                result = future.result()
                if result.get("_status") == "success":
                    jobs = result.get("jobs", [])
                    # Add region to each job
                    for job in jobs:
                        job["_source_region"] = region
                    all_jobs.extend(jobs)
                    region_summaries.append(
                        {
                            "region": region,
                            "count": result.get("count", len(jobs)),
                            "total": result.get("total", len(jobs)),
                        }
                    )
                else:
                    errors.append(
                        {
                            "region": region,
                            "error": result.get("_error", "Unknown error"),
                        }
                    )
            except Exception as e:
                errors.append({"region": region, "error": str(e)})

    # Sort by creation time descending
    all_jobs.sort(
        key=lambda j: j.get("metadata", {}).get("creationTimestamp", ""),
        reverse=True,
    )

    # Trim to limit
    all_jobs = all_jobs[:limit]

    return {
        "total": sum(r["total"] for r in region_summaries),
        "count": len(all_jobs),
        "limit": limit,
        "regions_queried": len(endpoints),
        "regions_successful": len(region_summaries),
        "region_summaries": region_summaries,
        "jobs": all_jobs,
        "errors": errors if errors else None,
    }


def aggregate_metrics() -> dict[str, Any]:
    """Aggregate cluster metrics from all regions."""
    endpoints = get_regional_endpoints()

    region_metrics: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(query_region, region, endpoint, "/api/v1/status"): region
            for region, endpoint in endpoints.items()
        }

        for future in as_completed(futures):
            region = futures[future]
            try:
                result = future.result()
                if result.get("_status") == "success":
                    region_metrics.append(
                        {
                            "region": region,
                            "cluster_id": result.get("cluster_id"),
                            "templates_count": result.get("templates_count", 0),
                            "webhooks_count": result.get("webhooks_count", 0),
                            "resource_limits": result.get("resource_limits", {}),
                            "allowed_namespaces": result.get("allowed_namespaces", []),
                        }
                    )
                else:
                    errors.append(
                        {
                            "region": region,
                            "error": result.get("_error", "Unknown error"),
                        }
                    )
            except Exception as e:
                errors.append({"region": region, "error": str(e)})

    return {
        "regions_queried": len(endpoints),
        "regions_successful": len(region_metrics),
        "regions": region_metrics,
        "errors": errors if errors else None,
    }


def aggregate_health() -> dict[str, Any]:
    """Aggregate health status from all regions."""
    endpoints = get_regional_endpoints()

    region_health: list[dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(query_region, region, endpoint, "/api/v1/health"): region
            for region, endpoint in endpoints.items()
        }

        for future in as_completed(futures):
            region = futures[future]
            try:
                result = future.result()
                if result.get("_status") == "success":
                    region_health.append(
                        {
                            "region": region,
                            "status": result.get("status", "unknown"),
                            "cluster_id": result.get("cluster_id"),
                            "kubernetes_api": result.get("kubernetes_api"),
                        }
                    )
                else:
                    region_health.append(
                        {
                            "region": region,
                            "status": "unreachable",
                            "error": result.get("_error"),
                        }
                    )
            except Exception as e:
                region_health.append(
                    {
                        "region": region,
                        "status": "error",
                        "error": str(e),
                    }
                )

    healthy_count = sum(1 for r in region_health if r["status"] == "healthy")
    overall_status = "healthy" if healthy_count == len(endpoints) else "degraded"
    if healthy_count == 0:
        overall_status = "unhealthy"

    return {
        "overall_status": overall_status,
        "healthy_regions": healthy_count,
        "total_regions": len(endpoints),
        "regions": region_health,
    }


def bulk_delete_jobs(
    namespace: str | None = None,
    status: str | None = None,
    older_than_days: int | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Bulk delete jobs across all regions."""
    endpoints = get_regional_endpoints()

    request_body: dict[str, Any] = {
        "dry_run": dry_run,
    }
    if namespace:
        request_body["namespace"] = namespace
    if status:
        request_body["status"] = status
    if older_than_days:
        request_body["older_than_days"] = older_than_days

    body_str = json.dumps(request_body)

    region_results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    total_deleted = 0
    total_matched = 0

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(
                query_region, region, endpoint, "/api/v1/jobs", "DELETE", body_str
            ): region
            for region, endpoint in endpoints.items()
        }

        for future in as_completed(futures):
            region = futures[future]
            try:
                result = future.result()
                if result.get("_status") == "success":
                    region_results.append(
                        {
                            "region": region,
                            "matched": result.get("total_matched", 0),
                            "deleted": result.get("deleted_count", 0),
                            "failed": result.get("failed_count", 0),
                        }
                    )
                    total_matched += result.get("total_matched", 0)
                    total_deleted += result.get("deleted_count", 0)
                else:
                    errors.append(
                        {
                            "region": region,
                            "error": result.get("_error", "Unknown error"),
                        }
                    )
            except Exception as e:
                errors.append({"region": region, "error": str(e)})

    return {
        "dry_run": dry_run,
        "total_matched": total_matched,
        "total_deleted": total_deleted,
        "regions_queried": len(endpoints),
        "region_results": region_results,
        "errors": errors if errors else None,
    }


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    Handle cross-region aggregation requests.

    Routes:
        GET /global/jobs - List jobs across all regions
        GET /global/health - Health status across all regions
        GET /global/status - Cluster status across all regions
        DELETE /global/jobs - Bulk delete across all regions
    """
    http_method = event.get("httpMethod", "GET")
    path = event.get("path", "")
    query_params = event.get("queryStringParameters") or {}
    body = event.get("body")

    try:
        # Route to appropriate handler
        if path == "/api/v1/global/jobs" and http_method == "GET":
            result = aggregate_jobs(
                namespace=query_params.get("namespace"),
                status=query_params.get("status"),
                limit=int(query_params.get("limit", "50")),
            )
        elif path == "/api/v1/global/jobs" and http_method == "DELETE":
            body_data = json.loads(body) if body else {}
            result = bulk_delete_jobs(
                namespace=body_data.get("namespace"),
                status=body_data.get("status"),
                older_than_days=body_data.get("older_than_days"),
                dry_run=body_data.get("dry_run", True),
            )
        elif path == "/api/v1/global/health":
            result = aggregate_health()
        elif path == "/api/v1/global/status":
            result = aggregate_metrics()
        else:
            return {
                "statusCode": 404,
                "body": json.dumps({"error": "Not found", "path": path}),
            }

        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(result),
        }

    except Exception as e:
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "Internal server error", "detail": str(e)}),
        }
