"""Synthesis checks for the configurable regional-shared bucket retention (W5).

``cdk.json::regional_shared_bucket.removal_policy`` decides what a regional
stack destroy does to the always-on general-purpose bucket, its access-logs
bucket, and its KMS key:

* ``destroy`` (default, today's behavior): all three delete with the stack
  and the two buckets auto-delete their objects first;
* ``retain``: all three survive the destroy — a retained bucket whose key
  was scheduled for deletion would be undecryptable, so they share a fate;
* anything else fails synthesis loudly.

Reuses the context-driven synth helper from the workload-metrics tests and
the bucket/key locators from the regional bucket synthesis tests.
"""

from __future__ import annotations

from functools import cache
from typing import Any

import pytest

from tests.test_mooncake_regional_bucket_synthesis import (
    _regional_bucket_logical_id,
    _regional_key_logical_id,
)
from tests.test_workload_metrics_grant import _synthesize


@cache
def _default_template() -> dict[str, Any]:
    return _synthesize()


@cache
def _retain_template() -> dict[str, Any]:
    return _synthesize({"regional_shared_bucket": {"removal_policy": "retain"}})


def _access_logs_logical_id(template: dict[str, Any], bucket_id: str) -> str:
    """The access-logs bucket, located from the primary bucket's logging config."""
    logging_config = template["Resources"][bucket_id]["Properties"]["LoggingConfiguration"]
    destination = logging_config["DestinationBucketName"]
    assert isinstance(destination, dict) and "Ref" in destination
    return destination["Ref"]


def _auto_delete_targets(template: dict[str, Any]) -> set[str]:
    """Logical ids of every bucket wired to the auto-delete custom resource."""
    targets: set[str] = set()
    for resource in template.get("Resources", {}).values():
        if resource.get("Type") != "Custom::S3AutoDeleteObjects":
            continue
        bucket_ref = resource.get("Properties", {}).get("BucketName")
        if isinstance(bucket_ref, dict) and "Ref" in bucket_ref:
            targets.add(bucket_ref["Ref"])
    return targets


class TestRegionalSharedBucketRetention:
    def test_default_destroy_matches_historical_teardown(self) -> None:
        template = _default_template()
        bucket_id = _regional_bucket_logical_id(template)
        logs_id = _access_logs_logical_id(template, bucket_id)
        key_id = _regional_key_logical_id(template, bucket_id)
        resources = template["Resources"]

        assert resources[bucket_id]["DeletionPolicy"] == "Delete"
        assert resources[logs_id]["DeletionPolicy"] == "Delete"
        assert resources[key_id]["DeletionPolicy"] == "Delete"
        auto_delete = _auto_delete_targets(template)
        assert bucket_id in auto_delete
        assert logs_id in auto_delete

    def test_retain_lets_bucket_logs_and_key_outlive_the_region(self) -> None:
        template = _retain_template()
        bucket_id = _regional_bucket_logical_id(template)
        logs_id = _access_logs_logical_id(template, bucket_id)
        key_id = _regional_key_logical_id(template, bucket_id)
        resources = template["Resources"]

        assert resources[bucket_id]["DeletionPolicy"] == "Retain"
        assert resources[bucket_id]["UpdateReplacePolicy"] == "Retain"
        assert resources[logs_id]["DeletionPolicy"] == "Retain"
        assert resources[key_id]["DeletionPolicy"] == "Retain"
        auto_delete = _auto_delete_targets(template)
        assert bucket_id not in auto_delete
        assert logs_id not in auto_delete

    def test_retain_changes_nothing_else_about_the_bucket(self) -> None:
        """Retention is a teardown decision, not a security posture change."""
        template = _retain_template()
        bucket_id = _regional_bucket_logical_id(template)
        properties = template["Resources"][bucket_id]["Properties"]

        assert properties["PublicAccessBlockConfiguration"] == {
            "BlockPublicAcls": True,
            "BlockPublicPolicy": True,
            "IgnorePublicAcls": True,
            "RestrictPublicBuckets": True,
        }
        assert properties["VersioningConfiguration"] == {"Status": "Enabled"}

    def test_invalid_policy_fails_synthesis(self) -> None:
        with pytest.raises(ValueError, match="removal_policy must be 'destroy' or 'retain'"):
            _synthesize({"regional_shared_bucket": {"removal_policy": "keep-forever"}})
