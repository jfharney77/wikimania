# Wikimania Critique & Spec — #4

## Criticism

**The wikilink graph and concept deduplication both use a hard alphabetical top-N cap, causing systematic quality degradation that worsens with every upload.**

Two constants govern wiki quality as the article count grows, and both are broken in the same way:

**Cap 1 — concept deduplication (`pipeline.py:197–198`)**

```python
existing_titles = await db.list_article_titles(wiki_id)
titles_preview = ", ".join(existing_titles[:50]) or "none yet"
```

`list_article_titles` returns titles in `ORDER BY title` (alphabetical), so only the first 50 are shown to the LLM in `EXTRACT_CONCEPTS_PROMPT`. The prompt instructs the model to "only return concepts NOT already well-covered," but it can only avoid duplicating articles it knows about. For a wiki with 200 articles, the 150 articles with titles starting beyond the 50th alphabetical position are invisible. Every subsequent upload re-extracts those concepts as "new," triggering a redundant call to the reasoning model (the most expensive operation in the pipeline) for each one.

**Cap 2 — wikilink context (`pipeline.py:129`)**

```python
all_titles = await db.list_article_titles(wiki_id)
related = ", ".join(all_titles[:80])
```

The same alphabetically-ordered list is truncated at 80 titles and passed identically to every article in the batch. `WRITE_ARTICLE_PROMPT` instructs the LLM to "Only link to concepts from the provided list." This has two concrete consequences:

1. **Alphabetical exclusion**: Any article whose title ranks past position 80 alphabetically can never appear as a wikilink target from newly written articles. In a 150-article wiki, articles with titles starting roughly N–Z are permanent orphans in the knowledge graph — they have edges going out but can never receive inbound links from future uploads regardless of topical relevance.

2. **Uniform, irrelevant context**: An article about "Machine Learning" and an article about "Roman Architecture" both receive the same 80-title blob. The LLM is asked to select relevant wikilinks from a list that is mostly noise, producing spurious cross-links (alphabetically early titles get cited regardless of relevance) while missing genuinely related concepts that happen to fall outside the cap.

Both caps share the same root cause: the function `list_article_titles` is called once, sorted alphabetically, and sliced — with no concept-specific filtering and no fallback when the wiki exceeds the cap size.

**Concrete failure modes (demonstrable at scale)**

| Wiki size | Effect of cap 1 | Effect of cap 2 |
|-----------|-----------------|-----------------|
| < 50 articles | No impact | Minor: a few relevant titles excluded |
| 50–100 articles | Articles 51–100 re-extracted on every upload | Articles 81–100 can never receive inbound links |
| 200+ articles | Most articles re-extracted every upload; each triggers a reasoning-model call (~$0.01–0.05 per article at paid rates) | Bottom 60% of articles alphabetically are permanent graph orphans |

The sample documents (`reinforcement_learning.md`, `graphify.md`) each generate 15–25 articles, keeping the wiki under 50 total and masking both problems during development. The failure mode is invisible until the wiki accumulates more than 50 articles across multiple uploads.

---

## Spec

### Goal

Concept deduplication and wikilink context must scale to arbitrarily large wikis without alphabetical bias. Every article title must be eligible for deduplication and cross-linking regardless of its alphabetical rank.

### Approach

**Phase 1 — Fix concept deduplication**

Replace the hard `:50` slice with a two-stage approach:

1. Extract raw concept titles from the document (same as today, no existing-titles context).
2. After extraction, filter the raw list against the full `all_titles` set in Python — an exact case-insensitive membership check. Titles that already exist are discarded before the writing phase begins.

```python
existing_set = {t.lower() for t in await db.list_article_titles(wiki_id)}
concepts = [c for c in raw_concepts if c.lower() not in existing_set]
```

This eliminates the LLM's role in deduplication entirely: the model generates candidate concepts freely, and the pipeline discards any that already exist. The existing-titles context in `EXTRACT_CONCEPTS_PROMPT` can be removed — it was only there to prevent the LLM from re-proposing known titles, but doing that check in Python is exact, free, and scales to any wiki size.

**Phase 2 — Fix wikilink context per article**

Replace the single global `related = ", ".join(all_titles[:80])` with a per-concept relevance query:

```python
def _relevant_titles(concept: str, all_titles: list[str], top_k: int = 40) -> str:
    words = set(re.split(r'\W+', concept.lower())) - {'the', 'a', 'an', 'of', 'in'}
    scored = []
    for t in all_titles:
        t_words = set(re.split(r'\W+', t.lower()))
        scored.append((len(words & t_words), t))
    scored.sort(key=lambda x: -x[0])
    top = [t for _, t in scored[:top_k]]
    if concept not in top:
        top.append(concept)  # always include the article's own title for self-awareness
    return ", ".join(top)
```

Call this function inside `_call_one` or pass the result per-concept from `_write_articles`. `all_titles` is fetched once per batch (not once per article) to avoid N+1 queries.

The `top_k=40` default is deliberately smaller than the current 80 — the resulting list is denser with relevant titles, which produces more accurate cross-links than a larger list of mostly-irrelevant ones. `top_k` should be configurable via environment variable.

**Phase 3 — Remove the hard cap from `EXTRACT_CONCEPTS_PROMPT`**

Delete the `{existing_titles}` substitution from `EXTRACT_CONCEPTS_PROMPT` and the `titles_preview` variable in `generate_wiki`. The prompt is simplified and the Python-side deduplication in Phase 1 takes over correctness.

### Specific file changes

| File | Change |
|------|--------|
| `backend/pipeline.py` | Remove `titles_preview` and `existing_titles[:50]` from `generate_wiki`; add Python-side deduplication after concept extraction; add `_relevant_titles(concept, all_titles)` helper; update `_write_articles` to pass `all_titles` and compute per-concept related string; update `_call_one` signature to accept `related: str` (no change) or compute it internally |
| `backend/pipeline_lg.py` | Apply the same deduplication and `_relevant_titles` logic to `node_extract_concepts` and `node_write_articles` |
| `backend/pipeline.py` (`EXTRACT_CONCEPTS_PROMPT`) | Remove the `{existing_titles}` slot and the "Existing wiki articles (avoid duplicates…)" instruction block |

No database schema changes. No API changes. No frontend changes.

### Acceptance criteria

1. Upload document A (25 concepts). Upload document B (30 concepts, 10 of which overlap with A). The second upload's `concepts` SSE event contains at most 20 titles; the 10 overlapping concepts do not appear regardless of their alphabetical rank.
2. Build a wiki with 120 articles whose titles span A–Z. Upload a new document. The resulting articles contain wikilinks to titles from the full alphabet range, not only the first 80 alphabetically.
3. Two articles on unrelated topics (e.g., "Roman Architecture" and "Gradient Descent") produce different `related_titles` strings — they share no more than 5 common entries.
4. The total number of reasoning-model calls for a document that produces 0 net-new concepts equals 0 (all filtered before writing). Today it equals the number of re-extracted existing concepts.
5. `list_article_titles` is called at most once per `generate_wiki` invocation, not once per article (no N+1 regression).
