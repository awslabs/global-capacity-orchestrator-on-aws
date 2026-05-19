"""Documentation discovery MCP tool.

Wraps the ``DOC_METADATA`` catalog defined in ``mcp/resources/docs.py`` and
exposes a single ``find_docs`` tool the LLM can call with a free-text
query plus an optional topic filter. Scoring is a deterministic weighted
sum of topic and summary/name substring matches; results are sorted by
score descending then name ascending so callers iterating with ``limit``
always see a stable ordering.
"""

from audit import audit_logged
from resources.docs import DOC_METADATA
from server import mcp


def _search(query: str | None, topic: str | None) -> list[tuple[str, int]]:
    """Filter and score docs; return ``[(name, score), ...]`` sorted desc."""
    results: list[tuple[str, int]] = []
    q = query.lower() if query else None
    t = topic.lower() if topic else None
    for name, meta in DOC_METADATA.items():
        score = 0
        if t:
            topics = meta.get("topics", [])
            if isinstance(topics, list):
                for top in topics:
                    if t in str(top).lower():
                        score += 3
            # Topic filter is a hard constraint — no match means drop the
            # entry, even if a query string would have matched the summary.
            if score == 0:
                continue
        if q:
            # Keyword matches are the strongest free-text signal — every
            # entry's ``keywords`` list is curated to surface terms a
            # user is likely to search for (e.g. "vllm", "odcr",
            # "global accelerator") even when those phrases don't appear
            # verbatim in the summary.
            keywords = meta.get("keywords", [])
            if isinstance(keywords, list):
                for kw in keywords:
                    if q in str(kw).lower():
                        score += 4
            summary = str(meta.get("summary", "")).lower()
            if q in summary:
                score += 1
            if q in name.lower():
                score += 1
            # When the only signal is a query and it didn't hit, drop it.
            if score == 0 and not t:
                continue
        results.append((name, score))
    results.sort(key=lambda x: (-x[1], x[0]))
    return results


def _format(name: str) -> dict[str, object]:
    """Format a metadata entry for the tool response."""
    meta = DOC_METADATA.get(name, {})
    return {
        "name": name,
        "summary": meta.get("summary", ""),
        "topics": meta.get("topics", []),
        "keywords": meta.get("keywords", []),
        "related": meta.get("related", []),
    }


@mcp.tool(tags={"safe", "docs"})
@audit_logged
async def find_docs(
    query: str | None = None,
    topic: str | None = None,
    limit: int = 10,
) -> list[dict[str, object]]:
    """`find_docs` — search the docs/ catalog by topic and free-text query.

    Args:
        query: Free-text query matched against the doc's keywords, summary,
            and name (case-insensitive substring match).
        topic: Filter by topic substring (case-insensitive). Acts as a hard
            filter — entries without a topic match are dropped.
        limit: Maximum results (default 10). ``limit <= 0`` returns ``[]``.

    Scoring: topic substring matches contribute 3 pts each; keyword
    substring matches contribute 4 pts each; summary/name substring
    matches contribute 1 pt each. Returns the top ``limit`` matches
    sorted by score descending then name ascending.
    """
    if limit <= 0:
        return []
    no_filters = not query and not topic
    if no_filters:
        # Stable alpha-sorted listing for the no-arg case.
        names = sorted(DOC_METADATA.keys())[:limit]
        return [_format(name) for name in names]
    matches = _search(query, topic)
    return [_format(name) for name, _score in matches[:limit]]
