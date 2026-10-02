#!/usr/bin/env python3
"""Opt-in kerb-contact registration CANDIDATE (BACKLOG GPS-REG-15).

Why
---
`register_gps_to_track.py` (the shipped registration) fits each lap so the
tyres stay inside the traced road. Inside the road that cost is flat over
0.5-1 m, so the wheel-to-kerb distance at the clipping points is not
determined by it (docs/fuji-apex-kerb-kpi-2026-10-02.md). Logger-native kerb
contact labels (public/data/races/<race>/kerb_contacts.json, roll-rate bursts,
no left/right side) add one-sided evidence: a strong pass means the inner
tyres reached the red/white blocks, a silent pass means they were not deep on
them. Review decision (2026-10-02): this is an OPT-IN 7/30 candidate layer
(viewer `?gps=kerb`), never a replacement of gps_registration.json.

What
----
    J(s, d_l) = sum_laps [ T_l(d_l) + W * sum_labels w_i * rho(v_i(d_l))
                           + 0.5 |d_l - s|^2 / sigma_lap^2 ]
    T_l   : Huber violation of the 10 Hz tyre points vs the road limits
            (geometry.json; at kerbs the road edge is the paint start of
            cg_study/kerb_zones.json, the dry edge adds kerbs missing in the CG)
    v_i   : hinge violation of label i; g = min over the inner front/rear tyre
            outer edge of the signed gap to the kerb BLOCK start (kerb_zones.json;
            negative = on the blocks; NaN beyond the line end vertices)
              strong: v = max(0, g - strongMaxGap)    (default 0.0 m)
              silent: v = max(0, silentMinGap - g)    (default -0.40 m)
    rho   : robust loss on v (default Huber, delta 0.15 m: v^2 inside, 2*delta*v - delta^2
            beyond), so one contradicting label cannot drag the translation
    s     : session translation (fitted first with d_l = s), then each lap
            deviation with the Gaussian prior sigma_lap

GPS anomalies are FLAGGED, not absorbed: labels inside a known anomalous
lap segment (default for 7/30: lap 2, 3250-3900 m) are excluded from the fit
(--anomaly-treatment exclude, default) or down-weighted (downweight); they stay
listed in `labelsUsed` with the reason. Labels still violated after the fit
are listed in `unresolvedLabels`. The label-feasible region (offsets that
satisfy every used label, intersected with the track-limit near-optimal set)
is published per session and lap.

Races with too few strong labels (7/29: one strong contact) are refused: the
strong labels are the only evidence on one side of the hinge, so the fit would
be pinned by silent labels alone and could not be validated.

The output keeps the gps_registration.json contract (kind lap-translation,
dataSha256 of the current lap files, limits + referenceTrack SHA-256) plus
metadata. Raw lat/lng are never modified. Not a survey.

Usage
-----
    python pipeline/register_gps_kerb_contact.py --track fuji --race fuji_aim_2020_07_30 \
        --surface dry --vehicle-profile public/data/vehicles/mazda2-dj.json
    (writes public/data/races/<race>/gps_registration_kerb.json)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy.interpolate import CubicSpline
from scipy.optimize import minimize

import register_gps_to_track as base

ROOT = Path(__file__).resolve().parents[1]
TOOL = "pipeline/register_gps_kerb_contact.py"
OUT_NAME = "gps_registration_kerb.json"

STRONG_MAX_GAP = 0.0
SILENT_MIN_GAP = -0.40
LABEL_WEIGHT = 50.0
LABEL_LOSS = "huber"
LABEL_DELTA = 0.15
SIGMA_LAP = 0.25
ANOMALY_WEIGHT = 0.25
PASS_MARGIN_M = 3.0          # pass window = kerb station range +- this (as build_apex_kpi.py)
DT = 0.02                    # 50 Hz wheel paths for kerb gaps
PRUNE_RADIUS_M = 4.0         # exact pruning radius around the search centre
MIN_STRONG_LABELS = 5
MIN_STRONG_KERBS = 3
CONTRADICTION_TOL = 0.15     # evaluation tolerance (strong g > +0.15, silent g < -0.55)
ABS_GAP_INTERVAL_MIN_M = 0.7
FEASIBLE_STEP_M = 0.05
FEASIBLE_HALF_M = 1.5
FEASIBLE_TAU_PER_LAP = 0.05  # track-limit cost within min + tau counts as near-optimal

# Lap segments whose GPS is known to carry a lap-internal, location-dependent
# lateral error (a rigid translation cannot represent it). Evidence: report v3.
KNOWN_GPS_ANOMALIES = {
    "fuji_aim_2020_07_30": [{
        "lap": 2,
        "stationRange": [3250.0, 3900.0],
        "kind": "lap-internal lateral GPS error",
        "evidence": [
            "GPS lateral motion leads the steering return by about 0.3 s in this sector",
            "strong roll-rate vibration at 3580-3600 m that L3 at the same displayed position does not show",
            "opposite-sign kerb-label contradictions at k21 (strong but off) and k24 (silent but deep)",
        ],
        "source": "docs/fuji-apex-kerb-kpi-2026-10-02.md",
    }],
}


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def rel(path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return Path(path).name


# ---------------------------------------------------------------- course zones
def load_zones(path: Path) -> dict:
    """kerb_zones.json -> id -> side/sign/station range/paint/block/outer boundaries."""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    zones = {}
    for kb in doc["kerbs"]:
        st = np.asarray(kb["station"], float)
        zones[kb["id"]] = dict(
            id=kb["id"], side=kb["side"], sign=+1 if kb["side"] == "left" else -1,
            s0=float(st[0]), s1=float(st[-1]), station=st,
            paintXY=np.asarray(kb["paint"], float), outerXY=np.asarray(kb["outer"], float),
            addedOutsideCg=bool(kb.get("addedOutsideCg")),
            block=base.Boundary.from_vertices(np.asarray(kb["block"], float)))
    return zones


def zoned_limits(limits: base.Limits, zones: dict) -> base.Limits:
    """Road edge -> kerb paint start at kerbs; dry edge -> kerb outer end where the CG has no kerb.
    Same rule as scripts/quality/apex_kpi/build_apex_kpi.py zoned_limits()."""
    rl, rr = limits.road_left.vertices.copy(), limits.road_right.vertices.copy()
    dl, dr = limits.dry_left.vertices.copy(), limits.dry_right.vertices.copy()
    step = float(np.median(np.diff(limits.dist)))
    for z in zones.values():
        left = z["side"] == "left"
        for s, p, o in zip(z["station"], z["paintXY"], z["outerXY"]):
            i = int(round(s / step))
            if 0 <= i < len(rl):
                (rl if left else rr)[i] = p
                if z["addedOutsideCg"]:
                    (dl if left else dr)[i] = o
    out = base.Limits(**{f: getattr(limits, f) for f in limits.__dataclass_fields__})
    out.road_left, out.road_right = base.Boundary.from_vertices(rl), base.Boundary.from_vertices(rr)
    out.dry_left, out.dry_right = base.Boundary.from_vertices(dl), base.Boundary.from_vertices(dr)
    return out


def line_gap(line: base.Boundary, points: np.ndarray, sign: int) -> np.ndarray:
    """Signed gap to a kerb line; NaN where the point projects beyond the end vertices
    (as build_apex_kpi.line_gap: the clipped end-segment distance flips sign there)."""
    g = line.signed_gap(points, sign)
    _, idx = line.tree.query(points)
    g[(idx <= 0) | (idx >= len(line.vertices) - 1)] = np.nan
    return g


# ---------------------------------------------------------------- parameters
@dataclass(frozen=True)
class Params:
    label_weight: float = LABEL_WEIGHT
    loss: str = LABEL_LOSS
    delta: float = LABEL_DELTA
    strong_max_gap: float = STRONG_MAX_GAP
    silent_min_gap: float = SILENT_MIN_GAP
    sigma_lap: float = SIGMA_LAP
    anomaly_treatment: str = "exclude"     # exclude | downweight | include
    anomaly_weight: float = ANOMALY_WEIGHT


def rho(v: float, loss: str, delta: float) -> float:
    """Robust loss on a hinge violation v >= 0; all three agree (v^2) for v << delta."""
    if v <= 0.0:
        return 0.0
    if loss == "squared":
        return v * v
    if loss == "huber":
        return v * v if v <= delta else 2.0 * delta * v - delta * delta
    if loss == "soft_l1":
        return 2.0 * delta * delta * (math.sqrt(1.0 + (v / delta) ** 2) - 1.0)
    raise ValueError(f"unknown loss {loss}")


def violation(cls: str, g: float | None, p: Params) -> float:
    if g is None or not np.isfinite(g):
        return 0.0
    return max(0.0, g - p.strong_max_gap) if cls == "strong" else max(0.0, p.silent_min_gap - g)


# ---------------------------------------------------------------- lap inputs
def robust_signed_gap(line: base.Boundary, inward: np.ndarray, points: np.ndarray) -> np.ndarray:
    """Distance to the closest rendered segment (as Boundary.signed_gap), but the SIGN comes from the
    inward road normal of that segment instead of the segment direction. They agree on ordinary edge
    segments; at a transverse step (e.g. where a kerb outer edge rejoins the road edge) the segment
    direction is across the track and Boundary.signed_gap flips points metres inside the road to
    'beyond the limit'. inward: (n,2) unit vectors per vertex pointing to the road side."""
    q = np.asarray(points, float).reshape(-1, 2)
    if not len(q):
        return np.empty(0)
    V = line.vertices
    _, near = line.tree.query(q, k=min(4, len(V)))
    near = np.atleast_2d(near).reshape(len(q), -1)
    segment = np.concatenate((np.maximum(near - 1, 0), np.minimum(near, len(V) - 2)), axis=1)
    a, b = V[segment], V[segment + 1]
    v = b - a
    along = np.clip(np.einsum("ijk,ijk->ij", q[:, None, :] - a, v) /
                    np.maximum(np.einsum("ijk,ijk->ij", v, v), 1e-12), 0, 1)
    residual = q[:, None, :] - (a + along[:, :, None] * v)
    squared = np.einsum("ijk,ijk->ij", residual, residual)
    choose = np.argmin(squared, axis=1)
    row = np.arange(len(q))
    seg, al = segment[row, choose], along[row, choose]
    n_in = (1 - al)[:, None] * inward[seg] + al[:, None] * inward[seg + 1]
    s = np.sign(np.einsum("ij,ij->i", residual[row, choose], n_in))
    s[s == 0] = 1.0
    return np.sqrt(squared[row, choose]) * s


class _Pruned:
    """Track term of one road side with exact pruning: a point whose gap at the search centre exceeds
    the radius cannot reach the limit for any offset within that radius (the robust sign only changes
    when the point crosses the edge)."""

    def __init__(self, line: base.Boundary, inward: np.ndarray, points: np.ndarray, centre, radius: float):
        self.line, self.inward, self.all = line, inward, points
        self.centre, self.radius = np.asarray(centre, float), radius
        g = self.gap(points + self.centre)
        self.points = points[g < radius + 0.05]

    def gap(self, pts: np.ndarray) -> np.ndarray:
        return robust_signed_gap(self.line, self.inward, pts)

    def cost(self, d) -> float:
        d = np.asarray(d, float)
        pts = self.points if np.hypot(*(d - self.centre)) <= self.radius else self.all
        if not len(pts):
            return 0.0
        return float(base.huber(np.maximum(0.0, -self.gap(pts + d))).sum())


class LapInput:
    """One shipped lap: 10 Hz tyre points for the track term, 50 Hz inner-tyre paths per kerb pass."""

    def __init__(self, record: dict, path: Path, origin: dict, limits: base.Limits, curb_use: float,
                 geometry: dict, zones: dict, ref_offset, centre, radius: float = PRUNE_RADIUS_M):
        self.record, self.path, self.lap = record, path, int(record["lap"])
        self.sha = sha256(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        t = np.asarray(data["t"], float)
        xy = base.to_local(data["lat"], data["lng"], origin)
        self.t, self.xy = t, xy
        self.keep = base.racing_mask(limits, xy, t)
        self.w10 = base.wheel_points(xy, t, (0.0, 0.0), geometry)
        pts = self.w10[self.keep].reshape(-1, 2)
        left = limits.dry_left if curb_use else limits.road_left
        right = limits.dry_right if curb_use else limits.road_right
        # limits.normal points to the driver's right: road side of the left edge = +normal
        self.sides = (_Pruned(left, limits.normal, pts, centre, radius),
                      _Pruned(right, -limits.normal, pts, centre, radius))
        # 50 Hz centre/tyre paths (build_apex_kpi.LapPaths), antenna at the vehicle centre
        cx, cz = CubicSpline(t, xy[:, 0]), CubicSpline(t, xy[:, 1])
        self.t50 = np.arange(t[0], t[-1], DT)
        p = np.stack([cx(self.t50), cz(self.t50)], 1)
        hv = np.stack([cx(self.t50, 1), cz(self.t50, 1)], 1)
        hv /= np.maximum(np.linalg.norm(hv, axis=1), 1e-6)[:, None]
        r = np.stack([-hv[:, 1], hv[:, 0]], 1)
        hl = geometry["wheelbaseMeters"] / 2
        hf = (geometry["frontTrackMeters"] + geometry["frontTyreWidthMeters"]) / 2
        hr = (geometry["rearTrackMeters"] + geometry["rearTyreWidthMeters"]) / 2
        self.W50 = np.stack([p + hl * hv - hf * r, p + hl * hv + hf * r, p - hl * hv - hr * r, p - hl * hv + hr * r], 1)
        self.p50 = p
        idx, _ = limits.lateral(p + np.asarray(ref_offset, float))
        self.st50 = limits.dist[idx]          # stations at the reference offset: pass windows only
        self.passes = {}
        c = np.asarray(centre, float)
        for kid, z in zones.items():
            m = (self.st50 >= z["s0"] - PASS_MARGIN_M) & (self.st50 <= z["s1"] + PASS_MARGIN_M)
            if m.sum() < 5:
                continue
            fi, ri = (0, 2) if z["sign"] > 0 else (1, 3)
            P = self.W50[m][:, [fi, ri]].reshape(-1, 2)
            g0 = line_gap(z["block"], P + c, z["sign"])
            if np.isfinite(g0).any():
                keep = ~np.isfinite(g0) | (g0 <= np.nanmin(g0) + 2 * radius + 0.05)
            else:
                keep = np.ones(len(P), bool)
            self.passes[kid] = dict(all=P, pts=P[keep], zone=z, centre=c, radius=radius,
                                    window=(float(self.t50[m][0]), float(self.t50[m][-1])))

    def track_cost(self, d) -> float:
        return self.sides[0].cost(d) + self.sides[1].cost(d)

    def gap(self, kid: str, d) -> float | None:
        """min over the inner front/rear tyre of the gap to the block start (m); None if not measurable."""
        ps = self.passes.get(kid)
        if ps is None:
            return None
        d = np.asarray(d, float)
        pts = ps["pts"] if np.hypot(*(d - ps["centre"])) <= ps["radius"] else ps["all"]
        g = line_gap(ps["zone"]["block"], pts + d, ps["zone"]["sign"])
        return float(np.nanmin(g)) if np.isfinite(g).any() else None

    def gap_grid(self, kid: str, D: np.ndarray, chunk: int = 400) -> np.ndarray:
        """gap() for many offsets (m,2) at once (all pass samples, no pruning)."""
        ps = self.passes[kid]
        P = ps["all"]
        out = np.full(len(D), np.nan)
        for a in range(0, len(D), chunk):
            Dc = D[a:a + chunk]
            Q = (P[None, :, :] + Dc[:, None, :]).reshape(-1, 2)
            g = line_gap(ps["zone"]["block"], Q, ps["zone"]["sign"]).reshape(len(Dc), len(P))
            with np.errstate(all="ignore"):
                out[a:a + chunk] = np.where(np.isfinite(g).any(1), np.nanmin(np.where(np.isfinite(g), g, np.inf), 1), np.nan)
        return out

    def track_cost_grid(self, D: np.ndarray, chunk: int = 200) -> np.ndarray:
        """track_cost() for many offsets (m,2); pruned exactly around the grid centre."""
        centre = D.mean(0)
        radius = float(np.max(np.hypot(*(D - centre).T))) + 0.05
        out = np.zeros(len(D))
        for side in self.sides:
            g0 = side.gap(side.all + centre)
            P = side.all[g0 < radius + 0.05]
            if not len(P):
                continue
            for a in range(0, len(D), chunk):
                Dc = D[a:a + chunk]
                Q = (P[None, :, :] + Dc[:, None, :]).reshape(-1, 2)
                g = side.gap(Q).reshape(len(Dc), len(P))
                out[a:a + chunk] += base.huber(np.maximum(0.0, -g)).sum(1)
        return out


# ---------------------------------------------------------------- labels
def anomaly_for(lap: int, zone: dict, anomalies: list) -> dict | None:
    for a in anomalies:
        s0, s1 = a["stationRange"]
        if a["lap"] == lap and zone["s0"] <= s1 and zone["s1"] >= s0:
            return a
    return None


def build_labels(contacts: dict, zones: dict, laps: dict, anomalies: list, treatment: str,
                 anomaly_weight: float = ANOMALY_WEIGHT) -> list[dict]:
    """Every strong/silent inside-kerb label of kerb_contacts.json with its fit weight
    (0 = not used) and the reason. Mild passes carry no hinge evidence and are not listed."""
    if treatment not in ("exclude", "downweight", "include"):
        raise ValueError(f"unknown anomaly treatment {treatment}")
    out = []
    for lap_key, rows in sorted(contacts["laps"].items(), key=lambda kv: int(kv[0])):
        lap_no = int(lap_key)
        for r in rows:
            cls = r.get("class")
            if cls not in ("strong", "silent"):
                continue
            kid = r.get("kerb")
            z = zones.get(kid)
            lab = {"lap": lap_no, "kerb": kid, "class": cls, "peakRollRms": r.get("peakRollRms"), "bin": r.get("bin"),
                   "weight": 1.0, "used": True, "reason": None, "anomaly": False}
            if z is None:
                lab.update(weight=0.0, used=False, reason="kerb id not in kerb_zones.json")
            elif lap_no not in laps:
                lab.update(weight=0.0, used=False, reason="lap not in laps.json")
            elif kid not in laps[lap_no].passes:
                lab.update(weight=0.0, used=False, reason="no pass samples")
            else:
                lab["stationRange"] = [z["s0"], z["s1"]]
                a = anomaly_for(lap_no, z, anomalies)
                if a is not None:
                    lab["anomaly"] = True
                    seg = f'GPS anomaly lap {a["lap"]} {a["stationRange"][0]:.0f}-{a["stationRange"][1]:.0f} m'
                    if treatment == "exclude":
                        lab.update(weight=0.0, used=False, reason=f"{seg}: excluded from the fit (flagged, not absorbed)")
                    elif treatment == "downweight":
                        lab.update(weight=float(anomaly_weight), reason=f"{seg}: down-weighted x{anomaly_weight:g}")
                    else:
                        lab["reason"] = f"{seg}: included at full weight (sensitivity run)"
            out.append(lab)
    return out


# ---------------------------------------------------------------- problem
@dataclass
class Problem:
    laps: dict                         # lap number -> LapInput
    labels: list
    params: Params = field(default_factory=Params)

    def label_cost(self, lap_no: int, d, exclude=None) -> float:
        lap = self.laps[lap_no]
        c = 0.0
        for lab in self.labels:
            if lab["lap"] != lap_no or lab["weight"] <= 0 or (exclude and exclude(lab)):
                continue
            v = violation(lab["class"], lap.gap(lab["kerb"], d), self.params)
            c += self.params.label_weight * lab["weight"] * rho(v, self.params.loss, self.params.delta)
        return c

    def session_cost(self, d, exclude=None, use_labels=True) -> float:
        c = sum(lap.track_cost(d) for lap in self.laps.values())
        if use_labels:
            c += sum(self.label_cost(n, d, exclude) for n in self.laps)
        return c

    def lap_cost(self, lap_no: int, d, session, exclude=None, use_labels=True) -> float:
        dev = np.asarray(d, float) - np.asarray(session, float)
        c = self.laps[lap_no].track_cost(d) + 0.5 * float(dev @ dev) / self.params.sigma_lap ** 2
        if use_labels:
            c += self.label_cost(lap_no, d, exclude)
        return c

    def fit_session(self, x0, exclude=None, use_labels=True, spread=0.5, grid=None) -> np.ndarray:
        """Nelder-Mead from x0 and four neighbours (and the best points of an optional grid)."""
        f = lambda d: self.session_cost(d, exclude, use_labels)
        x0 = np.asarray(x0, float)
        starts = [x0] + [x0 + spread * np.array(v) for v in ((1, 0), (-1, 0), (0, 1), (0, -1))]
        if grid is not None:
            vals = sorted((f(g), tuple(g)) for g in grid)
            starts = [np.array(g) for _, g in vals[:3]] + starts
        best = None
        for s in starts:
            r = minimize(f, s, method="Nelder-Mead", options={"xatol": 1e-3, "fatol": 1e-6, "maxiter": 800})
            if best is None or r.fun < best.fun - 1e-9:
                best = r
        return best.x

    def fit_lap(self, lap_no: int, session, exclude=None, use_labels=True) -> np.ndarray:
        f = lambda d: self.lap_cost(lap_no, d, session, exclude, use_labels)
        r = minimize(f, np.asarray(session, float), method="Nelder-Mead",
                     options={"xatol": 1e-3, "fatol": 1e-6, "maxiter": 800})
        return r.x

    def fit(self, x0, exclude=None, use_labels=True, grid=None) -> tuple[np.ndarray, dict]:
        s = self.fit_session(x0, exclude, use_labels, grid=grid)
        return s, {n: self.fit_lap(n, s, exclude, use_labels) for n in self.laps}


# ---------------------------------------------------------------- feasible regions
def region_stats(D: np.ndarray, mask: np.ndarray, step: float, half: float, centre) -> dict:
    pts = D[mask]
    if not len(pts):
        return {"empty": True, "points": 0}
    ext = pts.max(0) - pts.min(0) + step
    out = {"empty": False, "points": int(len(pts)), "areaM2": round(float(len(pts) * step * step), 3),
           "eastWidthM": round(float(ext[0]), 2), "southWidthM": round(float(ext[1]), 2),
           "centroid": [round(float(v), 3) for v in pts.mean(0)],
           "touchesWindow": bool((np.abs(pts - np.asarray(centre)) >= half - step / 2).any())}
    if len(pts) >= 3:
        w, v = np.linalg.eigh(np.cov((pts - pts.mean(0)).T))
        major, minor = v[:, 1], v[:, 0]
        pm, pn = (pts - pts.mean(0)) @ major, (pts - pts.mean(0)) @ minor
        out.update(majorWidthM=round(float(pm.max() - pm.min() + step), 2),
                   minorWidthM=round(float(pn.max() - pn.min() + step), 2),
                   majorAxisBearingDeg=round(float(math.degrees(math.atan2(major[0], -major[1])) % 180), 1))
    return out


def feasible_regions(problem: Problem, centre, half: float = FEASIBLE_HALF_M, step: float = FEASIBLE_STEP_M,
                     tols=(0.0, CONTRADICTION_TOL), lap_centres: dict | None = None) -> dict:
    """Track-limit near-optimal set, label-feasible set (every used label within tol) and their
    intersection, on a grid of offsets; per session (one translation) and per lap (own labels)."""
    def grid(c):
        ax = np.arange(-half, half + step / 2, step)
        X, Z = np.meshgrid(ax + c[0], ax + c[1], indexing="ij")
        return np.stack([X.ravel(), Z.ravel()], 1)

    def labels_ok(lap_nos, D):
        worst = np.zeros(len(D))
        n = 0
        for lab in problem.labels:
            if lab["lap"] not in lap_nos or lab["weight"] <= 0:
                continue
            g = problem.laps[lab["lap"]].gap_grid(lab["kerb"], D)
            v = np.where(np.isfinite(g), (g - problem.params.strong_max_gap) if lab["class"] == "strong"
                         else (problem.params.silent_min_gap - g), 0.0)
            worst = np.maximum(worst, np.maximum(v, 0.0))
            n += 1
        return worst, n

    out = {"definition": (
        f"grid {step} m over +-{half} m around the fitted offset. trackNearOptimal: track-limit cost <= grid minimum + "
        f"{FEASIBLE_TAU_PER_LAP} per lap. labelFeasible[tol]: every used label has hinge violation <= tol "
        "(strong: g <= strongMaxGap + tol; silent: g >= silentMinGap - tol). touchesWindow = the set reaches the grid "
        "edge (width is a lower bound)."), "tolerancesMeters": list(tols)}
    c = np.asarray(centre, float)
    D = grid(c)
    T = sum(problem.laps[n].track_cost_grid(D) for n in problem.laps)
    track_ok = T <= T.min() + FEASIBLE_TAU_PER_LAP * len(problem.laps)
    worst, n = labels_ok(set(problem.laps), D)
    sess = {"centre": [round(float(v), 3) for v in c], "labels": n,
            "trackNearOptimal": region_stats(D, track_ok, step, half, c),
            "minWorstLabelViolationM": round(float(worst.min()), 3)}
    for tol in tols:
        sess[f"labelFeasible_tol{tol:g}"] = region_stats(D, worst <= tol, step, half, c)
        sess[f"trackAndLabel_tol{tol:g}"] = region_stats(D, track_ok & (worst <= tol), step, half, c)
    out["session"] = sess
    laps = {}
    for lap_no, lap in problem.laps.items():
        lc = np.asarray((lap_centres or {}).get(lap_no, c), float)
        D = grid(lc)
        T = lap.track_cost_grid(D)
        ok = T <= T.min() + FEASIBLE_TAU_PER_LAP
        worst, n = labels_ok({lap_no}, D)
        e = {"centre": [round(float(v), 3) for v in lc], "labels": n,
             "trackNearOptimal": region_stats(D, ok, step, half, lc),
             "minWorstLabelViolationM": round(float(worst.min()), 3)}
        for tol in tols:
            e[f"labelFeasible_tol{tol:g}"] = region_stats(D, worst <= tol, step, half, lc)
            e[f"trackAndLabel_tol{tol:g}"] = region_stats(D, ok & (worst <= tol), step, half, lc)
        laps[str(lap_no)] = e
    out["laps"] = laps
    return out


# ---------------------------------------------------------------- setup helpers
def strong_evidence(labels: list) -> tuple[int, int]:
    strong = [l for l in labels if l["class"] == "strong" and l["used"]]
    return len(strong), len({l["kerb"] for l in strong})


def beyond_limit_summary(lap: LapInput, raw_limits: base.Limits, d, curb_use: float) -> dict:
    """Seconds / max metres of tyres beyond the limit, measured exactly as gps_registration.json
    reports raw/registered (geometry.json limits, base margins, base racing mask)."""
    keep = base.racing_mask(raw_limits, lap.xy, lap.t)
    m = raw_limits.margins(lap.w10[keep] + np.asarray(d, float), curb_use).min(axis=1)
    dt = float(np.median(np.diff(lap.t)))
    return {"secondsBeyondLimit": round(float((m < -0.05).sum() * dt), 2),
            "maxBeyondLimitMeters": round(float(max(0.0, -m.min())), 3), "samples": int(keep.sum())}


def setup(track: str, race: str, surface: str, vehicle_profile: Path, zones_path: Path | None = None,
          contacts_path: Path | None = None, ref_registration: Path | None = None,
          anomalies: list | None = None, params: Params = Params()):
    """Load everything; returns a dict with problem, limits, paths and metadata (shared with the evaluation)."""
    track_dir = ROOT / "public/data/tracks" / track
    race_dir = ROOT / "public/data/races" / race
    zones_path = Path(zones_path or track_dir / "cg_study/kerb_zones.json")
    contacts_path = Path(contacts_path or race_dir / "kerb_contacts.json")
    ref_registration = Path(ref_registration or race_dir / "gps_registration.json")
    track_doc = json.loads((track_dir / "track.json").read_text(encoding="utf-8"))
    zones = load_zones(zones_path)
    contacts = json.loads(contacts_path.read_text(encoding="utf-8"))
    for key in ("laps",):
        if key not in contacts:
            raise ValueError(f"{rel(contacts_path)}: missing '{key}' (expected export_inputs.py v2 schema)")
    raw_limits = base.load_limits(track_dir, 0.0)
    limits = zoned_limits(raw_limits, zones)
    curb_use = 1.0 if surface == "dry" else 0.0
    geometry = base.load_vehicle_geometry(Path(vehicle_profile))
    index = json.loads((race_dir / "laps.json").read_text(encoding="utf-8"))
    ref = {}
    ref_session = np.zeros(2)
    if ref_registration.exists():
        reg = json.loads(ref_registration.read_text(encoding="utf-8"))
        ref = {int(l["lap"]): np.asarray(l["offsetMeters"], float) for l in reg["laps"]}
        ref_session = np.asarray(reg.get("sessionOffsetMeters", np.mean(list(ref.values()), axis=0)), float)
    laps = {}
    for record in index["selected"]:
        path = race_dir / record["data_file"]
        n = int(record["lap"])
        laps[n] = LapInput(record, path, track_doc["origin"], limits, curb_use, geometry, zones,
                           ref.get(n, ref_session), ref_session)
    if anomalies is None:
        anomalies = KNOWN_GPS_ANOMALIES.get(race, [])
    labels = build_labels(contacts, zones, laps, anomalies, params.anomaly_treatment, params.anomaly_weight)
    return dict(problem=Problem(laps, labels, params), limits=limits, raw_limits=raw_limits, zones=zones, contacts=contacts,
                track_dir=track_dir, race_dir=race_dir, zones_path=zones_path, contacts_path=contacts_path,
                ref_registration=ref_registration, ref_offsets=ref, ref_session=ref_session, curb_use=curb_use,
                geometry=geometry, anomalies=anomalies, track_doc=track_doc, vehicle_profile=Path(vehicle_profile))


def parse_anomaly(text: str) -> dict:
    """LAP:S0-S1[:REASON]"""
    parts = text.split(":", 2)
    s0, s1 = (float(v) for v in parts[1].split("-"))
    return {"lap": int(parts[0]), "stationRange": [s0, s1], "kind": "user-flagged lateral GPS error",
            "evidence": [parts[2]] if len(parts) > 2 else [], "source": "command line"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--track", required=True)
    ap.add_argument("--race", required=True)
    ap.add_argument("--surface", choices=("dry", "wet"), required=True)
    ap.add_argument("--vehicle-profile", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=None, help=f"default: public/data/races/<race>/{OUT_NAME}")
    ap.add_argument("--zones", type=Path, default=None, help="kerb zones JSON (default: <track>/cg_study/kerb_zones.json)")
    ap.add_argument("--contacts", type=Path, default=None, help="kerb contacts JSON (default: <race>/kerb_contacts.json)")
    ap.add_argument("--label-weight", type=float, default=LABEL_WEIGHT)
    ap.add_argument("--loss", choices=("huber", "soft_l1", "squared"), default=LABEL_LOSS)
    ap.add_argument("--loss-delta", type=float, default=LABEL_DELTA)
    ap.add_argument("--strong-max-gap", type=float, default=STRONG_MAX_GAP)
    ap.add_argument("--silent-min-gap", type=float, default=SILENT_MIN_GAP)
    ap.add_argument("--sigma-lap", type=float, default=SIGMA_LAP)
    ap.add_argument("--anomaly", action="append", default=None, metavar="LAP:S0-S1[:REASON]",
                    help="flag a GPS-anomalous lap segment (replaces the built-in list for this race)")
    ap.add_argument("--anomaly-treatment", choices=("exclude", "downweight", "include"), default="exclude")
    ap.add_argument("--anomaly-weight", type=float, default=ANOMALY_WEIGHT)
    ap.add_argument("--min-strong-labels", type=int, default=MIN_STRONG_LABELS)
    ap.add_argument("--min-strong-kerbs", type=int, default=MIN_STRONG_KERBS)
    ap.add_argument("--evaluation", default=None, help="repo-relative path of the held-out evaluation to cite")
    ap.add_argument("--no-feasible", action="store_true", help="skip the feasible-region grid (faster)")
    args = ap.parse_args()

    params = Params(args.label_weight, args.loss, args.loss_delta, args.strong_max_gap, args.silent_min_gap,
                    args.sigma_lap, args.anomaly_treatment, args.anomaly_weight)
    anomalies = [parse_anomaly(a) for a in args.anomaly] if args.anomaly else None
    S = setup(args.track, args.race, args.surface, args.vehicle_profile, args.zones, args.contacts,
              anomalies=anomalies, params=params)
    problem: Problem = S["problem"]
    n_strong, n_strong_kerbs = strong_evidence(problem.labels)
    if n_strong < args.min_strong_labels or n_strong_kerbs < args.min_strong_kerbs:
        print(f"REFUSED {args.race}: {n_strong} usable strong labels on {n_strong_kerbs} kerbs "
              f"(need >= {args.min_strong_labels} on >= {args.min_strong_kerbs}). Without strong contacts the hinge "
              "is one-sided (silent labels only push the car away from the blocks), the offset stays on the flat "
              "track-limit set and no held-out strong label exists to validate it. No candidate written.")
        return 3

    centre = S["ref_session"]
    ax = np.arange(-3.0, 3.01, 0.5)
    grid = [centre + np.array([a, b]) for a in ax for b in ax]
    session, lap_offsets = problem.fit(centre, grid=grid)
    out_laps, unresolved, flags_by_lap = [], [], {}
    for a in S["anomalies"]:
        flags_by_lap.setdefault(a["lap"], []).append(
            f'gps-anomaly {a["stationRange"][0]:.0f}-{a["stationRange"][1]:.0f} m ({args.anomaly_treatment})')
    labels_out = []
    for lab in problem.labels:
        lap = problem.laps.get(lab["lap"])
        d = np.round(lap_offsets[lab["lap"]], 3) if lap else None
        g = lap.gap(lab["kerb"], d) if lap and lab["kerb"] in lap.passes else None
        v = violation(lab["class"], g, params)
        row = {k: lab[k] for k in ("lap", "kerb", "class", "peakRollRms", "bin", "used", "weight", "reason", "anomaly")}
        if "stationRange" in lab:
            row["stationRange"] = lab["stationRange"]
        row["gBlockMinAtFit"] = None if g is None else round(g, 3)
        row["violationAtFitM"] = round(v, 3)
        labels_out.append(row)
        if g is not None and ((lab["class"] == "strong" and g > CONTRADICTION_TOL) or
                              (lab["class"] == "silent" and g < params.silent_min_gap - CONTRADICTION_TOL)):
            unresolved.append({"lap": lab["lap"], "kerb": lab["kerb"], "class": lab["class"], "gBlockMin": round(g, 3),
                               "usedInFit": lab["used"], "anomaly": lab["anomaly"]})
    for n, lap in problem.laps.items():
        d = np.round(lap_offsets[n], 3)
        used = [l for l in problem.labels if l["lap"] == n and l["used"]]
        out_laps.append({
            "vehicleId": lap.record["vehicle_id"], "lap": n, "dataFile": lap.record["data_file"],
            "dataSha256": lap.sha, "offsetMeters": [float(d[0]), float(d[1])],
            "deviationFromSessionMeters": [round(float(d[0] - session[0]), 3), round(float(d[1] - session[1]), 3)],
            "labelsUsed": {"strong": sum(l["class"] == "strong" for l in used),
                           "silent": sum(l["class"] == "silent" for l in used)},
            "flags": flags_by_lap.get(n, []),
            "raw": beyond_limit_summary(lap, S["raw_limits"], (0.0, 0.0), S["curb_use"]),
            "registered": beyond_limit_summary(lap, S["raw_limits"], d, S["curb_use"]),
        })
        print(f'{args.race} lap {n}: d=({d[0]:+.3f},{d[1]:+.3f}) labels used={len(used)}')

    feasible = None
    if not args.no_feasible:
        feasible = feasible_regions(problem, session, lap_centres={n: lap_offsets[n] for n in problem.laps})
    track_dir, race_dir = S["track_dir"], S["race_dir"]
    geometry = S["geometry"]
    used = [l for l in problem.labels if l["used"]]
    result = {
        "version": 1,
        "kind": "lap-translation",
        "status": "candidate-kerb-contact-fit",
        "optIn": {"viewerQuery": "?gps=kerb", "replacesShipped": False,
                  "note": ("Opt-in candidate layer (review decision 2026-10-02). gps_registration.json stays the "
                           "default; this file is never loaded without ?gps=kerb.")},
        "raceId": args.race, "trackId": args.track,
        "frame": "viewer local XZ metres: +x east, +z south; added after latLngToLocal.",
        "definition": ("Display-only rigid translation per lap: a session translation fitted to the traced road limits "
                       "and the logger-native kerb-contact labels (robust hinge loss on the gap to the kerb block "
                       "start), plus a small lap deviation. Raw lat/lng unchanged. Not a survey; absolute clipping-point "
                       f"gaps carry at least +-{ABS_GAP_INTERVAL_MIN_M} m."),
        "method": {
            "tool": TOOL, "surface": args.surface, "curbUse": S["curb_use"],
            "vehicleWidthMeters": geometry["bodyWidthMeters"], "wheelbaseMeters": geometry["wheelbaseMeters"],
            "vehicleGeometry": geometry,
            "antennaOffsetMeters": {"right": 0.0, "forward": 0.0},
            "model": "session translation + lap deviation with a Gaussian prior (sigmaLapMeters)",
            "trackTerm": {"loss": "huber", "huberDeltaMeters": base.HUBER_DELTA_M,
                          "limits": "geometry.json road/dry edges; at kerbs the road edge is the kerb paint start "
                                    "(kerb_zones.json); dry edge extended to the outer end of kerbs missing in the CG",
                          "pitExcludeBeyondMeters": base.PIT_EXCLUDE_BEYOND_M, "samples": "10 Hz, 4 tyre contact points"},
            "labelTerm": {"loss": params.loss, "lossDeltaMeters": params.delta, "labelWeight": params.label_weight,
                          "strongMaxGapMeters": params.strong_max_gap, "silentMinGapMeters": params.silent_min_gap,
                          "gap": ("min over the inner front/rear tyre outer edge (50 Hz spline path) of the signed gap to "
                                  "the kerb block start of kerb_zones.json; negative = on the blocks; NaN beyond the line "
                                  "end vertices"),
                          "passWindow": f"kerb station range +-{PASS_MARGIN_M:g} m at the shipped registration offset "
                                        "(locates the pass only)",
                          "labelClasses": "strong and silent only; mild passes carry no hinge evidence; no left/right side"},
            "sigmaLapMeters": params.sigma_lap,
            "anomalyTreatment": params.anomaly_treatment,
            "anomalyWeight": params.anomaly_weight if params.anomaly_treatment == "downweight" else None,
            "solver": "Nelder-Mead from the best points of a 0.5 m grid (+-3 m) and the shipped session offset; exact "
                      f"cost pruning within {PRUNE_RADIUS_M:g} m of that centre (full evaluation beyond)",
            "inputs": {
                "kerbZones": {"source": rel(S["zones_path"]), "sha256": sha256(S["zones_path"])},
                "kerbContacts": {"source": rel(S["contacts_path"]), "sha256": sha256(S["contacts_path"])},
                "shippedRegistration": ({"source": rel(S["ref_registration"]), "sha256": sha256(S["ref_registration"]),
                                         "use": "pass windows and search centre only"}
                                        if S["ref_registration"].exists() else None),
                "vehicleProfile": {"source": rel(S["vehicle_profile"]), "sha256": sha256(S["vehicle_profile"])},
            },
        },
        "limits": {"source": S["limits"].source, "sha256": S["limits"].source_sha256},
        "referenceTrack": {"source": f"public/data/tracks/{args.track}/track.json", "sha256": sha256(track_dir / "track.json")},
        "uncertainty": {
            "absolutePositionAccuracyVerified": False, "antennaPositionMeasured": False,
            "absoluteGapIntervalMeters": ABS_GAP_INTERVAL_MIN_M,
            "absoluteGapIntervalBasis": ("lower bound: label-feasible widths of 0.4-0.7 m (feasibleRegion), held-out "
                                         "contradictions within 0.15 m tolerance, 2019 ortho kerb lines (~0.2-0.3 m "
                                         "georeferencing, not verified against 2020). Compare laps at the same corner first."),
            "validation": args.evaluation or "leave-one-kerb-out and leave-one-lap-out on the kerb-contact labels",
        },
        "labelSummary": {"strongUsed": sum(l["class"] == "strong" for l in used),
                         "silentUsed": sum(l["class"] == "silent" for l in used),
                         "strongKerbs": sorted({l["kerb"] for l in used if l["class"] == "strong"}),
                         "notUsed": sum(not l["used"] for l in problem.labels)},
        "labelsUsed": labels_out,
        "gpsAnomalies": [{**a, "treatment": params.anomaly_treatment,
                          "affectedLabels": [f'L{l["lap"]} {l["kerb"]} {l["class"]}' for l in problem.labels
                                             if l["anomaly"] and l["lap"] == a["lap"]],
                          "displayNote": "translation not adjusted to this segment; KPI there is flagged, not corrected"}
                         for a in S["anomalies"]],
        "unresolvedLabels": unresolved,
        "beyondLimitNote": ("laps[].raw/registered are measured like gps_registration.json (geometry.json limits, "
                            "tyre beyond the edge by > 0.05 m); the candidate trades some of them for label consistency"),
        "feasibleRegion": feasible,
        "sessionOffsetMeters": [round(float(session[0]), 3), round(float(session[1]), 3)],
        "laps": out_laps,
    }
    out = args.out or race_dir / OUT_NAME
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"session d=({session[0]:+.3f},{session[1]:+.3f}); unresolved labels {len(unresolved)} -> {rel(out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
