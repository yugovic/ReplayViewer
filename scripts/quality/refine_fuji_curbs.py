"""Regularize native-orthophoto candidates without imposing a constant width.

Coordinates remain in world XZ. No AI imagery or map tiles are inputs.
Every shift is capped, recorded, and remains an image-derived estimate.
"""
import numpy as np
from scipy.sparse import diags
from scipy.sparse.linalg import spsolve
from scipy.ndimage import gaussian_filter1d


def regularize(values, strength, weights=None):
    values = np.asarray(values, dtype=float)
    n = len(values)
    if n < 5:
        return values.copy()
    weights = np.ones(n) if weights is None else np.asarray(weights, dtype=float)
    diff = diags([np.ones(n-2), -2*np.ones(n-2), np.ones(n-2)], [0, 1, 2], shape=(n-2, n), format='csc')
    # Second differences penalize small zigzags while allowing corner curvature.
    matrix = diags(weights, format='csc') + strength * (diff.T @ diff)
    return spsolve(matrix, weights[:, None] * values if values.ndim == 2 else weights * values)


def refine_source_curbs(data, sample):
    stats = []
    for index, curb in enumerate(data['curbs']):
        rows = np.asarray(curb['rows'], dtype=float)
        inside, outside = rows[:, 1:3], rows[:, 3:5]
        midpoint = (inside + outside) / 2
        width = np.linalg.norm(outside - inside, axis=1)
        weights = np.where(rows[:, 8] > 0, 1., .35)
        centre = regularize(midpoint, 180., weights)
        # Cap the entire fit uniformly rather than clipping individual vertices,
        # which would introduce new kinks at a clipped sample.
        delta = centre - midpoint
        delta *= min(1., .30 / max(np.linalg.norm(delta, axis=1).max(), 1e-9))
        centre = midpoint + delta
        # Preserve detected red cross-section widths exactly. Smoothing all
        # widths biased peaks narrower; only bridge unobserved white/shadow gaps.
        fitted_width = regularize(width, 100., np.where(rows[:, 8] > 0, 1e8, 1.))
        fitted_width[rows[:, 8] > 0] = width[rows[:, 8] > 0]
        dw = fitted_width - width
        dw *= min(1., .24 / max(np.abs(dw).max(), 1e-9))
        fitted_width = width + dw
        tangent = np.gradient(centre, rows[:, 0], axis=0)
        normal = np.column_stack([-tangent[:, 1], tangent[:, 0]])
        normal /= np.linalg.norm(normal, axis=1)[:, None]
        normal *= np.where(np.sum(normal * (outside-inside), axis=1) >= 0, 1., -1.)[:, None]
        new_inside = centre - normal * fitted_width[:, None] / 2
        new_outside = centre + normal * fitted_width[:, None] / 2
        # Read paint in the central part of the curb, avoiding the narrow red
        # rim along WHITE slabs. Never synthesize a periodic red/white pattern.
        t = np.linspace(.30, .70, 5)
        samples = new_inside[:, None, :] + (new_outside-new_inside)[:, None, :] * t[None, :, None]
        rgb = sample(samples[:, :, 0], samples[:, :, 1]).astype(float)
        r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
        score = np.mean((r-g > 22) & (r-b > 18) & (r > 120) & (g < 175), axis=1)
        score = gaussian_filter1d(score, .65)
        colors = (score >= .5).astype(int)
        shifts = np.maximum(np.linalg.norm(new_inside-inside, axis=1), np.linalg.norm(new_outside-outside, axis=1))
        for j, row in enumerate(curb['rows']):
            row[1:5] = np.concatenate([new_inside[j], new_outside[j]]).round(3).tolist()
            row[7] = int(colors[j])
            # Column 8 means raw detection existed; fitting provenance is below.
        stats.append(dict(index=index, side=curb['side'], start=float(rows[0, 0]), end=float(rows[-1, 0]),
            maxBoundaryShiftMeters=round(float(shifts.max()), 4),
            medianBoundaryShiftMeters=round(float(np.median(shifts)), 4),
            maxWidthChangeMeters=round(float(np.abs(dw).max()), 4),
            widthRangeMeters=[round(float(fitted_width.min()), 4), round(float(fitted_width.max()), 4)],
            paintChangedStations=int(np.count_nonzero(colors != rows[:, 7])),
            centreSecondDifferenceRmsBefore=float(np.sqrt(np.mean(np.diff(midpoint, n=2, axis=0)**2))),
            centreSecondDifferenceRmsAfter=float(np.sqrt(np.mean(np.diff(centre, n=2, axis=0)**2)))))
    data['refinement'] = dict(method='World-XZ second-difference regularization; observed red-paint weighted; variable width retained; centre paint resampled from native orthophoto',
        maxCentreShiftMeters=.30, maxWidthChangeMeters=.24, independentlySurveyed=False, spans=stats)
    return stats


def connect_road_to_curbs(data):
    """Smooth image-edge jitter and join each curb with a tangent-continuous apron.

    The curb inner edge IS the road boundary. The previous 8cm gap exposed
    terrain, and a weighted offset left a hooked road edge at curb endpoints.
    """
    road = data['road']
    q = np.asarray(road)
    changes = []
    for col, side in [(7, 'left'), (9, 'right')]:
        # Refine the displayed hairpin excerpt only; retain distant road edges.
        ids = np.flatnonzero((q[:, 0] >= 1780) & (q[:, 0] <= 2670))
        xy = q[ids, col:col+2]
        fit = regularize(xy, 220.)
        delta = fit-xy
        length = np.linalg.norm(delta, axis=1)
        delta *= np.minimum(1., .35/np.maximum(length, 1e-9))[:, None]
        fade = np.minimum(1., np.minimum(np.arange(len(ids)), np.arange(len(ids))[::-1])/20)
        for k, i in enumerate(ids):
            road[i][col:col+2] = (xy[k]+delta[k]*fade[k]).round(3).tolist()
        changes.append(dict(side=side, start=1780, end=2670, maxShiftMeters=float(np.linalg.norm(delta*fade[:, None],axis=1).max())))
        occupied = {round(r[0]/.5) for c in data['curbs'] if c['side'] == side for r in c['rows']}
        for curb in data['curbs']:
            if curb['side'] != side:
                continue
            cr = np.asarray(curb['rows'])
            for row in cr:
                road[round(row[0]/.5)][col:col+2] = row[1:3].tolist()
            for endpoint, direction in [(0, -1), (-1, 1)]:
                index = round(cr[endpoint, 0]/.5)
                available = 0
                for step in range(1, 13):
                    j = index+direction*step
                    if j <= 1 or j >= len(road)-2 or j in occupied:
                        break
                    available = step
                if available < 3:
                    continue
                anchor = index+direction*available
                a = np.array(road[anchor][col:col+2])
                b = cr[endpoint, 1:3]
                # Derivatives in the direction anchor -> curb (per station).
                da = (np.array(road[anchor-direction][col:col+2])-np.array(road[anchor+direction][col:col+2]))/2
                # At start, travel forward; at end, travel backwards.
                db = (cr[3, 1:3]-cr[0, 1:3])/3 if direction == -1 else (cr[-4, 1:3]-cr[-1, 1:3])/3
                for k in range(1, available):
                    t = k/available
                    point = (2*t**3-3*t*t+1)*a + (t**3-2*t*t+t)*available*da + (-2*t**3+3*t*t)*b + (t**3-t*t)*available*db
                    road[anchor-direction*k][col:col+2] = point.round(3).tolist()
    data['refinement']['roadEdgeFits'] = changes
