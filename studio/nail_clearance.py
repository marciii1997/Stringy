"""Plan-view clearance for a hand-wound path, in millimetres."""

from collections import Counter
from itertools import pairwise

import numpy as np


def distances(points, start, end):
    vector = end - start
    delta = points - start
    along = np.sum(delta * vector, axis=-1) / np.sum(vector * vector, axis=-1)
    return np.linalg.norm(delta - np.clip(along, 0, 1)[..., None] * vector, axis=-1)


def placement_report(route):
    points = np.array([(p["x_mm"], p["y_mm"]) for p in route["anchors"]])
    counts = Counter(tuple(sorted(e)) for e in pairwise(route["path"]))
    edges = np.array(list(counts))
    shaft = route["settings"]["shaft_mm"]
    diameter = route["settings"]["thread_mm"]
    minimum = np.inf
    uncertain = {str(error): 0 for error in (.15, .3, .5)}
    for start in range(0, len(edges), 96):
        a, b = edges[start:start + 96].T
        gaps = distances(points, points[a, None], points[b, None])
        rows = np.arange(len(a))
        gaps[rows, a] = gaps[rows, b] = np.inf
        # A tangent can be one radius farther out than the centre line. The
        # foreign shaft plus half the thread consumes a second such radius.
        margins = gaps.min(axis=1) - shaft - diameter
        minimum = min(minimum, margins.min())
        multiplicities = np.array([counts[(i, j)] for i, j in zip(a, b)])
        for error in uncertain:
            uncertain[error] += int(multiplicities[margins <= 2 * float(error)].sum())
    separations = np.linalg.norm(points[:, None] - points[None, :], axis=2)
    pairs = separations[np.triu_indices(len(points), 1)]
    return {
        "min_shaft_margin_mm": float(minimum),
        "uncertain_steps_by_radial_error_mm": uncertain,
        "pairs_under_5mm": int(np.sum(pairs < 5)),
        "spacing_deficit_mm": float(np.maximum(5 - pairs, 0).sum()),
        "min_pitch_mm": float(pairs.min()),
    }


def spread_close_nails(route, seed):
    """Return candidate coordinates; validate optical effects before adoption."""
    settings = route["settings"]
    points = np.array([(p["x_mm"], p["y_mm"]) for p in route["anchors"]])
    dims = np.array(settings["rectangle_mm"] or [settings["diameter_mm"]] * 2)
    edges = np.array(sorted(set(tuple(sorted(edge)) for edge in pairwise(route["path"]))))
    a, b = edges.T
    clearance = settings["shaft_mm"] + settings["thread_mm"] + .5
    directions = np.arange(8) * np.pi / 4
    offsets = np.column_stack((np.cos(directions), np.sin(directions)))
    moved = 0
    for nail in np.random.default_rng(seed).permutation(len(points)):
        original = points[nail].copy()
        near = np.linalg.norm(points - original, axis=1)
        near[nail] = np.inf
        nearest = near.min()
        if nearest >= 5:
            continue
        deficit = np.maximum(5 - near, 0).sum()
        incident = (a == nail) | (b == nail)
        ends = np.where(a[incident] == nail, b[incident], a[incident])
        start, end = points[a[~incident]], points[b[~incident]]
        best = None
        for step in (.35, .7, 1.05):
            for point in original + step * offsets:
                normalized = 2 * point / dims
                if np.max(abs(normalized)) > 1 or (not settings["rectangle_mm"] and normalized @ normalized > 1):
                    continue
                near = np.linalg.norm(points - point, axis=1)
                near[nail] = np.inf
                cost = np.maximum(5 - near, 0).sum()
                if near.min() < nearest or cost >= deficit - .08 or (best and cost >= best[0]):
                    continue
                if near[ends].min() < settings["min_segment_mm"]:
                    continue
                if len(start) and distances(point, start, end).min() <= clearance:
                    continue
                gaps = distances(points, point, points[ends, None])
                gaps[:, nail] = gaps[np.arange(len(ends)), ends] = np.inf
                if gaps.min() > clearance:
                    best = cost, point
        if best:
            points[nail] = best[1]
            moved += 1
            if moved >= seed % 7 + 3:
                break
    return (2 * points / dims).tolist() if moved else None


