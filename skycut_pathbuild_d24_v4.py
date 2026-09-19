#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""
skycut_pathbuild_d24_v4.py

Knife-offset corner overshoot/return generation and the final overcut,
used to compensate for a rotating drag-knife's offset from its pivot
point when cutting corners.
"""

import math

from skycut_geometry_d24_v4 import unit, dot, midpoint, flatten_cubic
from skycut_constants_d24_v4 import MIN_CORNER_ANGLE, RETURN_TRIM, FLATTEN_TOLERANCE_MM


def _is_closed(points, eps=1e-6):
    """True if the first and last points coincide (a closed ring)."""
    if len(points) < 3:
        return False
    return math.hypot(points[0][0] - points[-1][0], points[0][1] - points[-1][1]) < eps


def rotate_closed_path_start(points, eps=1e-6):
    """
    For a closed ring (points[0] == points[-1]), shift the start/end
    seam off the vertex it currently sits on and onto the midpoint of
    the first edge instead.

    Without this, corner_overshoots_with_curve() never evaluates the
    corner at the seam - it only looks at interior indices 1..len-2,
    so every real corner gets its knife-offset overshoot/return loop
    *except* the one where the path closes back on itself. That
    corner is where the blade re-crosses its own start, since it was
    never given the same compensation as every other corner.

    Moving the seam to an edge midpoint means it no longer sits on a
    corner at all, so every actual corner - including the one that
    used to be the untreated seam - goes through the same overshoot
    handling. Non-closed (open) paths are returned unchanged.
    """
    if not _is_closed(points, eps):
        return points
    ring = points[:-1]
    if len(ring) < 3:
        return points
    mid = midpoint(ring[0], ring[1])
    return [mid] + ring[1:] + [ring[0], mid]


def merge_close_corners(points, min_spacing):
    """
    Collapse runs of consecutive *sharp-corner* points spaced closer
    together than `min_spacing` into a single representative point
    (their centroid). Points that aren't themselves sharp corners -
    including the closely-spaced points that make up a tightly
    curved, smoothly flattened arc - are left completely untouched,
    no matter how close together they are.

    Font outlines - especially script/calligraphic ones - sometimes
    digitize a smooth curve-to-straight transition (e.g. a letter's
    round bowl rolling into its vertical stem) as two anchor points
    only a fraction of a mm apart instead of one clean vertex, each
    with a real, sharp turn angle. Both independently read as a
    genuine corner to corner_overshoots_with_curve(), which then
    builds a full overshoot/return loop for each - back to back, a
    fraction of a mm apart. Since each loop reaches out `offset` from
    its corner, two such loops closer together than `offset`
    inevitably overlap: the blade backtracks into a cut it just made
    instead of tracing one clean corner, which looks like a doubled,
    tangled loop right at that junction.

    This scans for the same sharp-turn condition
    corner_overshoots_with_curve() itself checks (angle >=
    MIN_CORNER_ANGLE), and only merges *adjacent* points that both
    qualify and sit within min_spacing of each other - so the
    junction is evaluated as the single corner it actually
    represents, using the direction into the run and the direction
    back out of it. A tight arc's flattened points normally have
    near-zero turn angle between them even when closely spaced, so
    they never qualify and are never merged; a genuine isolated sharp
    corner has no close sharp-corner neighbor to merge with either.

    The first and last points are kept fixed rather than merged into
    a run - corner_overshoots_with_curve() never evaluates them as
    corners anyway (only interior indices 1..len-2 are), so for a
    closed ring this also preserves the seam placement
    rotate_closed_path_start() deliberately chose.
    """
    n = len(points)
    if n < 4 or min_spacing <= 0:
        return points

    last_idx = n - 1  # kept fixed, mirrors points[0] on a closed ring
    is_sharp = [False] * n
    for i in range(1, last_idx):
        A, B, C = points[i - 1], points[i], points[i + 1]
        v1 = unit((B[0] - A[0], B[1] - A[1]))
        v2 = unit((C[0] - B[0], C[1] - B[1]))
        ang = math.acos(max(-1, min(1, dot(v1, v2))))
        is_sharp[i] = ang >= MIN_CORNER_ANGLE

    merged = [points[0]]
    i = 1
    while i < last_idx:
        if not is_sharp[i]:
            merged.append(points[i])
            i += 1
            continue
        cluster = [points[i]]
        j = i + 1
        while (
            j < last_idx
            and is_sharp[j]
            and math.hypot(points[j][0] - cluster[-1][0], points[j][1] - cluster[-1][1]) < min_spacing
        ):
            cluster.append(points[j])
            j += 1
        if len(cluster) == 1:
            merged.append(cluster[0])
        else:
            cx = sum(p[0] for p in cluster) / len(cluster)
            cy = sum(p[1] for p in cluster) / len(cluster)
            merged.append((cx, cy))
        i = j
    merged.append(points[last_idx])
    return merged


def corner_overshoots_with_curve(points, offset, trim=RETURN_TRIM):
    """
    For each interior corner in points sharper than MIN_CORNER_ANGLE,
    compute an overshoot point (O), a return point (R), and a cubic
    Bezier control tuple bowing the return path slightly outward so
    the knife blade can re-align before continuing.

    Returns a list of (index, corner_point, O, R, bezier_tuple).
    """
    out = []
    for i in range(1, len(points) - 1):
        A, B, C = points[i - 1], points[i], points[i + 1]
        v1 = unit((B[0] - A[0], B[1] - A[1]))
        v2 = unit((C[0] - B[0], C[1] - B[1]))
        ang = math.acos(max(-1, min(1, dot(v1, v2))))
        if ang < MIN_CORNER_ANGLE:
            continue

        cross = v1[0] * v2[1] - v1[1] * v2[0]
        O = (B[0] + v1[0] * offset, B[1] + v1[1] * offset)
        R = (B[0] + v2[0] * offset * trim, B[1] + v2[1] * offset * trim)

        dx = R[0] - O[0]
        dy = R[1] - O[1]
        length = math.hypot(dx, dy)

        if length:
            perp = (dy / length * 0.3, -dx / length * 0.3) if cross > 0 else (-dy / length * 0.3, dx / length * 0.3)
        else:
            perp = (0, 0)

        c1 = (O[0] + dx * 0.33 + perp[0], O[1] + dy * 0.33 + perp[1])
        c2 = (O[0] + dx * 0.66 + perp[0], O[1] + dy * 0.66 + perp[1])

        out.append((i, B, O, R, (O, c1, c2, R)))
    return out


def final_overcut(points, dist):
    """Extend the path dist past its final point, along the direction of the last segment."""
    if len(points) < 2:
        return None
    A, B = points[-2], points[-1]
    v = unit((B[0] - A[0], B[1] - A[1]))
    return [(B[0] + v[0] * dist, B[1] + v[1] * dist)]


def build_cut_path(points, knife_offset, overcut):
    """
    Build the final toolpath for a closed contour: insert overshoot/return
    loops at sharp corners to compensate for knife offset, then append a
    final overcut past the path's closing point.

    Points closer together than knife_offset are merged into a single
    corner first (see merge_close_corners()) - otherwise two corners
    sitting a fraction of a mm apart (a common font-digitization
    artifact) each get their own overshoot/return loop, and since
    each loop reaches out knife_offset from its corner, the two loops
    overlap into a doubled, tangled mess right at that junction.
    """
    points = rotate_closed_path_start(points)
    points = merge_close_corners(points, knife_offset)

    path = []
    corners = corner_overshoots_with_curve(points, knife_offset)
    last = 0

    for idx, B, O, R, bez in corners:
        path.extend(points[last:idx])
        path.append(B)
        path.append(O)
        curve_pts = []
        flatten_cubic(*bez, FLATTEN_TOLERANCE_MM, curve_pts)
        path.extend(curve_pts)
        last = idx + 1

    path.extend(points[last:])
    oc = final_overcut(path, overcut)
    if oc:
        path.extend(oc)
    return path