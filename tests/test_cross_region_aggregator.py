"""
Tests for the cross-region aggregator Lambda (lambda/cross-region-aggregator).

Covers the full request pipeline: Secrets Manager token fetch with
in-memory caching, SSM-based regional endpoint discovery, per-region
HTTP queries via urllib3, and the higher-level aggregate_* helpers
that merge job lists, health status, metrics, and bulk-delete results
across every discovered region. Also drives the API Gateway Lambda
handler surface.

The ``lambda/`` directory isn't on the normal import path, so the
handler is loaded by file path under a unique ``sys.modules`` name
via :func:`tests._lambda_imports.load_lambda_module`. That avoids
the ``sys.path.insert('lambda/foo') + import handler`` pattern used
elsewhere, which would otherwise collide with other Lambda handler
tests running in the same pytest session.
"""

import json
from unittest.mock import MagicMock, patch

from tests._lambda_imports import load_lambda_module

handler = load_lambda_module("cross-region-aggregator")


class TestGetSecretToken:
    """Tests for get_secret_token function."""

    def test_get_secret_token_success(self):
        """Test successful secret retrieval."""

        get_secret_token = handler.get_secret_token

        # Reset cache
        handler._cached_secret = None

        mock_secrets = MagicMock()
        mock_secrets.get_secret_value.return_value = {
            "SecretString": json.dumps({"token": "test-token-123"})
        }

        with (
            patch.dict(
                "os.environ",
                {"SECRET_ARN": "arn:aws:secretsmanager:us-east-1:123456789012:secret:test"},
            ),
            patch.object(handler, "secrets_client", mock_secrets),
        ):
            token = get_secret_token()
            assert (
                token == "test-token-123"
            )  # nosec B105 - test assertion against fixture value, not a real credential

    def test_get_secret_token_cached(self):
        """Test that secret is cached."""

        handler._cached_secret = "cached-token"  # nosec B105 - test fixture, not a real credential

        token = handler.get_secret_token()
        assert (
            token == "cached-token"
        )  # nosec B105 - test assertion against fixture value, not a real credential


class TestGetRegionalEndpoints:
    """Tests for get_regional_endpoints function."""

    def test_get_regional_endpoints_success(self):
        """Test successful endpoint retrieval from SSM."""

        handler._cached_endpoints = None

        mock_ssm = MagicMock()
        mock_paginator = MagicMock()
        mock_paginator.paginate.return_value = [
            {
                "Parameters": [
                    {
                        "Name": "/gco/alb-hostname-us-east-1",
                        "Value": "alb-us-east-1.example.com",
                    },
                    {
                        "Name": "/gco/alb-hostname-us-west-2",
                        "Value": "alb-us-west-2.example.com",
                    },
                ]
            }
        ]
        mock_ssm.get_paginator.return_value = mock_paginator

        with (
            patch.dict("os.environ", {"PROJECT_NAME": "gco", "GLOBAL_REGION": "us-east-2"}),
            patch("boto3.client", return_value=mock_ssm),
        ):
            endpoints = handler.get_regional_endpoints()
            assert "us-east-1" in endpoints
            assert "us-west-2" in endpoints
            assert endpoints["us-east-1"] == "alb-us-east-1.example.com"
            assert endpoints["us-west-2"] == "alb-us-west-2.example.com"

    def test_get_regional_endpoints_empty(self):
        """Test empty endpoints when no SSM parameters found."""

        handler._cached_endpoints = None

        mock_ssm = MagicMock()
        mock_paginator = MagicMock()
        mock_paginator.paginate.return_value = [{"Parameters": []}]
        mock_ssm.get_paginator.return_value = mock_paginator

        with (
            patch.dict("os.environ", {"PROJECT_NAME": "gco", "GLOBAL_REGION": "us-east-2"}),
            patch("boto3.client", return_value=mock_ssm),
        ):
            endpoints = handler.get_regional_endpoints()
            assert endpoints == {}

    def test_get_regional_endpoints_ssm_error(self):
        """Test graceful handling of SSM errors."""

        handler._cached_endpoints = None

        mock_ssm = MagicMock()
        mock_ssm.get_paginator.side_effect = Exception("SSM access denied")

        with (
            patch.dict("os.environ", {"PROJECT_NAME": "gco", "GLOBAL_REGION": "us-east-2"}),
            patch("boto3.client", return_value=mock_ssm),
        ):
            endpoints = handler.get_regional_endpoints()
            # Should return empty dict on error, not raise
            assert endpoints == {}

    def test_get_regional_endpoints_cached(self):
        """Test that endpoints are cached."""

        handler._cached_endpoints = {
            "us-east-1": "cached-alb.example.com",
        }

        # Should return cached value without calling SSM
        endpoints = handler.get_regional_endpoints()
        assert endpoints == {"us-east-1": "cached-alb.example.com"}


