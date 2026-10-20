"""Pipe networks as the game joins them, from blueprint data.

A plain pipe joins any neighbour that connects back; a pipe-to-ground joins on
its one surface side (its blueprint direction, NOTES.md) and underground to the
first opposite-facing pipe-to-ground within reach. Pumps split networks, and
machines and tanks are left out: these are the pipes alone.
"""

from draftsman.data import entities

VEC = {0: (0, -1), 4: (1, 0), 8: (0, 1), 12: (-1, 0)}
REACH = next(c["max_underground_distance"] for c in entities.raw["pipe-to-ground"]["fluid_box"]["pipe_connections"]
             if c.get("connection_type") == "underground")


def networks(bp: dict) -> dict[tuple[int, int], tuple[int, int]]:
    """Tile -> network id (a tile in it) for every pipe and pipe-to-ground."""
    tiles = {}
    for e in bp["entities"]:
        if e["name"] in ("pipe", "pipe-to-ground"):
            tiles[(int(e["position"]["x"] // 1), int(e["position"]["y"] // 1))] = (e["name"], e.get("direction", 0))

    def opens(tile, toward):  # does the thing at tile have a normal connection toward this side?
        name, d = tiles[tile]
        return name == "pipe" or VEC[d] == toward

    group = {t: t for t in tiles}

    def root(t):
        while group[t] != t:
            group[t] = group[group[t]]
            t = group[t]
        return t

    for (x, y), (name, d) in tiles.items():
        sides = VEC.values() if name == "pipe" else [VEC[d]]
        for dx, dy in sides:
            n = (x + dx, y + dy)
            if n in tiles and opens(n, (-dx, -dy)):
                group[root((x, y))] = root(n)
        if name == "pipe-to-ground":
            dx, dy = VEC[d]
            for k in range(1, REACH + 1):
                n = (x - dx * k, y - dy * k)
                if n in tiles and tiles[n] == ("pipe-to-ground", (d + 8) % 16):
                    group[root((x, y))] = root(n)
                    break
    return {t: root(t) for t in tiles}


def extents(bp: dict) -> dict[tuple[int, int], int]:
    """Network id -> its extent: the longer side of the tiles it spans."""
    spans = {}
    for (x, y), net in networks(bp).items():
        x1, y1, x2, y2 = spans.get(net, (x, y, x, y))
        spans[net] = (min(x1, x), min(y1, y), max(x2, x), max(y2, y))
    return {net: max(x2 - x1, y2 - y1) + 1 for net, (x1, y1, x2, y2) in spans.items()}
