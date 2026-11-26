"""Improve physical tone reproduction on a fixed, already windable board."""

from copy import deepcopy

import cv2
import numpy as np

from studio.handcraft import MAX_FIDELITY_STEPS, improve_winding_order
from studio.nail_clearance import placement_report
from studio.optical import OpticalRefinement
from studio.physical import assess, png, simulation, target_array
from studio.quality import view_quality
from studio.winding_study import (
    crossings,
    day_ends,
    difficulty,
    effort,
    measurements,
    rebuilt,
    registered_regions,
    reorder_visits,
)


def gain_map(search, view, regions, focus):
    height, width = search.target.shape
    image = cv2.resize(view, (width, height), interpolation=cv2.INTER_AREA).astype(np.float32) / 255
    blur = lambda x: cv2.GaussianBlur(x, (0, 0), 1.5)
    original, actual = blur(search.target), blur(image)
    residual = original - actual
    gradient = residual / max(0.01, np.sqrt(np.mean(residual**2)))
    for name, (x, y, u, v) in regions:
        box = np.s_[int(height * y) : int(height * v), int(width * x) : int(width * u)]
        a, b = original[box], actual[box]
        a, b = a - a.mean(), b - b.mean()
        sx, sy = max(0.01, a.std()), max(0.01, b.std())
        derivative = a / (sx * sy) - np.mean(a * b) * b / (sx * sy**3)
        gradient[box] += focus[name] * original.size / (len(regions) * a.size) * derivative
    gradient[original > 0.94] += 0.4 / max(0.02, np.mean(original > 0.94))
    sensitivity = (blur(gradient) * image).ravel()
    gain = np.full(search.legal.shape, -np.inf)
    gain[search.a, search.b] = gain[search.b, search.a] = (
        -sensitivity[search.indices].mean(axis=1) * search.length
    )
    return gain


def edits(search, route, gain, count, rng, swaps):
    p = np.array(route["path"])
    uses = np.zeros(search.legal.shape, dtype=int)
    np.add.at(uses, (p[:-1], p[1:]), 1)
    uses += uses.T.copy()
    load = uses.sum(axis=0)
    safe = search.legal & (search.center_gap >= 2.8)
    max_load = route["settings"]["max_nail_load"]
    for _ in range(count):
        i = rng.integers(1, len(p) - 1, 9000)
        if swaps:
            j = rng.integers(1, len(p) - 1, len(i))
            i, j = np.minimum(i, j), np.maximum(i, j)
            a, b, c, d = p[i - 1], p[i], p[j], p[j + 1]
            valid = (j > i) & (a != d) & (b != c) & safe[a, c] & safe[b, d]
            added = ((a, c), (b, d))
            removed = ((a, b), (c, d))
        else:
            a, b, c = p[i - 1], p[i], p[i + 1]
            d = rng.integers(0, len(load), len(i))
            valid = (a != c) & (b != d) & safe[a, d] & safe[d, c] & (load[d] + 2 <= max_load)
            added = ((a, d), (d, c))
            removed = ((a, b), (b, c))
        for u, v in added:
            valid &= uses[u, v] < search.limit
        idx = np.flatnonzero(valid)
        if not len(idx):
            continue
        diff = sum(gain[u[idx], v[idx]] for u, v in added) - sum(
            gain[u[idx], v[idx]] for u, v in removed
        )
        n = idx[np.argmax(diff)]
        if diff.max() <= 0:
            continue
        for pairs, delta in ((added, 1), (removed, -1)):
            for u, v in pairs:
                uses[u[n], v[n]] += delta
                uses[v[n], u[n]] += delta
        if swaps:
            p[i[n] : j[n] + 1] = p[i[n] : j[n] + 1][::-1]
        else:
            load[b[n]] -= 2
            load[d[n]] += 2
            p[i[n]] = d[n]
    return p.tolist()


