# SPEC: Graph Evaluation — Connectivity & Orphan Rate

**Branch:** `feature/graph-eval`
**Status:** Implemented
**Date:** 2026-06-28

## Problem

The knowledge graph (graphify snapshot, served from `graph_snapshots`) has no
quantitative health signal. Users can *see* the force-directed graph but can't
tell at a glance whether it is well-connected or littered with orphan articles —
pages that have no links in or out and float disconnected from every cluster.
Orphans are exactly the symptom produced by upstream issues (e.g. unresolved
wikilinks); a score makes graph health measurable and trackable over time.

## Goal

Compute and surface a small set of objective graph-health metrics, headlined by:

- **Connectivity score** (0–100) — how mutually reachable / cohesive the graph is.
- **Orphan rate** — fraction of nodes with no edges.

Plus supporting metrics (components, largest cluster, average degree, density)
and a letter grade for quick reading.

## Data model

The latest snapshot is stored as JSON in `graph_snapshots.graph_json` and has the
NetworkX `node_link_data` shape:

```json
{
  "nodes": [{ "id": "article_12", "label": "Machine Learning", "community": 0 }],
  "links": [{ "source": "article_12", "target": "article_7", "relation": "references" }]
}
```

(`links` is the canonical key; the evaluator also accepts `edges` defensively.)

## Design

### Backend: `backend/graph_eval.py`

Pure, dependency-free `evaluate_graph(graph: dict) -> dict`. Builds an
**undirected** adjacency map, ignoring self-loops and edges whose endpoints are
not in the node set. Computes:

| metric | definition |
|--------|------------|
| `node_count`, `edge_count` | counts (edges after dropping self-loops/dangling) |
| `orphans`, `orphan_rate` | nodes with degree 0; `orphans / node_count` |
| `components` | connected components (each isolated node is its own component) |
| `largest_component_size`, `largest_component_fraction` | size / fraction of the biggest component |
| `avg_degree` | `2 * edges / nodes` |
| `density` | `2 * edges / (nodes * (nodes - 1))` |
| `connectivity_score` | `100 * (0.5 * reach + 0.5 * cohesion)` |
| `grade` | A ≥90, B ≥75, C ≥60, D ≥40, else F |

where `reach = largest_component_fraction` and
`cohesion = 1 - (components - 1)/(nodes - 1)`. Both terms are 1.0 for a single
fully-connected graph and trend to 0 as the graph shatters into isolated nodes,
so the score rewards *both* a dominant connected core and few fragments.

Components are found with iterative BFS (no recursion-depth risk on large
graphs). Edge cases: an empty graph returns all-zero metrics with a `message`
and `grade: "N/A"`; a single-node graph has `cohesion = 1`.

### API: `GET /api/wikis/{wiki_id}/graph/eval`

Reads the latest snapshot, returns `{ "eval": <metrics> }`, or
`{ "eval": null, "message": ... }` when no graph exists yet. Auth via the same
`get_current_user` dependency as the other graph endpoints.

### Frontend: `Graph.jsx`

On wiki load, fetch the eval alongside the graph (best-effort — failures are
swallowed). Render a compact panel in the **top-left** of the graph canvas (the
legend stays top-right) showing the grade badge, connectivity score, orphan
count/rate, component count, largest-cluster %, and average degree.

## Out of scope

- Per-node orphan drill-down / "fix orphans" actions.
- Shortest-path / reachability queries — see `SPEC_GRAPH_SHORTEST_PATH.md`.
- Directed-graph metrics (the relation edges are treated as undirected here).

## Verification

- Unit checks on `evaluate_graph` for: fully-connected triangle (score 100, A),
  mixed connected+orphan graph (orphan_rate 0.5, 3 components), empty graph,
  and self-loop/dangling-edge inputs (both ignored).
- Manual: open the Graph tab; confirm the eval panel renders and the score moves
  as documents are added.
