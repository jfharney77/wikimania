"""Shortest path between two nodes in a graph snapshot.

Pure, dependency-free BFS over a graphify snapshot (NetworkX ``node_link_data``
shape). Edges are treated as undirected — "are these two concepts related?" does
not depend on which article cited which. Consistent with ``graph_eval``:
self-loops and dangling endpoints are ignored.

See specs/SPEC_GRAPH_SHORTEST_PATH.md.
"""

import re
import unicodedata
from collections import deque

_WS = re.compile(r"\s+")


def canonical_key(text: str) -> str:
    """Case/punctuation/diacritic-insensitive key for matching node labels."""
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^a-z0-9]+", " ", t.lower())
    return _WS.sub(" ", t).strip()


def _links_key(graph: dict) -> str:
    return "links" if "links" in graph else "edges"


def _build_adjacency(graph: dict):
    node_ids = [n["id"] for n in graph.get("nodes") or []]
    id_set = set(node_ids)
    adj: dict = {nid: set() for nid in node_ids}
    for link in graph.get(_links_key(graph)) or []:
        s, t = link.get("source"), link.get("target")
        if s not in id_set or t not in id_set or s == t:
            continue
        adj[s].add(t)
        adj[t].add(s)
    return adj


def resolve_node(graph: dict, ref: str) -> str | None:
    """Resolve a node reference to a node id.

    Accepts either an exact node id (e.g. "article_12") or a node label matched
    case/punctuation-insensitively via ``canonical_key``. Returns the id or None.
    """
    if ref is None:
        return None
    nodes = graph.get("nodes") or []
    ids = {n["id"] for n in nodes}
    if ref in ids:
        return ref
    key = canonical_key(ref)
    if not key:
        return None
    for n in nodes:
        if canonical_key(n.get("label", "")) == key:
            return n["id"]
    return None


def shortest_path(graph: dict, source_id: str, target_id: str) -> dict:
    """Fewest-hops path between two node ids (BFS, undirected, unweighted).

    Returns:
      {
        "found": bool,
        "length": int | None,          # number of edges (0 when source == target)
        "nodes": [{"id", "label"}, ...],
        "edges": [{"source", "target", "relation"}, ...],
      }
    Assumes source_id and target_id are valid node ids (resolve them first).
    """
    labels = {n["id"]: n.get("label", n["id"]) for n in graph.get("nodes") or []}

    def node_obj(nid):
        return {"id": nid, "label": labels.get(nid, nid)}

    if source_id == target_id:
        return {"found": True, "length": 0, "nodes": [node_obj(source_id)], "edges": []}

    adj = _build_adjacency(graph)

    # BFS with parent pointers for path reconstruction.
    parents = {source_id: None}
    queue = deque([source_id])
    while queue:
        cur = queue.popleft()
        if cur == target_id:
            break
        for nb in adj.get(cur, ()):  # undirected neighbours
            if nb not in parents:
                parents[nb] = cur
                queue.append(nb)

    if target_id not in parents:
        return {"found": False, "length": None, "nodes": [], "edges": []}

    # Reconstruct path source -> target.
    path = []
    cur = target_id
    while cur is not None:
        path.append(cur)
        cur = parents[cur]
    path.reverse()

    # Relation labels per traversed edge (best-effort; undirected lookup).
    rel = {}
    for link in graph.get(_links_key(graph)) or []:
        s, t = link.get("source"), link.get("target")
        rel[(s, t)] = link.get("relation", "")
        rel[(t, s)] = link.get("relation", "")

    edges = [
        {"source": a, "target": b, "relation": rel.get((a, b), "")}
        for a, b in zip(path, path[1:])
    ]

    return {
        "found": True,
        "length": len(path) - 1,
        "nodes": [node_obj(nid) for nid in path],
        "edges": edges,
    }