def plan_winding_sides(route, reference=None):
    """Choose a consistent clockwise/counterclockwise traversal at each visit.

    Board coordinates have y increasing downwards. A tangent offset to the
    right of travel (+90 degrees in these coordinates) wraps counterclockwise.
    """
    points = np.array([(p["x_mm"], p["y_mm"]) for p in route["anchors"]])
    path = np.array(route["path"])
    a, b = path[:-1], path[1:]
    v = points[b] - points[a]
    length = np.linalg.norm(v, axis=1)
    unit = v / length[:, None]
    perp = unit[:, ::-1] * np.array([-1, 1])
    # Evaluate using the widest yarn in this profile family.
    radius = (route["settings"]["shaft_mm"] + route["settings"]["thread_mm"] * 1.2) / 2
    sides = (-1, 1)
    error = .5
    # Changing the chord vector by at most 2e rotates its direction and
    # changes the angle of an internal tangent. Bound both contributions.
    shortest = length - 2 * error
    if np.any(shortest <= 2 * radius):
        return
    angle_bound = np.arcsin(2 * error / length) + np.arcsin(2 * radius / shortest) - np.arcsin(2 * radius / length)
    displacement = 2 * error + 2 * radius * np.sin(angle_bound / 2)
    angles = np.zeros((len(a), 2, 2))
    margins = np.empty_like(angles)
    nearest = np.empty_like(angles, dtype=int)
    for i, left in enumerate(sides):
        for j, right in enumerate(sides):
            k = radius * (left - right) / length
            normal = k[:, None] * unit + np.sqrt(1 - k * k)[:, None] * perp
            angles[:, i, j] = np.arctan2(normal[:, 1], normal[:, 0])
            start = points[a] + left * radius * normal
            end = points[b] + right * radius * normal
            for offset in range(0, len(a), 96):
                sl = slice(offset, offset + 96)
                gaps = distances(points, start[sl, None], end[sl, None])
                rows = np.arange(len(gaps))
                gaps[rows, a[sl]] = gaps[rows, b[sl]] = np.inf
                margins[sl, i, j] = gaps.min(axis=1) - radius
                nearest[sl, i, j] = gaps.argmin(axis=1)
    costs = np.maximum(0, displacement[:, None, None] - margins)
    # For incremental optical edits, prefer the already tested tangents of
    # surviving chords. Reversing travel negates both tangent signs.
    changes = np.zeros_like(costs, dtype=np.int8)
    if reference is not None:
        old = reference["path"]
        old_sides = [int(s == "ccw") for s in reference["winding_sides"]]
        known = {}
        for u, v, i, j in zip(old, old[1:], old_sides, old_sides[1:]):
            known.setdefault((u, v), set()).add((i, j))
            known.setdefault((v, u), set()).add((1 - j, 1 - i))
        for n, pair in enumerate(zip(a, b)):
            if pair in known:
                changes[n] = 1
                for i, j in known[pair]:
                    changes[n, i, j] = 0
    # Minimize uncertainty count, deficit, tangent changes, then wrapped angle.
    parent = np.empty((len(a), 2, 2), dtype=np.int8)
    previous = {(i, j): (int(costs[0, i, j] > 0), costs[0, i, j], int(changes[0, i, j]), 0.)
                for i in range(2) for j in range(2)}
    for edge in range(1, len(a)):
        current = {}
        for i in range(2):
            for j in range(2):
                candidates = []
                for h in range(2):
                    count, deficit, changed, wrap = previous[h, i]
                    arc = (-sides[i] * (angles[edge, i, j] - angles[edge - 1, h, i])) % (2 * np.pi)
                    candidates.append((
                        count + int(costs[edge, i, j] > 0),
                        deficit + costs[edge, i, j],
                        changed + int(changes[edge, i, j]),
                        wrap + arc,
                    ))
                choice = min(range(2), key=candidates.__getitem__)
                current[i, j] = candidates[choice]
                parent[edge, i, j] = choice
        previous = current
    i, j = min(previous, key=previous.__getitem__)
    count, _, _, total_arc = previous[i, j]
    schedule = np.empty(len(path), dtype=int)
    schedule[-2:] = i, j
    for edge in range(len(a) - 1, 0, -1):
        schedule[edge - 1] = parent[edge, schedule[edge], schedule[edge + 1]]
    chosen = margins[np.arange(len(a)), schedule[:-1], schedule[1:]]
    route["winding_sides"] = ["cw" if side == 0 else "ccw" for side in schedule]
    route["winding_clearance"] = {
        "radial_error_mm": error, "test_thread_mm": 2 * radius - route["settings"]["shaft_mm"],
        "uncertain_edges": [
            {"step": int(n)+1, "obstacle": int(nearest[n,schedule[n],schedule[n+1]]),
             "margin_mm": float(chosen[n])} for n in np.flatnonzero(chosen < displacement)
        ],
        "uncertified_steps": count, "min_nominal_margin_mm": float(chosen.min()),
        "wrapped_length_m_at_test_diameter": float(total_arc * radius / 1000),
    }
