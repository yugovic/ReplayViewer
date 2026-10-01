"""Source-reviewed white edge selection; no GPS or vehicle geometry inputs."""
from __future__ import annotations


def select_white_road_run(runs, offsets, allowed_inner_offsets=None):
    """Retain only observed paint, optionally constrained by an image review.

    A reviewed band disambiguates simultaneous white markings (e.g. grid boxes
    beside a continuous road edge). An absent matching run stays unobserved;
    the caller can interpolate between actual neighbouring observations.
    """
    if allowed_inner_offsets is not None:
        low, high = allowed_inner_offsets
        runs = [run for run in runs if low <= offsets[run[0]] <= high]
    if not runs:
        return None
    return min(runs, key=lambda run: abs(offsets[run[0]]) + .15 * max(0, len(run) - 5))
