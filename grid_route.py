"""
grid_route.py
-------------
Concave-safe ordering of lawnmower scan-line segments into a flight route.

Pure Python (no QGIS) so the routing logic can be unit-tested directly.
generate_flight_grid() in grid_planner.py sweeps the survey polygon into
scan-line segments and hands them here to be grouped into contiguous strips and
snaked, so the drone never flies a pass across a gap in the survey area.
"""

import math

# Two segments on adjacent scan lines belong to the same strip when their
# along-track (y) ranges overlap by more than this (metres). A real gap between
# strips gives no overlap, so the strips are kept apart and never flown across.
_OVERLAP_EPS = 1e-6


def boustrophedon_route(columns, densify_spacing=None):
    """Turn a swept set of scan-line segments into an ordered turn-point route
    that never flies a pass across a gap in the survey area.

    `columns` is a list of (x, segments), one per scan line left to right, where
    each segment is (y_low, y_high) of an in-polygon piece of that line.

    With `densify_spacing` set (metres), each pass is filled with points that
    close together, so every photo location becomes its own waypoint (used by
    full-automatic capture). Left as None (semi-automatic), only the pass
    endpoints are returned. Returns a flat list of (x, y) points in the rotated
    frame.
    """
    cells, adjacency = decompose_cells(columns)
    return order_cells(cells, adjacency, densify_spacing)


def decompose_cells(columns):
    """Boustrophedon cellular decomposition: group scan-line segments into
    contiguous strips (cells). A cell is a run of segments, one per scan line,
    that stay connected; it ends where the area splits, merges, or stops.

    Returns (cells, adjacency): `cells` is a list of cells, each a list of
    (x, y_low, y_high); `adjacency` is a parallel list of sets giving, for each
    cell, the cells it connects to at a split/merge. Adjacent cells share the
    survey area's spine, so visiting them in graph order keeps the legs between
    strips inside the polygon."""
    cells = []
    adjacency = []

    def open_cell(seg, parents):
        idx = len(cells)
        cells.append([seg])
        adjacency.append(set())
        for p in parents:
            adjacency[idx].add(p)
            adjacency[p].add(idx)
        return idx

    prev_segs = []
    prev_idx = {}                        # prev column seg index -> cell index
    for x, segs in columns:
        cur_to_prev = [[] for _ in segs]
        prev_to_cur = [[] for _ in prev_segs]
        for j, (clo, chi) in enumerate(segs):
            for i, (plo, phi) in enumerate(prev_segs):
                if min(chi, phi) - max(clo, plo) > _OVERLAP_EPS:
                    cur_to_prev[j].append(i)
                    prev_to_cur[i].append(j)

        cur_idx = {}
        for j, (clo, chi) in enumerate(segs):
            prevs = cur_to_prev[j]
            # A clean one-to-one link continues the same strip; anything else
            # (a start, a split, or a merge) opens a fresh cell that neighbours
            # the previous cells it touches.
            if len(prevs) == 1 and len(prev_to_cur[prevs[0]]) == 1:
                ci = prev_idx[prevs[0]]
                cells[ci].append((x, clo, chi))
                cur_idx[j] = ci
            else:
                cur_idx[j] = open_cell((x, clo, chi),
                                       [prev_idx[i] for i in prevs])

        prev_segs = segs
        prev_idx = cur_idx

    return cells, adjacency


def _pass_points(x, ylo, yhi, densify_spacing):
    """Points along one pass. Endpoints only when densify_spacing is None;
    otherwise evenly spaced points no farther apart than densify_spacing, always
    including both ends (so photo waypoints land on exact, regular spacing)."""
    if not densify_spacing or densify_spacing <= 0:
        return [(x, ylo), (x, yhi)]
    length = yhi - ylo
    n = max(1, int(math.ceil(length / densify_spacing)))
    step = length / n
    return [(x, ylo + i * step) for i in range(n + 1)]


