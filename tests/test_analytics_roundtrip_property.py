"""Property-based test for the analytics toggle round-trip.

*For any* ``(enabled, hyperpod_enabled) ∈ {true, false}²``, synthesizing
the full CDK app with that toggle pair and then recovering
``(enabled', hyperpod_enabled')`` by inspecting the resulting templates
SHALL recover the ``enabled`` bit exactly and, **when ``enabled=True``**,
the ``hyperpod_enabled`` bit as well.

  * If ``enabled=True``:  ``(enabled', hyperpod_enabled') == (True, hyperpod_enabled)``
  * If ``enabled=False``: ``enabled' == False`` — ``hyperpod_enabled`` is
    unobservable because the analytics stack is never instantiated, so
    the HyperPod grant can't possibly be present. This matches the
    semantics documented in ``gco/config/config_loader.py`` (the
    ``hyperpod`` sub-toggle is a no-op unless the master toggle is on).

The deriver functions live in :mod:`tests._analytics_derivations`:

* :func:`~tests._analytics_derivations.derive_enabled_from_templates` —
  returns True iff any template contains an ``AWS::SageMaker::Domain``.
* :func:`~tests._analytics_derivations.derive_hyperpod_from_templates` —
  returns True iff the analytics stack's SageMaker execution role carries
  any action matching ``sagemaker:CreateTrainingJob`` or
  ``sagemaker:ClusterInstance*``.

Cardinality of the input space is 4, so ``max_examples=4`` is already
exhaustive. Hypothesis is used for its boilerplate-free replay /
shrinking machinery, not for input coverage.
"""

from __future__ import annotations

import functools
import json
from typing import Any

from hypothesis import HealthCheck, given, settings

from tests._analytics_cdk_overlays import build_overlay, synth_all_stacks
from tests._analytics_derivations import (
    HYPERPOD_ACTION_PATTERNS,
    derive_enabled_from_templates,
    derive_hyperpod_from_templates,
)
from tests._analytics_strategies import roundtrip_fixtures

# ---------------------------------------------------------------------------
# Cached synth — one entry per unique (enabled, hyperpod) tuple.
# ---------------------------------------------------------------------------

_REGIONS_FIXED: list[str] = ["us-east-1"]


@functools.cache
def _cached_templates_frozen(enabled: bool, hyperpod_enabled: bool) -> tuple[tuple[str, str], ...]:
    """Return every (stack_name, JSON-serialized template) pair for the
    given toggle tuple as a hashable structure.

    Serializing the template dicts via ``json.dumps`` + a sorted-key
    ordering makes the cache entry hashable; callers materialize it
    back into a ``{stack_name: template_dict}`` dict on every use so
    mutations don't bleed between examples.
    """
    overlay = build_overlay(enabled, hyperpod_enabled, _REGIONS_FIXED)
    templates = synth_all_stacks(overlay)
    return tuple(
        (name, json.dumps(template, sort_keys=True)) for name, template in templates.items()
    )


def _unfreeze(
    frozen: tuple[tuple[str, str], ...],
) -> dict[str, dict[str, Any]]:
    """Rebuild a ``{stack_name: template_dict}`` from the cache entry."""
    return {name: json.loads(blob) for name, blob in frozen}


# ---------------------------------------------------------------------------
# Round-trip property.
# ---------------------------------------------------------------------------