def density_search(route, target, rounds, alive, extra):
    best = deepcopy(route)
    regions = registered_regions(route, target)
    search = OpticalRefinement(route, target, route["settings"]["rectangle_mm"])
    view = simulation(best)
    initial = metrics = assess(target, view, regions)
    risk = route["winding_clearance"]["uncertified_steps"]
    white = crossings(route, target).max()
    # Block prospective chords with long contiguous runs of white target pixels.
    mask = search.target.ravel()[search.indices] > 0.94
    ticks = np.arange(mask.shape[1])
    span = (ticks - np.maximum.accumulate(np.where(mask, -1, ticks), axis=1)).max(axis=1)
    dims = route["settings"]["rectangle_mm"]
    pins = np.asarray(route["settings"]["nail_positions"]) * dims / 2
    distances = np.linalg.norm(pins[search.a] - pins[search.b], axis=1)
    block = span * distances / mask.shape[1] > max(1.0, white - 2)
    search.legal[search.a[block], search.b[block]] = False
    search.legal[search.b[block], search.a[block]] = False
    rng = np.random.default_rng(31819)
    history = []
    incumbent, record = best, metrics

    def merit(m):
        return (
            m["tone_error"]
            + sum(3 * max(0.0, initial[n] + 0.002 - m[n]) for n, _ in regions)
            + 5 * max(0.0, m["white_ink"] - initial["white_ink"])
        )

    for i in range(rounds):
        if not alive():
            break
        if i == 200:
            best, metrics = incumbent, record
            view = simulation(best)
            rng = np.random.default_rng(31819)
        iteration = i if i < 200 else i - 200
        focus = {
            n: (0.03 if metrics[n] > initial[n] + 0.005 else (0.8, 2.5, 5.0, 0.35)[iteration % 4])
            for n, _ in regions
        }
        g = gain_map(search, view, regions, focus)
        remaining = len(route["path"]) + extra - len(best["path"])
        if remaining > 0 and iteration % 2 == 0:
            if i >= 200 and iteration % 4 == 0 and remaining >= 3:
                p = add_loops(search, best, g, min(remaining, (24, 30, 12)[iteration % 3]), rng)
            else:
                p = insert_visits(search, best, g, min(remaining, (16, 30, 8)[iteration % 3]), rng)
        elif i >= 200 and iteration % 3 == 1:
            p = relocate_visits(search, best, g, (10, 4, 20)[iteration % 5 % 3], rng)
        else:
            p = edits(search, best, g, (16, 6, 24, 3, 10)[iteration % 5], rng, iteration % 3 != 0)
        if p == best["path"]:
            continue
        trial = rebuilt(best, p, preserve_tangents=i >= 200)
        if trial["winding_clearance"]["uncertified_steps"] > risk:
            continue
        im = simulation(trial)
        m = assess(target, im, regions)
        if merit(m) >= merit(metrics) or crossings(trial, target).max() > white + 0.2:
            continue
        best, view, metrics = trial, im, m
        if (
            m["tone_error"] < record["tone_error"]
            and m["white_ink"] <= initial["white_ink"] + 0.0003
            and all(m[n] >= initial[n] - 0.001 for n, _ in regions)
        ):
            incumbent, record = trial, m
            history.append([i + 1, m["tone_error"]])
            if m["tone_error"] <= initial["tone_error"] * 0.82:
                break
    best, metrics = incumbent, record
    best["fidelity"] = {"baseline": initial, "accepted": history}
    best["physical_quality"] = metrics
    best["placement"] = placement_report(best)
    best["day_ends"] = day_ends(effort(best))
    return best


def insert_visits(search, route, gain, count, rng):
    path = list(route["path"])
    uses = np.zeros(search.legal.shape, dtype=int)
    np.add.at(uses, (path[:-1], path[1:]), 1)
    uses += uses.T.copy()
    load = uses.sum(axis=0)
    safe = search.legal & (search.center_gap >= 2.8)
    for _ in range(count):
        i = rng.integers(0, len(path) - 1, 24000)
        p = np.array(path)
        a, b = p[i], p[i + 1]
        c = rng.integers(0, len(load), len(i))
        valid = safe[a, c] & safe[c, b] & (uses[a, c] < search.limit) & (uses[c, b] < search.limit)
        valid &= load[c] + 2 <= route["settings"]["max_nail_load"]
        idx = np.flatnonzero(valid)
        if not len(idx):
            break
        score = gain[a[idx], c[idx]] + gain[c[idx], b[idx]] - gain[a[idx], b[idx]]
        n = idx[np.argmax(score)]
        if score.max() <= 0:
            break
        for u, v, delta in ((a[n], b[n], -1), (a[n], c[n], 1), (c[n], b[n], 1)):
            uses[u, v] += delta
            uses[v, u] += delta
        load[c[n]] += 2
        path.insert(i[n] + 1, int(c[n]))
    return path


