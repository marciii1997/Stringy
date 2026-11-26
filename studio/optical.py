"""Propose degree-preserving winding edits using the rendered thread image."""

from io import BytesIO

import cv2
import numpy as np
from PIL import Image


class OpticalRefinement:
    def __init__(self, route, target, dims):
        settings = route["settings"]
        image = Image.open(BytesIO(target)).convert("L")
        image.thumbnail((512, 512))
        self.target = np.asarray(image, dtype=np.float32) / 255
        self.white = self.target >= 245 / 255
        pins = np.array(settings["nail_positions"])
        mm = pins * np.array(dims) / 2
        self.legal = np.zeros((len(mm), len(mm)), bool)
        self.center_gap = np.zeros_like(self.legal, dtype=float)
        clearance = settings["shaft_mm"] + settings["thread_mm"] + 0.5
        for i in range(len(mm) - 1):
            vectors = mm[i + 1:] - mm[i]
            lengths = np.sum(vectors ** 2, axis=1)
            offsets = mm - mm[i]
            along = np.clip(np.einsum("ik,jk->ij", vectors, offsets) / np.maximum(lengths[:, None], 1e-12), 0, 1)
            distance = np.sum((offsets - vectors[:, None] * along[:, :, None]) ** 2, axis=2)
            distance[:, i] = np.inf
            rows = np.arange(len(vectors))
            distance[rows, rows + i + 1] = np.inf
            self.center_gap[i, i+1:] = np.sqrt(distance.min(axis=1))
            self.legal[i, i + 1:] = (distance.min(axis=1) > clearance**2) & (lengths >= settings["min_segment_mm"]**2)
        self.legal |= self.legal.T.copy()
        self.center_gap += self.center_gap.T.copy()
        self.a, self.b = np.where(np.triu(self.legal, 1))
        xy = (pins + 1) / 2 * (np.array(image.size) - 1)
        delta = xy[self.b] - xy[self.a]
        self.length = np.linalg.norm(delta, axis=1)
        fractions = (np.arange(72) + 0.5) / 72
        samples = np.rint(xy[self.a, None] + delta[:, None] * fractions[None, :, None]).astype(int)
        self.indices = samples[:, :, 1] * image.width + samples[:, :, 0]
        self.limit = settings["max_chord_uses"]
        self.rng = np.random.default_rng(20261125)

    def chord_gains(self, rendered, physical, local_weight, regions=()):
        height, width = self.target.shape
        view = cv2.resize(rendered, (width, height), interpolation=cv2.INTER_AREA).astype(np.float32) / 255
        smooth = lambda image: cv2.GaussianBlur(image, (0, 0), 1.5)
        reference, simulated = smooth(self.target), smooth(view)
        residual = reference - simulated
        # Normalize local contrast so edges in faces or fur also influence the gradient.
        local = residual - cv2.GaussianBlur(residual, (0, 0), 6)
        gradient = residual + 1.5 * local
        gradient[self.white] += 0.15
        if physical:
            # Derivative of normalized correlation on fine detail, plus whole-image
            # tone and highlights. Scale each by its area so white sky stays clear.
            def derivative(x, y):
                xc, yc = x-x.mean(), y-y.mean()
                sx, sy = max(.01, x.std()), max(.01, y.std())
                return xc/(sx*sy) - np.mean(xc*yc)*yc/(sx*sy**3)
            high_target = reference - cv2.GaussianBlur(reference, (0,0), 7)
            high_sim = simulated - cv2.GaussianBlur(simulated, (0,0), 7)
            detail = derivative(high_target, high_sim)
            gradient = .5*derivative(reference, simulated) + .4*residual/max(.01, np.sqrt(np.mean(residual**2)))
            gradient += .5*(detail-cv2.GaussianBlur(detail, (0,0), 7))
            if local_weight:
                from studio.physical import detail_windows
                windows = list(detail_windows(reference))
                for box in windows:
                    area = len(windows) * reference[box].size
                    gradient[box] += local_weight*reference.size/area*derivative(reference[box], simulated[box])
            for _, (x,y,u,v) in regions:
                box = np.s_[int(y*height):int(v*height), int(x*width):int(u*width)]
                gradient[box] += reference.size/(len(regions)*reference[box].size)*derivative(reference[box], simulated[box])
            white = reference >= .94
            gradient[white] += 1.25/max(.01, white.mean())
        sensitivity = (smooth(gradient) * view).ravel()
        gain = np.full(self.legal.shape, -np.inf)
        gain[self.a, self.b] = gain[self.b, self.a] = -sensitivity[self.indices].mean(axis=1) * self.length

        return gain

    def propose(self, winding, rendered, exchanges, three_cut=False, physical=False, local_weight=0., regions=()):
        gain = self.chord_gains(rendered, physical, local_weight, regions)
        path = np.array(winding)
        counts = np.zeros_like(gain, dtype=int)
        np.add.at(counts, (path[:-1], path[1:]), 1)
        counts += counts.T.copy()
        for _ in range(exchanges):
            if three_cut:
                i, j, k = np.sort(self.rng.integers(0, len(path)-1, (3, 12000)), axis=0)
                a,b,c,d,e,f = path[i],path[i+1],path[j],path[j+1],path[k],path[k+1]
                distinct = np.all(np.diff(np.sort(np.stack((a,b,c,d,e,f)), axis=0), axis=0) != 0, axis=0)
                valid = distinct & self.legal[a,d] & self.legal[e,b] & self.legal[c,f]
                valid &= (counts[a,d]<self.limit) & (counts[e,b]<self.limit) & (counts[c,f]<self.limit)
                candidates = np.flatnonzero(valid)
                if candidates.size:
                    changes = (gain[a,d]+gain[e,b]+gain[c,f])[candidates] - (gain[a,b]+gain[c,d]+gain[e,f])[candidates]
                    n = candidates[np.argmax(changes)]
                    if changes.max() > 0:
                        for u,v,delta in ((a,b,-1),(c,d,-1),(e,f,-1),(a,d,1),(e,b,1),(c,f,1)):
                            counts[u[n],v[n]] += delta
                            counts[v[n],u[n]] += delta
                        left, middle, right = i[n]+1,j[n]+1,k[n]+1
                        path[left:right] = np.concatenate((path[middle:right],path[left:middle]))
                continue
            cuts = self.rng.integers(0, len(path) - 1, (2, 12000))
            left, right = np.sort(cuts, axis=0)
            a, b, c, d = path[left], path[left + 1], path[right], path[right + 1]
            feasible = (right > left + 1) & (a != d) & (b != c) & self.legal[a, c] & self.legal[b, d]
            feasible &= (counts[a, c] < self.limit) & (counts[b, d] < self.limit)
            valid = np.flatnonzero(feasible)
            if not valid.size:
                continue
            a, b, c, d = a[valid], b[valid], c[valid], d[valid]
            change = gain[a, c] + gain[b, d] - gain[a, b] - gain[c, d]
            j = np.argmax(change)
            if change[j] <= 0:
                continue
            for u, v, diff in ((a[j], b[j], -1), (c[j], d[j], -1), (a[j], c[j], 1), (b[j], d[j], 1)):
                counts[u, v] += diff
                counts[v, u] += diff
            start, end = left[valid[j]] + 1, right[valid[j]] + 1
            path[start:end] = path[start:end][::-1]
        return path.tolist()
