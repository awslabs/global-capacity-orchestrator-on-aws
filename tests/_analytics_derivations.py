"""Template-to-toggle derivation helpers for the round-trip property test.

Two pure helpers that recover the ``analytics_environment.*`` toggle
state from the synthesized CloudFormation templates:

* :func:`derive_enabled_from_templates` — True iff any template contains
  an ``AWS::SageMaker::Domain`` resource. When the toggle is off the
  analytics stack is never instantiated, so no Studio domain is emitted.

* :func:`derive_hyperpod_from_templates` — True iff the analytics stack's
  ``SageMaker_Execution_Role`` carries any IAM action matching the
  HyperPod grant shape (``sagemaker:CreateTrainingJob`` or any
  ``sagemaker:ClusterInstance*`` action). The HyperPod branch in
  ``GCOAnalyticsStack._create_execution_role_and_grants`` only emits
  those actions when ``analytics_environment.hyperpod.enabled=true``.

The two helpers together close the round-trip: for any
``(enabled, hyperpod_enabled)`` input, synthesizing the full app and
then calling both derivers on the resulting templates recovers the
input tuple exactly.
"""

from __future__ import annotations

import fnmatch
from typing import Any

from tests._analytics_template_inspectors import get_sagemaker_role_actions

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def derive_enabled_from_templates(
    templates: dict[str, dict[str, Any]],
) -> bool:
    """Return True iff any template contains an ``AWS::SageMaker::Domain``.

    The Studio domain is created only by
    ``GCOAnalyticsStack._create_studio_domain`` and the analytics stack
    is only instantiated when ``analytics_environment.enabled=true``, so
    its presence is a reliable proxy for the toggle.

    Args:
        templates: ``{stack_name: template_dict}`` as returned by
            :func:`tests._analytics_cdk_overlays.synth_all_stacks`.

    Returns:
        ``True`` if at least one template in the dict has a resource of
        type ``AWS::SageMaker::Domain``; ``False`` otherwise.
    """
    for template in templates.values():
        resources = template.get("Resources", {}) or {}
        for res in resources.values():
            if isinstance(res, dict) and res.get("Type") == "AWS::SageMaker::Domain":
                return True
    return False


# The two action patterns that the HyperPod branch in
# ``_create_execution_role_and_grants`` adds. Kept as a module-level
# constant so the round-trip deriver callers can import it for
# diagnostic messages.
HYPERPOD_ACTION_PATTERNS: tuple[str, ...] = (
    "sagemaker:CreateTrainingJob",
    "sagemaker:ClusterInstance*",
)


def derive_hyperpod_from_templates(
    templates: dict[str, dict[str, Any]],
) -> bool:
    """Return True iff the analytics stack's SageMaker execution role has
    a HyperPod-specific IAM action.

    Walks every template in ``templates`` (HyperPod grants only appear on
    the analytics stack, but the deriver is liberal and scans all stacks
    so a future refactor can't silently stop the derivation working).
    Each template's SageMaker execution role is extracted via
    :func:`tests._analytics_template_inspectors.get_sagemaker_role_actions`
    and the action list is matched against
    :data:`HYPERPOD_ACTION_PATTERNS` using ``fnmatch`` so the
    ``sagemaker:ClusterInstance*`` wildcard matches any specific action
    the implementation grants (``sagemaker:ClusterInstanceAssign``,
    ``sagemaker:ClusterInstanceList``, etc.).

    Args:
        templates: ``{stack_name: template_dict}`` as returned by
            :func:`tests._analytics_cdk_overlays.synth_all_stacks`.

    Returns:
        ``True`` if at least one SageMaker execution role statement
        carries a HyperPod-shaped action; ``False`` otherwise. Also
        returns ``False`` when the analytics stack is absent (no
        matching role to extract actions from).
    """
    for template in templates.values():
        actions = get_sagemaker_role_actions(template)
        for action in actions:
            for pattern in HYPERPOD_ACTION_PATTERNS:
                if fnmatch.fnmatch(action, pattern):
                    return True
    return False