class TestQueryRegion:
    """Tests for query_region function."""

    def test_query_region_success(self):
        """Test successful region query."""

        handler._cached_secret = "test-token"  # nosec B105 - test fixture, not a real credential

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.data = json.dumps({"jobs": [], "count": 0}).encode("utf-8")

        mock_http = MagicMock()
        mock_http.request.return_value = mock_response

        with patch.object(handler, "http", mock_http):
            result = handler.query_region(
                "us-east-1",
                "alb.example.com",
                "/api/v1/jobs",
                "GET",
            )

            assert result["_region"] == "us-east-1"
            assert result["_status"] == "success"

    def test_query_region_with_query_params(self):
        """Test region query with query parameters."""

        handler._cached_secret = "test-token"  # nosec B105 - test fixture, not a real credential

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.data = json.dumps({"jobs": []}).encode("utf-8")

        mock_http = MagicMock()
        mock_http.request.return_value = mock_response

        with patch.object(handler, "http", mock_http):
            handler.query_region(
                "us-east-1",
                "alb.example.com",
                "/api/v1/jobs",
                "GET",
                query_params={"namespace": "default", "status": "running"},
            )

            call_args = mock_http.request.call_args
            url = call_args[0][1]
            assert "namespace=default" in url
            assert "status=running" in url

    def test_query_region_error(self):
        """Test region query with HTTP error."""

        handler._cached_secret = "test-token"  # nosec B105 - test fixture, not a real credential

        mock_response = MagicMock()
        mock_response.status = 500

        mock_http = MagicMock()
        mock_http.request.return_value = mock_response

        with patch.object(handler, "http", mock_http):
            result = handler.query_region(
                "us-east-1",
                "alb.example.com",
                "/api/v1/jobs",
            )

            assert result["_status"] == "error"
            assert "HTTP 500" in result["_error"]

    def test_query_region_exception(self):
        """Test region query with exception."""

        handler._cached_secret = "test-token"  # nosec B105 - test fixture, not a real credential

        mock_http = MagicMock()
        mock_http.request.side_effect = Exception("Connection timeout")

        with patch.object(handler, "http", mock_http):
            result = handler.query_region(
                "us-east-1",
                "alb.example.com",
                "/api/v1/jobs",
            )

            assert result["_status"] == "error"
            assert "Connection timeout" in result["_error"]