def relocate_visits(search, route, gain, count, rng):
    # Move a visit between unrelated parts of the path, maintaining the number
    # of pulls while allowing the length and spatial distribution to change.
    path = np.asarray(route["path"])
    uses = np.zeros(search.legal.shape, dtype=int)
    np.add.at(uses, (path[:-1], path[1:]), 1)
    uses += uses.T.copy()
    load = uses.sum(axis=0)
    safe = search.legal & (search.center_gap >= 2.8)
    for _ in range(count):
        a, b, c = path[:-2], path[1:-1], path[2:]
        ix = np.flatnonzero(safe[a, c] & (uses[a, c] < search.limit))
        if not len(ix):
            break
        cut = ix[np.argmax(gain[a[ix], c[ix]] - gain[a[ix], b[ix]] - gain[b[ix], c[ix]])]
        x, y, z = path[cut : cut + 3]
        i = rng.integers(0, len(path) - 1, 20000)
        d = rng.integers(0, len(load), len(i))
        u, v = path[i], path[i + 1]
        ok = (abs(i - cut) > 2) & (load[d] + 2 <= route["settings"]["max_nail_load"])
        # Distinct endpoints keep chord caps valid across removal and insertion.
        for node in (x, y, z):
            ok &= (u != node) & (v != node) & (d != node)
        ix = np.flatnonzero(
            ok & safe[u, d] & safe[v, d] & (uses[u, d] < search.limit) & (uses[v, d] < search.limit)
        )
        if not len(ix):
            continue
        n = ix[np.argmax(gain[u[ix], d[ix]] + gain[d[ix], v[ix]] - gain[u[ix], v[ix]])]
        add = int(d[n])
        insert = int(i[n]) + 1
        for l, r, delta in (
            (x, y, -1),
            (y, z, -1),
            (x, z, 1),
            (u[n], v[n], -1),
            (u[n], add, 1),
            (add, v[n], 1),
        ):
            uses[l, r] += delta
            uses[r, l] += delta
        load[y] -= 2
        load[add] += 2
        path = np.delete(path, cut + 1)
        path = np.insert(path, insert - int(cut + 1 < insert), add)
    return path.tolist()


