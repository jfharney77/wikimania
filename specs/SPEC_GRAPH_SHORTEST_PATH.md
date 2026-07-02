# SPEC: Graph Shortest Path Between Two Nodes

**Branch:** `feature/graph-eval`
**Status:** Implemented
**Date:** 2026-06-28 (implemented 2026-07-01)

> Implementation note: `graph_path.py` inlines its own `canonical_key` (the
> `titles.py` module referenced below lives on the separate `feature/duplicate-fix`
> branch and isn't present here). Behaviour is equivalent for label matching.

## Problem

Users can see the knowledge graph and (after `SPEC_GRAPH_EVAL.md`) its overall
health, but there is no way to ask *how* two concepts relate. Given two articles
— e.g. "Gradient Descent" and "Reinforcement Learning" — there is no way to see
the chain of wikilinks that connects them, or to learn that they are not
connected at all. A shortest-path query exposes the conceptual "distance" and the
bridging articles between any two nodes.

## Goal

Given two nodes in the latest graph snapshot, return the **shortest path** (fewest
hops) between them as an ordered list of nodes and the edges traversed, plus the
path length. Surface it in the Graph tab by letting the user pick a source and
target and highlighting the resulting path.

## Data model

Same snapshot as `SPEC_GRAPH_EVAL.md`: `nodes[].id` / `nodes[].label` and
`links[].source` / `links[].target`. Nodes are addressed by `id` (e.g.
`article_12`). The API should also accept a node **label** (case-insensitively,
reusing `titles.canonical_key`) for convenience, resolving it to an id.

## Design

### Backend: `backend/graph_path.py`

Pure, dependency-free helper:

```python
def shortest_path(graph: dict, source_id: str, target_id: str) -> dict
```

- Build an **undirected** adjacency map (consistent with `graph_eval`: ignore
  self-loops and dangling edges). Treating edges as undirected matches user
  intent ("are these related?") regardless of which article cited which.
- Run breadth-first search from `source_id` to `target_id` (unweighted → BFS
  gives the fewest-hops path). Reconstruct the path via a parent map.
- Return:
  ```json
  {
    "found": true,
    "length": 2,
    "nodes": [{"id": "article_12", "label": "Gradient Descent"}, ...],
    "edges": [{"source": "...", "target": "...", "relation": "references"}]
  }
  ```
  When no path exists: `{ "found": false, "length": null, "nodes": [], "edges": [] }`.
  When source or target id is unknown: HTTP 404 with a clear message.
- Edge cases: `source == target` returns a single-node path of length 0.

A resolver `resolve_node(graph, ref) -> str | None` maps an id or a label
(via `canonical_key`) to a node id, so the API can accept either.

### API: `GET /api/wikis/{wiki_id}/graph/path`

Query params: `source` and `target` (each an id or a label). Returns
`{ "path": <result> }`, or `{ "path": null, "message": ... }` when no graph
exists. 404 if a ref cannot be resolved. Auth via `get_current_user`.

Example: `GET /api/wikis/1/graph/path?source=Gradient%20Descent&target=Reinforcement%20Learning`

### Frontend: `Graph.jsx`

- Add two searchable selects (source / target) populated from the loaded node
  labels, plus a "Find path" button.
- On result, dim non-path nodes/edges and highlight the path nodes and the edges
  between them; show a small breadcrumb of the path labels and the hop count.
  If `found: false`, show "No path — these concepts are not connected."
- A "Clear" control restores the normal view.

## Algorithm notes

- BFS is O(V + E) and sufficient; edges are unweighted (one hop = one wikilink).
- If weighted distance is ever wanted (e.g. by `confidence_score`), swap BFS for
  Dijkstra — out of scope for v1.
- Build adjacency per request from the snapshot JSON; no precomputation or schema
  change needed. Revisit with caching only if snapshots get large.

## Out of scope (v1)

- All-pairs distances / graph diameter.
- K-shortest-paths or alternative routes.
- Weighted/directed pathfinding.

## Verification

- Unit checks on `shortest_path`: direct neighbors (length 1), multi-hop chain,
  disconnected pair (`found: false`), `source == target` (length 0), and
  unknown-id handling.
- Manual: pick two connected concepts, confirm the highlighted path matches the
  visible wikilink chain; pick two articles in different components, confirm the
  "not connected" message.
