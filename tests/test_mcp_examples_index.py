"""
Tests for the GCO MCP server's example-discovery surface.

Covers ``EXAMPLE_METADATA`` consistency (every metadata key resolves to a
real ``examples/*.yaml`` file and every YAML file has a metadata entry),
the ``related`` reference closure, the ``find_examples`` tool's keyword
matching and edge cases (no-arg listing, non-positive limits), and the
two new ``docs://gco/examples/by-category/...`` and
``docs://gco/examples/by-use-case/...`` resource paths.
"""

import asyncio
import sys
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

# Ensure mcp/ is importable
sys.path.insert(0, str(Path(__file__).parent.parent / "mcp"))

import run_mcp  # noqa: E402, F401  -- side effect: registers tools and resources
from resources.docs import EXAMPLE_METADATA, EXAMPLES_DIR  # noqa: E402
from tools.examples import find_examples  # noqa: E402

# The shared FastMCP instance with everything registered. Pulled from
# ``run_mcp`` rather than ``server`` because importing ``server`` alone
# leaves the resource handlers unregistered.
mcp = run_mcp.mcp


# =============================================================================
# EXAMPLE_METADATA structural invariants (Tasks 7.7, 7.8)
# =============================================================================


@settings(max_examples=100, derandomize=True)
@given(name=st.sampled_from(sorted(EXAMPLE_METADATA.keys())))
def test_every_example_has_yaml_property(name: str) -> None:
    """Every metadata key points at a real YAML file under examples/."""
    assert (EXAMPLES_DIR / f"{name}.yaml").is_file(), f"missing examples/{name}.yaml"


def test_metadata_keys_match_yaml_files() -> None:
    """Symmetric check: every YAML file has a metadata entry, and vice versa."""
    yaml_names = {f.stem for f in EXAMPLES_DIR.glob("*.yaml")}
    metadata_names = set(EXAMPLE_METADATA.keys())
    assert yaml_names == metadata_names, (
        f"Metadata/YAML mismatch — only in YAML: {yaml_names - metadata_names}, "
        f"only in metadata: {metadata_names - yaml_names}"
    )


def test_every_related_reference_resolves() -> None:
    """Every entry in any ``related`` list must itself be a key in EXAMPLE_METADATA."""
    keys = set(EXAMPLE_METADATA.keys())
    for name, meta in EXAMPLE_METADATA.items():
        related = meta.get("related", [])
        assert isinstance(related, list), f"{name!r}.related must be a list"
        for ref in related:
            assert ref in keys, f"{name!r}.related references unknown {ref!r}"


# =============================================================================
# find_examples behavior (Tasks 7.9, 7.10)
# =============================================================================


@settings(max_examples=200)
@given(data=st.data())
def test_keyword_match_property(data: st.DataObject) -> None:
    """If a keyword exists in any example's keywords list, querying that
    keyword returns the example.
    """
    candidates = [
        name
        for name, meta in EXAMPLE_METADATA.items()
        if isinstance(meta.get("keywords", []), list) and meta.get("keywords")
    ]
    if not candidates:
        return  # Nothing to test
    name = data.draw(st.sampled_from(candidates))
    keywords = EXAMPLE_METADATA[name]["keywords"]
    assert isinstance(keywords, list)
    keyword = data.draw(st.sampled_from(keywords))

    results = asyncio.run(find_examples(query=str(keyword)))
    result_names = [r["name"] for r in results]
    assert name in result_names, f"querying {keyword!r} did not return {name!r}"


def test_find_examples_no_args_returns_alpha_sorted_first_limit() -> None:
    """With no filters and no query, the catalog is alpha-sorted and clipped."""
    results = asyncio.run(find_examples(limit=5))
    assert len(results) == 5
    names = [r["name"] for r in results]
    assert names == sorted(EXAMPLE_METADATA.keys())[:5]


def test_find_examples_negative_limit_returns_empty() -> None:
    """``limit <= 0`` short-circuits to an empty list."""
    assert asyncio.run(find_examples(limit=-1)) == []
    assert asyncio.run(find_examples(limit=0)) == []


# =============================================================================
# Resource paths (Task 7.11)
# =============================================================================


def test_examples_by_category_unknown_returns_available_list() -> None:
    """Unknown category returns the literal "Category 'X' not found." string."""
    result = asyncio.run(mcp.read_resource("docs://gco/examples/by-category/nonexistent"))
    content = result.contents[0].content
    assert "not found" in content
    assert "Available:" in content


def test_examples_by_use_case_no_match_suggests_find_examples() -> None:
    """Unknown use_case returns a guiding pointer to ``find_examples``."""
    result = asyncio.run(
        mcp.read_resource("docs://gco/examples/by-use-case/totally-bogus-use-case")
    )
    content = result.contents[0].content
    assert "No examples match use case" in content
    assert "find_examples" in content
