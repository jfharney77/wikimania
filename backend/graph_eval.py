"""Knowledge-graph health metrics.

Pure, dependency-free evaluation of a graphify snapshot (NetworkX
``node_link_data`` shape). Scores how well-connected the wiki graph is and how
many articles are orphaned (no links in or out).

See specs/SPEC_GRAPH_EVAL.md.
"""


def _links_key(graph: dict) -> str:
    return "links" if "links" in graph else "edges"


def evaluate_graph(graph: dict) -> dict:
    """Compute connectivity / orphan metrics for a graph snapshot.

    Returns a dict with:
      - node_count, edge_count
      - orphans, orphan_rate            (isolated nodes / nodes)
      - components                      (connected components, isolated nodes count as one each)
      - largest_component_size, largest_component_fraction
      - avg_degree, density
      - connectivity_score              (0-100 composite, see below)
      - grade                           (A-F bucket of connectivity_score)

    connectivity_score = 100 * (0.5 * reach + 0.5 * cohesion) where
      reach   = largest_component_fraction  (how much of the graph is mutually reachable)
      cohesion = 1 - (components - 1)/(nodes - 1)  (penalizes fragmentation)
    Both terms are 1.0 for a single fully-connected graph and trend to 0 as the
    graph shatters into isolated nodes.
    """
    nodes = graph.get("nodes") or []
    node_ids = [n["id"] for n in nodes]
    id_set = set(node_ids)
    n = len(node_ids)

    # Adjacency (undirected, self-loops and dangling endpoints ignored).
    adj: dict = {nid: set() for nid in node_ids}
    edge_count = 0
    for link in graph.get(_links_key(graph)) or []:
        s, t = link.get("source"), link.get("target")
        if s not in id_set or t not in id_set or s == t:
            continue
        adj[s].add(t)
        adj[t].add(s)
        edge_count += 1

    if n == 0:
        return {
            "node_count": 0, "edge_count": 0,
            "orphans": 0, "orphan_rate": 0.0,
            "components": 0,
            "largest_component_size": 0, "largest_component_fraction": 0.0,
            "avg_degree": 0.0, "density": 0.0,
            "connectivity_score": 0.0, "grade": "N/A",
            "message": "Graph is empty — upload a document first.",
        }

    orphans = [nid for nid in node_ids if not adj[nid]]
    orphan_rate = len(orphans) / n

    # Connected components via iterative BFS.
    seen: set = set()
    component_sizes = []
    for start in node_ids:
        if start in seen:
            continue
        size = 0
        stack = [start]
        seen.add(start)
        while stack:
            cur = stack.pop()
            size += 1
            for nb in adj[cur]:
                if nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        component_sizes.append(size)

    components = len(component_sizes)
    largest = max(component_sizes)
    largest_fraction = largest / n

    avg_degree = (2 * edge_count) / n
    density = (2 * edge_count) / (n * (n - 1)) if n > 1 else 0.0

    reach = largest_fraction
    cohesion = 1.0 - (components - 1) / (n - 1) if n > 1 else 1.0
    connectivity_score = round(100 * (0.5 * reach + 0.5 * cohesion), 1)

    return {
        "node_count": n,
        "edge_count": edge_count,
        "orphans": len(orphans),
        "orphan_rate": round(orphan_rate, 4),
        "components": components,
        "largest_component_size": largest,
        "largest_component_fraction": round(largest_fraction, 4),
        "avg_degree": round(avg_degree, 3),
        "density": round(density, 5),
        "connectivity_score": connectivity_score,
        "grade": _grade(connectivity_score),
    }


def _grade(score: float) -> str:
    if score >= 90:
        return "A"
    if score >= 75:
        return "B"
    if score >= 60:
        return "C"
    if score >= 40:
        return "D"
    return "F"
