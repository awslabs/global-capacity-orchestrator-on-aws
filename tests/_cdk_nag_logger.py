"""
Custom ``INagLogger`` implementation that captures cdk-nag findings
into Python-side data structures for pytest assertions.

Why this exists
---------------
``cdk synth`` exit codes and stdout are unreliable signals for
cdk-nag compliance under our configuration. The Aspects we register
in ``app.py`` (``AwsSolutionsChecks`` + 4 more rule packs) emit
findings through the CDK Annotations system by default, which
``cdk synth --quiet`` suppresses entirely — and even without
``--quiet`` the exit code is zero. That leaked the ``AwsSolutions-IAM5``
finding on ``ServiceAccountRole`` past the CI matrix and surfaced it
to a user running ``cdk deploy`` in us-east-1 for the first time.

Routing findings through an additional ``INagLogger`` gives us a
deterministic, in-process hook: every non-suppressed finding calls
``on_non_compliance`` on our logger, we record it, and pytest
asserts the list is empty after ``app.synth()``. No subprocess, no
stdout parsing, no exit-code guessing.

How to use
----------

    from tests._cdk_nag_logger import CapturingCdkNagLogger

    logger = CapturingCdkNagLogger()
    cdk.Aspects.of(app).add(
        AwsSolutionsChecks(verbose=True, additional_loggers=[logger])
    )
    # ... build stacks ...
    app.synth()

    assert logger.findings == [], logger.format_findings()

The logger stores each non-compliance finding as a plain dict so it
survives past jsii proxy teardown. ``format_findings()`` produces a
human-readable multi-line summary suitable for a pytest assertion
message.

Why a separate module
---------------------
The JSII machinery that registers the ``@jsii.implements`` decorator
has to be applied at class definition time, not inside a test body,
or jsii throws a typecheck error. Keeping the class in its own
module also makes it easy to re-use across test files without
re-implementing the 6 ``on_*`` stubs.
"""

from __future__ import annotations

from typing import Any

import jsii
from cdk_nag import INagLogger


@jsii.implements(INagLogger)
class CapturingCdkNagLogger:
    """Collects non-compliance findings for pytest assertions.

    Attributes:
        findings: List of dicts, one per finding. Each dict has keys:
            ``rule_id`` (e.g. ``"AwsSolutions-IAM5"``),
            ``finding_id`` (cdk-nag's unique id for this occurrence),
            ``resource_path`` (the CDK construct path, e.g.
            ``"gco-us-east-1/ServiceAccountRole/DefaultPolicy/Resource"``),
            ``rule_info`` (a short human-readable description of why
            the rule fired), and ``rule_level`` (WARN or ERROR).

        errors: List of dicts for rules that threw while evaluating
            a CfnResource. In practice this is almost always a
            Token-resolution error at synth time and should be rare.
    """

    def __init__(self) -> None:
        self.findings: list[dict[str, Any]] = []
        self.errors: list[dict[str, Any]] = []

    # All six INagLogger methods must be implemented or jsii throws
    # at registration time. The methods we don't care about are
    # intentionally no-ops.

    @jsii.member(jsii_name="onCompliance")
    def on_compliance(
        self,
        *,
        nag_pack_name: str,
        resource: Any,
        rule_explanation: str,
        rule_id: str,
        rule_info: str,
        rule_level: Any,
        rule_original_name: str,
    ) -> None:
        pass

    @jsii.member(jsii_name="onError")
    def on_error(
        self,
        *,
        error_message: str,
        nag_pack_name: str,
        resource: Any,
        rule_explanation: str,
        rule_id: str,
        rule_info: str,
        rule_level: Any,
        rule_original_name: str,
    ) -> None:
        self.errors.append(
            {
                "rule_id": rule_id,
                "resource_path": resource.node.path,
                "error_message": error_message,
                "nag_pack_name": nag_pack_name,
            }
        )

    @jsii.member(jsii_name="onNonCompliance")
    def on_non_compliance(
        self,
        *,
        finding_id: str,
        nag_pack_name: str,
        resource: Any,
        rule_explanation: str,
        rule_id: str,
        rule_info: str,
        rule_level: Any,
        rule_original_name: str,
    ) -> None:
        self.findings.append(
            {
                "rule_id": rule_id,
                "finding_id": finding_id,
                "resource_path": resource.node.path,
                "rule_info": rule_info,
                "rule_level": str(rule_level),
                "nag_pack_name": nag_pack_name,
            }
        )

    @jsii.member(jsii_name="onNotApplicable")
    def on_not_applicable(
        self,
        *,
        nag_pack_name: str,
        resource: Any,
        rule_explanation: str,
        rule_id: str,
        rule_info: str,
        rule_level: Any,
        rule_original_name: str,
    ) -> None:
        pass

    @jsii.member(jsii_name="onSuppressed")
    def on_suppressed(
        self,
        *,
        suppression_reason: str,
        finding_id: str,
        nag_pack_name: str,
        resource: Any,
        rule_explanation: str,
        rule_id: str,
        rule_info: str,
        rule_level: Any,
        rule_original_name: str,
    ) -> None:
        pass

    @jsii.member(jsii_name="onSuppressedError")
    def on_suppressed_error(
        self,
        *,
        error_suppression_reason: str,
        error_message: str,
        nag_pack_name: str,
        resource: Any,
        rule_explanation: str,
        rule_id: str,
        rule_info: str,
        rule_level: Any,
        rule_original_name: str,
    ) -> None:
        pass

    def format_findings(self) -> str:
        """Return a multi-line human-readable summary of all captured
        findings and errors, suitable for use as a pytest assertion
        message."""
        lines: list[str] = []
        if self.findings:
            lines.append(f"Captured {len(self.findings)} unsuppressed cdk-nag finding(s):")
            # Sort by (pack, rule, path) so the output order is
            # deterministic — makes diffs easier to read if multiple
            # findings appear across runs.
            for f in sorted(
                self.findings,
                key=lambda x: (x["nag_pack_name"], x["rule_id"], x["resource_path"]),
            ):
                lines.append(f"  [{f['nag_pack_name']}] {f['rule_id']} " f"at {f['resource_path']}")
                # Truncate very long rule_info strings — the rule id
                # is what you'll cross-reference with cdk-nag's docs
                # and the path is what tells you where to fix it.
                info = f["rule_info"]
                if len(info) > 200:
                    info = info[:197] + "..."
                lines.append(f"    -> {info}")
        if self.errors:
            if lines:
                lines.append("")
            lines.append(f"Captured {len(self.errors)} rule evaluation error(s):")
            for e in self.errors:
                lines.append(f"  {e['rule_id']} at {e['resource_path']}: " f"{e['error_message']}")
        if not lines:
            return "(no findings)"
        return "\n".join(lines)
