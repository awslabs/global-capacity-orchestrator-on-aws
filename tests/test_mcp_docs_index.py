"""
Tests for the GCO MCP server's docs-discovery surface.

Covers ``DOC_METADATA`` consistency (every metadata key resolves to a
real ``docs/*.md`` file and every markdown file has a metadata entry),
the ``related`` reference closure, the ``find_docs`` tool's topic
matching and no-arg behaviour, and the two new
``docs://gco/docs/by-topic/...`` and ``docs://gco/docs/by-related/...``
resource paths.
"""

import asyncio
import sys
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

# Ensure mcp/ is importable
sys.path.insert(0, str(Path(__file__).parent.parent / "mcp"))

import run_mcp  # noqa: E402, F401  -- side effect: registers tools and resources
from resources.docs import DOC_METADATA, DOCS_DIR  # noqa: E402
from tools.docs import find_docs  # noqa: E402

# Pull the shared FastMCP instance with everything registered from
# ``run_mcp`` rather than ``server`` because importing ``server`` alone
# leaves the resource handlers unregistered.
mcp = run_mcp.mcp


# =============================================================================
# DOC_METADATA structural invariants
# =============================================================================


@settings(max_examples=100, derandomize=True)
@given(name=st.sampled_from(sorted(DOC_METADATA.keys())))
def test_every_doc_has_md_file_property(name: str) -> None:
    """Every metadata key points at a real markdown file under docs/."""
    assert (DOCS_DIR / f"{name}.md").is_file(), f"missing docs/{name}.md"


def test_metadata_keys_match_md_files() -> None:
    """Symmetric check: every .md file has a metadata entry, and vice versa."""
    md_names = {f.stem for f in DOCS_DIR.glob("*.md")}
    metadata_names = set(DOC_METADATA.keys())
    assert md_names == metadata_names, (
        f"Metadata/markdown mismatch — only in markdown: {md_names - metadata_names}, "
        f"only in metadata: {metadata_names - md_names}"
    )


def test_every_doc_related_reference_resolves() -> None:
    """Every entry in any ``related`` list must itself be a key in DOC_METADATA."""
    keys = set(DOC_METADATA.keys())
    for name, meta in DOC_METADATA.items():
        related = meta.get("related", [])
        assert isinstance(related, list), f"{name!r}.related must be a list"
        for ref in related:
            assert ref in keys, f"{name!r}.related references unknown {ref!r}"


# =============================================================================
# find_docs behaviour
# =============================================================================


@settings(max_examples=200)
@given(data=st.data())
def test_topic_match_property(data: st.DataObject) -> None:
    """If a topic exists in any doc's topics list, querying that topic
    returns the doc.
    """
    candidates = [
        name
        for name, meta in DOC_METADATA.items()
        if isinstance(meta.get("topics", []), list) and meta.get("topics")
    ]
    if not candidates:
        return  # Nothing to test
    name = data.draw(st.sampled_from(candidates))
    topics = DOC_METADATA[name]["topics"]
    assert isinstance(topics, list)
    topic = data.draw(st.sampled_from(topics))

    results = asyncio.run(find_docs(topic=str(topic)))
    result_names = [r["name"] for r in results]
    assert name in result_names, f"querying topic {topic!r} did not return {name!r}"


def test_find_docs_no_args_returns_alpha_sorted_first_limit() -> None:
    """With no filters and no query, the catalog is alpha-sorted and clipped."""
    results = asyncio.run(find_docs(limit=5))
    assert len(results) == 5
    names = [r["name"] for r in results]
    assert names == sorted(DOC_METADATA.keys())[:5]


# =============================================================================
# Resource paths
# =============================================================================


def test_docs_by_topic_unknown_returns_available_list() -> None:
    """Unknown topic returns the literal "Topic 'X' not found." string."""
    result = asyncio.run(mcp.read_resource("docs://gco/docs/by-topic/nonexistent"))
    content = result.contents[0].content
    assert "not found" in content
    assert "Available:" in content


def test_docs_by_related_unknown_returns_available_list() -> None:
    """Unknown doc name returns the literal "Doc 'X' not found." string."""
    result = asyncio.run(mcp.read_resource("docs://gco/docs/by-related/nonexistent"))
    content = result.contents[0].content
    assert "not found" in content
    assert "Available:" in content