def add_loops(search, route, gain, budget, rng):
    path = list(route["path"])
    uses = np.zeros_like(search.legal, dtype=int)
    np.add.at(uses, (path[:-1], path[1:]), 1)
    uses += uses.T.copy()
    loads = uses.sum(axis=0)
    for _ in range(budget // 3):
        i = rng.integers(1, len(path) - 1, 50000)
        p = np.asarray(path)
        a = p[i]
        b, c = rng.integers(len(loads), size=(2, len(i)))
        ok = (b != p[i - 1]) & (c != p[i + 1])
        for n in (a, b, c):
            ok &= loads[n] + 2 <= route["settings"]["max_nail_load"]
        for u, v in ((a, b), (b, c), (c, a)):
            ok &= (
                search.legal[u, v] & (search.center_gap[u, v] >= 2.8) & (uses[u, v] < search.limit)
            )
        ix = np.flatnonzero(ok)
        if not len(ix):
            continue
        j = ix[np.argmax(gain[a[ix], b[ix]] + gain[b[ix], c[ix]] + gain[c[ix], a[ix]])]
        aa, bb, cc = int(a[j]), int(b[j]), int(c[j])
        path[i[j] + 1 : i[j] + 1] = [bb, cc, aa]
        for u, v in ((aa, bb), (bb, cc), (cc, aa)):
            uses[u, v] += 1
            uses[v, u] += 1
            loads[u] += 2
    return path


def structural_gain(search, view, regions):
    # SSIM derivative with respect to local luminance, contrast and covariance.
    h, w = search.target.shape
    smooth = lambda a, s: cv2.GaussianBlur(a, (0, 0), s)
    ref = smooth(search.target, max(h, w) / 256)
    actual = cv2.resize(view, (w, h), interpolation=cv2.INTER_AREA).astype(np.float32) / 255
    sim = smooth(actual, max(h, w) / 256)
    sigma = max(h, w) / 128
    mean = lambda a: smooth(a, sigma)
    x, y = mean(ref), mean(sim)
    a = 2 * x * y + 0.01**2
    b = 2 * (mean(ref * sim) - x * y) + 0.03**2
    c = x * x + y * y + 0.01**2
    d = np.maximum(0, mean(ref * ref) - x * x) + np.maximum(0, mean(sim * sim) - y * y) + 0.03**2
    s = a * b / (c * d)
    gradient = 2 * (
        mean(x * b / (c * d) - s * y / c - x * a / (c * d) + s * y / d)
        + ref * mean(a / (c * d))
        - sim * mean(s / d)
    )
    sensitivity = (smooth(gradient, max(h, w) / 256) * actual).ravel()
    gain = search.chord_gains(view, True, 1, regions) * 0.12
    values = -sensitivity[search.indices].mean(axis=1) * search.length
    gain[search.a, search.b] += values
    gain[search.b, search.a] += values
    return gain


def finish_candidate(route, candidate, target, alive):
    regions = registered_regions(route, target)
    floor = measurements(route, target, regions)
    base_ssim = view_quality(target, png(simulation(route)), rectangular=True)["ssim"]
    initial_len = len(route["path"])
    # Extra pulls earn their place only at a substantial gain. Equal-length
    # improvements can pass with a smaller gain.
    ratio = 0.85 if len(candidate["path"]) > initial_len else 0.99
    threshold = floor[0]["tone_error"] * ratio
    if candidate["physical_quality"]["tone_error"] > threshold:
        return None
    origin = deepcopy(candidate)
    improve_winding_order(candidate)
    rng = np.random.default_rng(558)
    path = candidate["path"]
    for i in range(180):
        if i % 20 == 0 and not alive():
            break
        path = reorder_visits(path, rng)
    candidate = rebuilt(origin, path, preserve_tangents=True)
    search = OpticalRefinement(route, target, route["settings"]["rectangle_mm"])
    safe_risk = route["winding_clearance"]["uncertified_steps"]
    bright_limit = crossings(route, target).max() + 0.2
    prior_returns = difficulty(route)[0]
    winner = None
    best_ssim = base_ssim
    working = candidate
    working_view = simulation(working)
    working_ssim = view_quality(target, png(working_view), rectangular=True)["ssim"]
    for i in range(141):
        if i and not alive():
            break
        if i:
            gain = structural_gain(search, working_view, regions)
            path = edits(search, working, gain, (4, 8, 16, 24)[i % 4], rng, i % 3 != 1)
            if difficulty({"path": path})[0] > prior_returns:
                continue
            candidate = rebuilt(working, path, preserve_tangents=True)
        if candidate["winding_clearance"]["uncertified_steps"] > safe_risk:
            continue
        view = simulation(candidate)
        m = assess(target, view, regions)
        ssim = view_quality(target, png(view), rectangular=True)["ssim"]
        if (
            m["tone_error"] > threshold
            or m["white_ink"] > floor[0]["white_ink"] + 0.0003
            or any(m[n] < floor[0][n] - 0.001 for n, _ in regions)
            or crossings(candidate, target).max() > bright_limit
            or i
            and ssim <= working_ssim
        ):
            continue
        working, working_view, working_ssim = candidate, view, ssim
        if ssim < best_ssim:
            continue
        profiles = measurements(candidate, target, regions)
        if any(
            new["white_ink"] > old["white_ink"] + 0.001
            or any(new[n] < old[n] - 0.001 for n, _ in regions)
            for new, old in zip(profiles, floor)
        ):
            continue
        if sum(x["tone_error"] for x in profiles) >= sum(x["tone_error"] for x in floor):
            continue
        winner = deepcopy(candidate)
        winner["settings"] = {**winner["settings"], "max_segments": len(winner["path"]) - 1}
        winner["physical_quality"] = m
        winner["material_profiles"] = profiles[1:]
        winner["placement"] = placement_report(winner)
        winner["day_ends"] = day_ends(effort(winner))
        winner["fidelity"] = {
            "baseline_profiles": floor,
            "profiles": profiles,
            "baseline_ssim": base_ssim,
            "ssim": ssim,
            "baseline_steps": initial_len - 1,
            "white_run_mm": float(crossings(winner, target).max()),
            "relative_tone_gain": 1 - m["tone_error"] / floor[0]["tone_error"],
        }
        winner.pop("polish", None)
        best_ssim = ssim
    return winner


def optimize_fidelity(route, target, alive=lambda: True):
    count = len(route["path"]) - 1
    extra = max(0, min(MAX_FIDELITY_STEPS - count, round(count * 0.30)))
    view = simulation(route)
    search_tone = target_array(target, view.shape)
    # More black thread is useful only when the current board is too light.
    if view.mean() / 255 - search_tone.mean() < 0.025:
        extra = 0
    candidate = density_search(route, target, 380 if extra else 48, alive, extra)
    winner = finish_candidate(route, candidate, target, alive)
    return winner if winner is not None else route