class TestAggregateJobs:
    """Tests for aggregate_jobs function."""

    def test_aggregate_jobs_success(self):
        """Test successful job aggregation."""

        handler._cached_endpoints = {
            "us-east-1": "alb-us-east-1.example.com",
            "us-west-2": "alb-us-west-2.example.com",
        }
        handler._cached_secret = "test-token"  # nosec B105 - test fixture, not a real credential

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.data = json.dumps(
            {
                "jobs": [
                    {"metadata": {"name": "job1", "creationTimestamp": "2024-01-15T10:00:00Z"}},
                ],
                "count": 1,
                "total": 1,
            }
        ).encode("utf-8")

        mock_http = MagicMock()
        mock_http.request.return_value = mock_response

        with patch.object(handler, "http", mock_http):
            result = handler.aggregate_jobs(namespace="default", limit=10)

            assert result["regions_queried"] == 2
            assert result["regions_successful"] == 2
            assert len(result["jobs"]) == 2  # One from each region

    def test_aggregate_jobs_with_errors(self):
        """Test job aggregation with some region errors."""

        handler._cached_endpoints = {
            "us-east-1": "alb-us-east-1.example.com",
            "us-west-2": "alb-us-west-2.example.com",
        }
        handler._cached_secret = "test-token"  # nosec B105 - test fixture, not a real credential

        def mock_request(*args, **kwargs):
            url = args[1]
            if "us-east-1" in url:
                response = MagicMock()
                response.status = 200
                response.data = json.dumps({"jobs": [], "count": 0, "total": 0}).encode("utf-8")
                return response
            else:
                response = MagicMock()
                response.status = 500
                return response

        mock_http = MagicMock()
        mock_http.request.side_effect = mock_request

        with patch.object(handler, "http", mock_http):
            result = handler.aggregate_jobs()

            assert result["regions_queried"] == 2
            assert result["regions_successful"] == 1
            assert result["errors"] is not None