class TestToggleRoundTrip:
    """The ``(enabled, hyperpod_enabled)`` tuple round-trips through
    full-app synth and template inspection.
    """

    @classmethod
    def setup_class(cls) -> None:
        """Pre-warm the cache for all 4 points of the input space.

        This is exhaustive: ``_cached_templates_frozen`` is deterministic
        in its two boolean arguments, so 2×2=4 cache entries cover
        every possible Hypothesis draw.
        """
        for enabled in (False, True):
            for hyperpod in (False, True):
                _cached_templates_frozen(enabled, hyperpod)

    @settings(
        max_examples=4,
        deadline=10000,
        suppress_health_check=[
            HealthCheck.too_slow,
            HealthCheck.function_scoped_fixture,
            HealthCheck.data_too_large,
        ],
    )
    @given(fixture=roundtrip_fixtures)
    def test_round_trip_recovers_original_tuple(self, fixture: tuple[bool, bool]) -> None:
        """Synthesizing with ``(enabled, hyperpod_enabled)`` and then
        deriving the tuple back from the templates recovers the exact
        original.
        """
        enabled, hyperpod_enabled = fixture
        templates = _unfreeze(_cached_templates_frozen(enabled, hyperpod_enabled))

        enabled_derived = derive_enabled_from_templates(templates)
        hyperpod_derived = derive_hyperpod_from_templates(templates)

        assert enabled_derived == enabled, (
            f"Round-trip failed on ``enabled``: expected "
            f"{enabled!r} (input), got {enabled_derived!r} (derived). "
            f"Input tuple was (enabled={enabled}, hyperpod={hyperpod_enabled}). "
            f"The deriver looks for AWS::SageMaker::Domain — check "
            f"whether the analytics stack was (not) instantiated as "
            f"expected in app.py / synth_all_stacks."
        )

        # The ``hyperpod`` sub-toggle is only observable when the master
        # toggle is on (the analytics stack carries the SageMaker
        # execution role that encodes HyperPod-shaped actions). When
        # ``enabled=False`` the analytics stack is never instantiated,
        # so the HyperPod grant can't possibly be present — the deriver
        # returns False regardless of the input ``hyperpod_enabled``.
        # This matches ``ConfigLoader.get_analytics_config``'s documented
        # semantics (the sub-toggle is moot under the master off).
        if enabled:
            assert hyperpod_derived == hyperpod_enabled, (
                f"Round-trip failed on ``hyperpod_enabled`` with "
                f"``enabled=True``: expected {hyperpod_enabled!r} "
                f"(input), got {hyperpod_derived!r} (derived). Input "
                f"tuple was (enabled={enabled}, "
                f"hyperpod={hyperpod_enabled}). The deriver matches "
                f"actions against {HYPERPOD_ACTION_PATTERNS!r}; check "
                f"whether the HyperPod branch of "
                f"_create_execution_role_and_grants was (not) taken "
                f"as expected."
            )
        else:
            assert hyperpod_derived is False, (
                f"Invariant under ``enabled=False``: the "
                f"HyperPod grant must not appear anywhere in the "
                f"synthesized app (the analytics stack is not "
                f"instantiated). Got hyperpod_derived={hyperpod_derived!r} "
                f"for input hyperpod_enabled={hyperpod_enabled!r}."
            )


# ---------------------------------------------------------------------------
# Smoke tests on each deriver in isolation — catches template-shape
# regressions without waiting on a full Hypothesis run.
# ---------------------------------------------------------------------------


class TestDerivers:
    """Deriver-level smoke tests pinned to the four concrete input
    tuples — fails fast if template shape drifts away from what the
    derivers expect.
    """

    def test_enabled_false_hyperpod_false(self) -> None:
        templates = _unfreeze(_cached_templates_frozen(False, False))
        assert derive_enabled_from_templates(templates) is False
        assert derive_hyperpod_from_templates(templates) is False

    def test_enabled_true_hyperpod_false(self) -> None:
        templates = _unfreeze(_cached_templates_frozen(True, False))
        assert derive_enabled_from_templates(templates) is True
        assert derive_hyperpod_from_templates(templates) is False

    def test_enabled_true_hyperpod_true(self) -> None:
        templates = _unfreeze(_cached_templates_frozen(True, True))
        assert derive_enabled_from_templates(templates) is True
        assert derive_hyperpod_from_templates(templates) is True

    def test_enabled_false_hyperpod_true_has_no_analytics_effect(self) -> None:
        # When analytics is disabled the HyperPod sub-toggle is a no-op
        # (the analytics stack isn't instantiated, so there's no role to
        # decorate). The deriver must read False for both.
        templates = _unfreeze(_cached_templates_frozen(False, True))
        assert derive_enabled_from_templates(templates) is False
        assert derive_hyperpod_from_templates(templates) is False
