"""
Builds a generic bus/branch/leaf connectivity graph for the auto-layout
Single Line Diagram view.

This is NOT a replica of ETAP's saved one-line diagram - that graphical
position/length data isn't accessible in the exported database (see the
investigation that led here). Instead, this reuses the same FromBus/ToBus/
Bus connectivity already powering the tabular Single Line Explorer, and the
frontend lays it out fresh as a tree (source at top, branching down to
buses, feeders, and generation/loads at the leaves).

Equipment doesn't always connect straight to a bus - a breaker's
FromElement/ToElement is just as often another piece of equipment's ID
(breaker -> transformer -> breaker -> bus). So this builds one unified
graph over buses AND every branch/leaf equipment ID, then traces each bus
outward through non-bus ("pass-through") equipment until it hits another
bus, collapsing the chain into a single virtual branch. Some references
turn out to be genuinely stale (equipment renamed/deleted over the
project's edit history but the old name left behind in another element's
terminal field) - those just don't resolve, which is correct: there's
nothing to recover, the source data itself is missing that link.
"""
from . import categories as cat_defs


def _clean(v):
    if isinstance(v, str):
        return v.strip()
    return v


def build_graph(conn, table_exists_fn):
    buses = []
    if table_exists_fn(conn, "Bus"):
        if table_exists_fn(conn, "BusH1"):
            query = ('SELECT b.ID, b.NominalkV, b.InService, h.BusOrientation '
                      'FROM "Bus" b LEFT JOIN "BusH1" h ON b.IID = h.IID')
        else:
            query = 'SELECT ID, NominalkV, InService, NULL FROM "Bus"'
        for row in conn.execute(query).fetchall():
            bus_id = _clean(row[0])
            if bus_id:
                buses.append({"id": bus_id, "kv": row[1], "in_service": row[2], "orientation": row[3]})
    bus_ids = {b["id"] for b in buses}

    # Unified adjacency over buses + every branch/leaf equipment ID.
    adj = {}

    def add_edge(a, b, equip):
        if not a or not b or a == b:
            return
        adj.setdefault(a, []).append((b, equip))
        adj.setdefault(b, []).append((a, equip))

    raw_leaves = []
    for table, cols in cat_defs.CONNECTIVITY_TABLES.items():
        if not table_exists_fn(conn, table):
            continue
        try:
            cur = conn.execute(f'SELECT ID, {", ".join(cols)} FROM "{table}"')
        except Exception:
            continue

        if len(cols) == 1:
            kind = cat_defs.LEAF_SYMBOL_KIND.get(table, "other")
            for row in cur.fetchall():
                eid, target = _clean(row[0]), _clean(row[1])
                if eid and target:
                    equip = {"table": table, "kind": kind, "id": eid}
                    add_edge(target, eid, equip)
                    raw_leaves.append((eid, target, equip))
        else:
            kind = cat_defs.BRANCH_SYMBOL_KIND.get(table, "line")
            for row in cur.fetchall():
                eid = _clean(row[0])
                terminals = [t for t in (_clean(v) for v in row[1:]) if t]
                if eid and len(terminals) >= 2:
                    equip = {"table": table, "kind": kind, "id": eid}
                    for other in terminals[1:]:
                        add_edge(terminals[0], eid, equip)
                        add_edge(eid, other, equip)

    def bfs_to_bus(start_node, already_at_bus):
        """From start_node, walk through non-bus nodes until a bus is
        reached. Returns (bus_id, chain_of_equipment) or (None, None)."""
        if already_at_bus:
            return start_node, []
        visited = {start_node}
        queue = [(start_node, [])]
        qi = 0
        while qi < len(queue):
            node, chain = queue[qi]
            qi += 1
            for neighbor, equip in adj.get(node, []):
                if neighbor in visited:
                    continue
                visited.add(neighbor)
                new_chain = chain + [equip]
                if neighbor in bus_ids:
                    return neighbor, new_chain
                queue.append((neighbor, new_chain))
        return None, None

    # Bus-to-bus virtual branches: BFS outward from every bus through
    # non-bus (equipment) nodes only, stopping at the first bus reached
    # along each path.
    branches = []
    seen_pairs = set()
    for start in bus_ids:
        visited = {start}
        queue = [(start, [])]
        qi = 0
        while qi < len(queue):
            node, chain = queue[qi]
            qi += 1
            for neighbor, equip in adj.get(node, []):
                if neighbor in visited:
                    continue
                visited.add(neighbor)
                new_chain = chain + [equip]
                if neighbor in bus_ids:
                    pair = frozenset((start, neighbor))
                    if pair not in seen_pairs:
                        seen_pairs.add(pair)
                        rep = new_chain[0]
                        branches.append({
                            "from": start, "to": neighbor,
                            "id": rep["id"], "kind": rep["kind"], "table": rep["table"],
                            "via": [e["id"] for e in new_chain],
                        })
                    # a bus is a hard stop - don't traverse past it
                else:
                    queue.append((neighbor, new_chain))

    leaves = []
    seen_leaf_ids = set()
    for eid, target, equip in raw_leaves:
        if eid in seen_leaf_ids:
            continue
        bus_id, _chain = bfs_to_bus(target, target in bus_ids)
        if bus_id:
            leaves.append({"id": eid, "bus": bus_id, "kind": equip["kind"], "table": equip["table"]})
            seen_leaf_ids.add(eid)

    return {"buses": buses, "branches": branches, "leaves": leaves,
            "source_tables": cat_defs.SOURCE_TABLES_PRIORITY}
