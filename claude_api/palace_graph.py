"""Agentic Daisy -- Palace graph traversal.

Builds a navigable graph over ``MemoryStore`` records:

- **Nodes** are rooms (block names).
- **Edges** connect rooms that appear in more than one wing (chip/design).
  These cross-wing edges are the "tunnels" -- ideas shared between designs.

Lifted and adapted from ``milla-jovovich/mempalace``'s ``palace_graph.py``
(MIT licensed). The only substantive change is swapping the ChromaDB data
source for iteration over ``MemoryStore._memories`` so the module has zero
external dependencies.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any, Dict, List, Tuple


def _iter_records(memory_store):
    """Yield dict records out of a MemoryStore. Supports both the in-memory
    list on the object and a manual override for tests.
    """
    return list(memory_store._memories)  # noqa: SLF001


def build_graph(memory_store) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]:
    """Return ``(nodes, edges)`` built from every namespaced memory record."""
    room_data: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {
            "wings": set(),
            "halls": set(),
            "count": 0,
            "dates": set(),
        }
    )

    for mem in _iter_records(memory_store):
        room = mem.get("room", "")
        wing = mem.get("wing", "")
        hall = mem.get("hall", "")
        created = mem.get("created", "")
        if not room or not wing:
            continue
        room_data[room]["wings"].add(wing)
        if hall:
            room_data[room]["halls"].add(hall)
        if created:
            # Keep just the date portion
            room_data[room]["dates"].add(created[:10])
        room_data[room]["count"] += 1

    edges: List[Dict[str, Any]] = []
    for room, data in room_data.items():
        wings = sorted(data["wings"])
        if len(wings) < 2:
            continue
        halls = sorted(data["halls"]) or [""]
        for i, wa in enumerate(wings):
            for wb in wings[i + 1:]:
                for hall in halls:
                    edges.append({
                        "room": room,
                        "wing_a": wa,
                        "wing_b": wb,
                        "hall": hall,
                        "count": data["count"],
                    })

    nodes: Dict[str, Dict[str, Any]] = {}
    for room, data in room_data.items():
        nodes[room] = {
            "wings": sorted(data["wings"]),
            "halls": sorted(data["halls"]),
            "count": data["count"],
            "dates": sorted(data["dates"])[-5:] if data["dates"] else [],
        }

    return nodes, edges


def traverse(memory_store, start_room: str, max_hops: int = 2) -> str:
    """BFS over shared-wing edges starting from *start_room*."""
    nodes, _ = build_graph(memory_store)

    if start_room not in nodes:
        return json.dumps({
            "status": "not_found",
            "start_room": start_room,
            "suggestions": _fuzzy_match(start_room, nodes),
        })

    start = nodes[start_room]
    visited = {start_room}
    results: List[Dict[str, Any]] = [{
        "room": start_room,
        "wings": start["wings"],
        "halls": start["halls"],
        "count": start["count"],
        "hop": 0,
    }]

    frontier: List[Tuple[str, int]] = [(start_room, 0)]
    while frontier:
        current_room, depth = frontier.pop(0)
        if depth >= max_hops:
            continue
        current = nodes.get(current_room, {})
        current_wings = set(current.get("wings", []))
        for room, data in nodes.items():
            if room in visited:
                continue
            shared = current_wings & set(data["wings"])
            if not shared:
                continue
            visited.add(room)
            results.append({
                "room": room,
                "wings": data["wings"],
                "halls": data["halls"],
                "count": data["count"],
                "hop": depth + 1,
                "connected_via": sorted(shared),
            })
            if depth + 1 < max_hops:
                frontier.append((room, depth + 1))

    results.sort(key=lambda x: (x["hop"], -x["count"]))
    return json.dumps({
        "status": "ok",
        "start_room": start_room,
        "max_hops": max_hops,
        "count": len(results),
        "results": results[:50],
    })


def find_tunnels(memory_store, wing_a: str = "", wing_b: str = "") -> str:
    """Return rooms that appear in >=2 wings, optionally constrained."""
    nodes, _ = build_graph(memory_store)

    tunnels: List[Dict[str, Any]] = []
    for room, data in nodes.items():
        wings = data["wings"]
        if len(wings) < 2:
            continue
        if wing_a and wing_a not in wings:
            continue
        if wing_b and wing_b not in wings:
            continue
        tunnels.append({
            "room": room,
            "wings": wings,
            "halls": data["halls"],
            "count": data["count"],
            "recent": data["dates"][-1] if data["dates"] else "",
        })

    tunnels.sort(key=lambda x: -x["count"])
    return json.dumps({
        "wing_a": wing_a or None,
        "wing_b": wing_b or None,
        "count": len(tunnels),
        "tunnels": tunnels[:50],
    })


def graph_stats(memory_store) -> str:
    """Return summary counts for the palace graph."""
    nodes, edges = build_graph(memory_store)

    tunnel_rooms = sum(1 for n in nodes.values() if len(n["wings"]) >= 2)
    wing_counts: Counter = Counter()
    for data in nodes.values():
        for w in data["wings"]:
            wing_counts[w] += 1

    top = [
        {"room": r, "wings": d["wings"], "count": d["count"]}
        for r, d in sorted(nodes.items(), key=lambda x: -len(x[1]["wings"]))[:10]
        if len(d["wings"]) >= 2
    ]
    return json.dumps({
        "total_rooms": len(nodes),
        "tunnel_rooms": tunnel_rooms,
        "total_edges": len(edges),
        "rooms_per_wing": dict(wing_counts.most_common()),
        "top_tunnels": top,
    })


def _fuzzy_match(query: str, nodes: Dict[str, Any], n: int = 5) -> List[str]:
    query_lower = query.lower()
    scored: List[Tuple[str, float]] = []
    for room in nodes:
        if query_lower in room.lower():
            scored.append((room, 1.0))
        elif any(word in room.lower() for word in query_lower.split("-")):
            scored.append((room, 0.5))
    scored.sort(key=lambda x: -x[1])
    return [r for r, _ in scored[:n]]