def cell_turns(cell, densify_spacing=None):
    """Snake one cell into points: up one line, down the next."""
    turns = []
    for k, (x, ylo, yhi) in enumerate(cell):
        pts = _pass_points(x, ylo, yhi, densify_spacing)
        turns.extend(pts if k % 2 == 0 else pts[::-1])
    return turns


def _visit_order(turnlists, adjacency):
    """Depth-first cell order, starting from a leaf so a path-shaped area is
    walked end to end. Keeps neighbouring cells consecutive."""
    n = len(turnlists)
    if n == 0:
        return []

    def rank(k):
        return (len(adjacency[k]), turnlists[k][0][0], turnlists[k][0][1])

    order, visited = [], set()
    for root in sorted(range(n), key=rank):          # leaves (low degree) first
        if root in visited:
            continue
        stack = [root]
        while stack:
            node = stack.pop()
            if node in visited:
                continue
            visited.add(node)
            order.append(node)
            nbrs = sorted(adjacency[node] - visited,
                          key=lambda k: (turnlists[k][0][0], turnlists[k][0][1]),
                          reverse=True)
            stack.extend(nbrs)
    return order


def order_cells(cells, adjacency, densify_spacing=None):
    """Concatenate the cells into one route in adjacency (graph) order, so the
    legs between strips run along the survey area's spine and stay inside it.
    Each cell is flown in whichever direction enters it closest to the previous
    cell's exit. Every pass itself lies within a single strip."""
    # Each cell has >= 1 segment, so cell_turns yields >= 2 points; indices stay
    # aligned with `adjacency`.
    turnlists = [cell_turns(c, densify_spacing) for c in cells]
    if not turnlists:
        return []
    route = []
    cur = None
    for k in _visit_order(turnlists, adjacency):
        t = turnlists[k]
        if cur is None:
            chosen = t
        else:
            fwd = (t[0][0] - cur[0]) ** 2 + (t[0][1] - cur[1]) ** 2
            rev = (t[-1][0] - cur[0]) ** 2 + (t[-1][1] - cur[1]) ** 2
            chosen = t if fwd <= rev else t[::-1]
        route.extend(chosen)
        cur = route[-1]
    return route


def split_by_waypoint_count(waypoints, n_missions, max_waypoints=None):
    """Split a dense (one-waypoint-per-photo) route into contiguous sub-missions
    for full-automatic capture.

    Unlike split_waypoints (which divides by flight line, two points each), a
    full-auto pass has many waypoints, so this divides by waypoint count. The
    edges between waypoints are shared out across the missions as evenly as
    possible, and consecutive missions share a seam waypoint so the coloured
    paths join up with no gap.

    `n_missions` is treated as a minimum (usually the battery count) and raised
    if needed so no sub-mission exceeds `max_waypoints` (DJI caps waypoints per
    mission). With max_waypoints set, every returned mission has at most that
    many waypoints. Returns a list of waypoint sub-lists.
    """
    pts = list(waypoints)
    w = len(pts)
    try:
        n = int(n_missions)
    except (TypeError, ValueError):
        n = 1
    n = max(1, n)
    if max_waypoints and max_waypoints >= 2 and w > 1:
        # Each mission spans at most max_waypoints points including its shared
        # seam, so it adds at most (max_waypoints - 1) edges. This is the fewest
        # missions that keeps every one within the cap.
        need = -(-(w - 1) // (max_waypoints - 1))
        n = max(n, need)
    n = min(n, max(1, w - 1))
    if n <= 1 or w <= 1:
        return [pts]

    base, rem = divmod(w - 1, n)          # distribute the w-1 edges evenly
    missions = []
    start = 0
    for g in range(n):
        span = base + (1 if g < rem else 0)
        end = start + span                # inclusive index of this mission's last point
        missions.append(pts[start:end + 1])
        start = end                       # next mission shares this seam waypoint
    return missions