class TestAggregateHealth:
    """Tests for aggregate_health function."""

    def test_aggregate_health_all_healthy(self):
        """Test health aggregation when all regions are healthy."""

        handler._cached_endpoints = {
            "us-east-1": "alb-us-east-1.example.com",
            "us-west-2": "alb-us-west-2.example.com",
        }
        handler._cached_secret = "test-token"  # nosec B105 - test fixture, not a real credential

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.data = json.dumps(
            {
                "status": "healthy",
                "cluster_id": "gco-cluster",
                "kubernetes_api": "healthy",
            }
        ).encode("utf-8")

        mock_http = MagicMock()
        mock_http.request.return_value = mock_response

        with patch.object(handler, "http", mock_http):
            result = handler.aggregate_health()

            assert result["overall_status"] == "healthy"
            assert result["healthy_regions"] == 2
            assert result["total_regions"] == 2

    def test_aggregate_health_degraded(self):
        """Test health aggregation when some regions are unhealthy."""

        handler._cached_endpoints = {
            "us-east-1": "alb-us-east-1.example.com",
            "us-west-2": "alb-us-west-2.example.com",
        }
        handler._cached_secret = "test-token"  # nosec B105 - test fixture, not a real credential

        call_count = [0]

        def mock_request(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                response = MagicMock()
                response.status = 200
                response.data = json.dumps({"status": "healthy"}).encode("utf-8")
                return response
            else:
                response = MagicMock()
                response.status = 500
                return response

        mock_http = MagicMock()
        mock_http.request.side_effect = mock_request

        with patch.object(handler, "http", mock_http):
            result = handler.aggregate_health()

            assert result["overall_status"] == "degraded"
            assert result["healthy_regions"] == 1


class TestAggregateMetrics:
    """Tests for aggregate_metrics function."""

    def test_aggregate_metrics_success(self):
        """Test successful metrics aggregation."""

        handler._cached_endpoints = {
            "us-east-1": "alb-us-east-1.example.com",
        }
        handler._cached_secret = "test-token"  # nosec B105 - test fixture, not a real credential

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.data = json.dumps(
            {
                "cluster_id": "gco-us-east-1",
                "templates_count": 5,
                "webhooks_count": 2,
            }
        ).encode("utf-8")

        mock_http = MagicMock()
        mock_http.request.return_value = mock_response

        with patch.object(handler, "http", mock_http):
            result = handler.aggregate_metrics()

            assert result["regions_queried"] == 1
            assert result["regions_successful"] == 1
            assert len(result["regions"]) == 1


class TestBulkDeleteJobs:
    """Tests for bulk_delete_jobs function."""

    def test_bulk_delete_jobs_dry_run(self):
        """Test bulk delete with dry run."""

        handler._cached_endpoints = {
            "us-east-1": "alb-us-east-1.example.com",
        }
        handler._cached_secret = "test-token"  # nosec B105 - test fixture, not a real credential

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.data = json.dumps(
            {
                "total_matched": 5,
                "deleted_count": 0,
                "failed_count": 0,
            }
        ).encode("utf-8")

        mock_http = MagicMock()
        mock_http.request.return_value = mock_response

        with patch.object(handler, "http", mock_http):
            result = handler.bulk_delete_jobs(
                namespace="default",
                status="completed",
                older_than_days=7,
                dry_run=True,
            )

            assert result["dry_run"] is True
            assert result["total_matched"] == 5
            assert result["total_deleted"] == 0


class TestLambdaHandler:
    """Tests for lambda_handler function."""

    def test_handler_get_jobs(self):
        """Test handler for GET /global/jobs."""

        handler._cached_endpoints = {"us-east-1": "alb.example.com"}
        handler._cached_secret = "test-token"  # nosec B105 - test fixture, not a real credential

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.data = json.dumps({"jobs": [], "count": 0, "total": 0}).encode("utf-8")

        mock_http = MagicMock()
        mock_http.request.return_value = mock_response

        with patch.object(handler, "http", mock_http):
            event = {
                "httpMethod": "GET",
                "path": "/api/v1/global/jobs",
                "queryStringParameters": {"namespace": "default"},
            }

            result = handler.lambda_handler(event, None)

            assert result["statusCode"] == 200
            body = json.loads(result["body"])
            assert "jobs" in body

    def test_handler_get_health(self):
        """Test handler for GET /global/health."""

        handler._cached_endpoints = {"us-east-1": "alb.example.com"}
        handler._cached_secret = "test-token"  # nosec B105 - test fixture, not a real credential

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.data = json.dumps({"status": "healthy"}).encode("utf-8")

        mock_http = MagicMock()
        mock_http.request.return_value = mock_response

        with patch.object(handler, "http", mock_http):
            event = {
                "httpMethod": "GET",
                "path": "/api/v1/global/health",
            }

            result = handler.lambda_handler(event, None)

            assert result["statusCode"] == 200
            body = json.loads(result["body"])
            assert "overall_status" in body

    def test_handler_get_status(self):
        """Test handler for GET /global/status."""

        handler._cached_endpoints = {"us-east-1": "alb.example.com"}
        handler._cached_secret = "test-token"  # nosec B105 - test fixture, not a real credential

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.data = json.dumps({"cluster_id": "test"}).encode("utf-8")

        mock_http = MagicMock()
        mock_http.request.return_value = mock_response

        with patch.object(handler, "http", mock_http):
            event = {
                "httpMethod": "GET",
                "path": "/api/v1/global/status",
            }

            result = handler.lambda_handler(event, None)

            assert result["statusCode"] == 200

    def test_handler_delete_jobs(self):
        """Test handler for DELETE /global/jobs."""

        handler._cached_endpoints = {"us-east-1": "alb.example.com"}
        handler._cached_secret = "test-token"  # nosec B105 - test fixture, not a real credential

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.data = json.dumps(
            {
                "total_matched": 3,
                "deleted_count": 0,
            }
        ).encode("utf-8")

        mock_http = MagicMock()
        mock_http.request.return_value = mock_response

        with patch.object(handler, "http", mock_http):
            event = {
                "httpMethod": "DELETE",
                "path": "/api/v1/global/jobs",
                "body": json.dumps({"dry_run": True, "status": "completed"}),
            }

            result = handler.lambda_handler(event, None)

            assert result["statusCode"] == 200

    def test_handler_not_found(self):
        """Test handler for unknown path."""

        event = {
            "httpMethod": "GET",
            "path": "/api/v1/unknown",
        }

        result = handler.lambda_handler(event, None)

        assert result["statusCode"] == 404

    def test_handler_error(self):
        """Test handler error handling."""

        # Force an error by making aggregate_jobs raise an exception
        with patch.object(handler, "aggregate_jobs", side_effect=Exception("Test error")):
            event = {
                "httpMethod": "GET",
                "path": "/api/v1/global/jobs",
            }

            result = handler.lambda_handler(event, None)

            assert result["statusCode"] == 500
            body = json.loads(result["body"])
            assert "error" in body
