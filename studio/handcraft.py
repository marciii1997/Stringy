"""Defaults for one hand-wound, 24-day project."""

THREAD_MM = 0.20
SHAFT_MM = 1.2
OPACITY = 0.85
MIN_SEGMENT_MM = 12
MAX_NAIL_LOAD = 48
MAX_CHORD_USES = 2
WHITE_PROTECTION = 8
MAX_NAILS = 350
SEGMENTS = 3000


def optimizer_flags():
    return [
        "--min-segment-mm", str(MIN_SEGMENT_MM),
        "--max-nail-load", str(MAX_NAIL_LOAD),
        "--max-chord-uses", str(MAX_CHORD_USES),
        "--white-protection", str(WHITE_PROTECTION),
        "--repeat-penalty", "0.001",
    ]


def improve_winding_order(route):
    """Reverse closed subpaths at repeated nails, preserving every chord."""
    import random
    from collections import defaultdict
    from itertools import pairwise

    original = route["path"]
    if len(original) < 4:
        return
    lengths = {tuple(sorted((s["from"], s["to"]))): s["length_mm"] for s in route["segments"]}

    def effort(path):
        returns = sum(a == c for a, c in zip(path, path[2:]))
        # Count arrivals per nail within the actual digital calendar boundaries.
        peak = concentration = 0
        step, extra = divmod(len(path) - 1, 24)
        start = 1
        for day in range(24):
            end = start + step + (day < extra)
            counts = defaultdict(int)
            for nail in path[start:end]:
                counts[nail] += 1
            peak = max(peak, max(counts.values(), default=0))
            concentration += sum(n * (n - 1) for n in counts.values())
            start = end
        return returns, peak, concentration

    best = original
    baseline = best_effort = effort(original)
    for seed in (42, 11, 79, 103):
        path = original.copy()
        rng = random.Random(seed)
        for _ in range(12):
            changed = False
            positions = list(range(1, len(path) - 1))
            rng.shuffle(positions)
            for a in positions:
                if path[a - 1] != path[a + 1]:
                    continue
                options = [b for b in range(1, len(path) - 1) if path[b] == path[a] and abs(a - b) >= 2]
                rng.shuffle(options)
                for b in options:
                    i, j = sorted((a, b))
                    old = int(path[i - 1] == path[i + 1]) + int(path[j - 1] == path[j + 1])
                    new = int(path[i - 1] == path[j - 1]) + int(path[i + 1] == path[j + 1])
                    if new < old:
                        path[i:j + 1] = reversed(path[i:j + 1])
                        changed = True
                        break
            if not changed:
                break
        candidate = effort(path)
        if candidate < best_effort:
            best, best_effort = path, candidate
    route["winding_order"] = {"before": list(baseline), "after": list(best_effort)}
    if best is not original:
        route["path"] = best
        route["segments"] = [
            {"from": a, "to": b, "length_mm": lengths[tuple(sorted((a, b)))]}
            for a, b in pairwise(best)
        ]
