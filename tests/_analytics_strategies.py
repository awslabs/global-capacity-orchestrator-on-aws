"""Shared Hypothesis strategies for the analytics-environment property tests.

Centralizes the Hypothesis strategies used by the analytics correctness
property tests:

* ``bucket_isolation_fixtures``    — ``tests/test_analytics_bucket_isolation_property.py``
* ``sagemaker_grant_toggle_fixtures`` — ``tests/test_analytics_configmap_property.py``
* ``roundtrip_fixtures``            — ``tests/test_analytics_roundtrip_property.py``
* ``cognito_username_strategy`` / ``cognito_claims_payload_strategy`` —
  shared fixtures for the presigned-URL Lambda property tests.

The strategies are deliberately small-cardinality (2 booleans, 4-point
region sample) because every example drives a full ``cdk.App().synth()``
pass — the cost per example is measured in seconds, so the strategies
are sized for exhaustive or near-exhaustive coverage of the input
space rather than stress-testing.
"""

from __future__ import annotations

from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Candidate regional regions used by the bucket-isolation and
# cluster-shared-ConfigMap property tests.
# ---------------------------------------------------------------------------

# The four regions that the regional-stack fixture is known to synthesize
# cleanly under a ``MockConfigLoader``.
CANDIDATE_REGIONS: list[str] = [
    "us-east-1",
    "us-west-2",
    "eu-west-1",
    "ap-southeast-1",
]


# ---------------------------------------------------------------------------
# Primitive strategies (exposed by name so callers can compose them).
# ---------------------------------------------------------------------------

# ``analytics_environment.enabled`` — the master toggle.
enabled_strategy = st.booleans()

# ``analytics_environment.hyperpod.enabled`` — the HyperPod sub-toggle.
hyperpod_enabled_strategy = st.booleans()

# ``deployment_regions.regional`` — 1–3 unique regions drawn from the
# ``CANDIDATE_REGIONS`` list. Size is capped at 3 to keep the per-example
# synth runtime bounded (every region in the list costs one regional
# stack's worth of CDK time).
regional_regions_strategy = st.lists(
    st.sampled_from(CANDIDATE_REGIONS),
    min_size=1,
    max_size=3,
    unique=True,
)


# ---------------------------------------------------------------------------
# Bucket isolation population.
# ---------------------------------------------------------------------------

# (enabled, hyperpod_enabled, regional_regions) — exercises the regional
# job-pod role's S3 grants across the full 2×2×region-list strategy space.
bucket_isolation_fixtures = st.tuples(
    enabled_strategy,
    hyperpod_enabled_strategy,
    regional_regions_strategy,
)


# ---------------------------------------------------------------------------
# SageMaker-grant-toggle invariant population.
# ---------------------------------------------------------------------------

# Just the analytics toggle — the biconditional is
# ``has_sagemaker_grant_on_cluster_shared == enabled``. Cardinality is 2, so
# ``max_examples=4`` in the test driver is already exhaustive twice over.
sagemaker_grant_toggle_fixtures = st.booleans()


# ---------------------------------------------------------------------------
# Bucket round-trip population.
# ---------------------------------------------------------------------------

# (enabled, hyperpod_enabled) — the full 2×2 toggle space. Cardinality is
# 4, so ``max_examples=4`` in the test driver is exhaustive.
roundtrip_fixtures = st.tuples(st.booleans(), st.booleans())


# ---------------------------------------------------------------------------
# Cognito claim strategies (presigned-URL Lambda property tests).
# ---------------------------------------------------------------------------

# A Cognito username — alphanumeric + ``-``/``_``/``.`` per the Cognito
# documented allowed character set, 1–128 chars. We stay away from
# whitespace and high-codepoint unicode so the strategy doesn't exercise
# Lambda-event serialization quirks unrelated to the property under test.
cognito_username_strategy = st.text(
    alphabet=st.characters(
        min_codepoint=0x20,
        max_codepoint=0x7E,
        whitelist_categories=("Lu", "Ll", "Nd"),
        whitelist_characters="-_.",
    ),
    min_size=1,
    max_size=128,
)


# An API-Gateway-proxied Cognito claims payload — the shape the
# presigned-URL Lambda actually sees under a Cognito authorizer on the
# ``/studio/login`` route. Only the fields the handler reads are
# generated (username, email, groups). Other claim fields are omitted
# because the handler's property-test assertions are scoped to the
# fields it actually consumes.
cognito_claims_payload_strategy = st.fixed_dictionaries(
    {
        "requestContext": st.fixed_dictionaries(
            {
                "authorizer": st.fixed_dictionaries(
                    {
                        "claims": st.fixed_dictionaries(
                            {
                                "cognito:username": cognito_username_strategy,
                                "email": st.emails(),
                                "cognito:groups": st.one_of(
                                    st.just(""),
                                    st.lists(
                                        st.text(
                                            alphabet=st.characters(
                                                whitelist_categories=(
                                                    "Lu",
                                                    "Ll",
                                                    "Nd",
                                                ),
                                            ),
                                            min_size=1,
                                            max_size=32,
                                        ),
                                        min_size=0,
                                        max_size=4,
                                    ).map(lambda xs: ",".join(xs)),
                                ),
                            }
                        ),
                    }
                ),
            }
        ),
    }
)
