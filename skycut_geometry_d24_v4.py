#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""
skycut_geometry_d24_v4.py

Low-level 2D vector math and cubic-Bezier flattening helpers.

These are pure functions with no inkex dependency, which makes them
easy to unit test in isolation from Inkscape.
"""

import math


def unit(v):
    """Return the unit vector for 2D vector v, or (0, 0) if v is zero-length."""
    l = math.hypot(v[0], v[1])
    return (v[0] / l, v[1] / l) if l else (0, 0)


def dot(a, b):
    """2D dot product."""
    return a[0] * b[0] + a[1] * b[1]


def midpoint(a, b):
    """Midpoint between two 2D points."""
    return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)


def dist_point_line(p, a, b):
    """Perpendicular distance from point p to the infinite line through a-b."""
    if a == b:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    num = abs((b[0] - a[0]) * (a[1] - p[1]) - (a[0] - p[0]) * (b[1] - a[1]))
    den = math.hypot(b[0] - a[0], b[1] - a[1])
    return num / den


def cubic_flat_enough(p0, c1, c2, p1, tol):
    """True if both control points of a cubic Bezier lie within tol of the chord."""
    return (
        dist_point_line(c1, p0, p1) <= tol and
        dist_point_line(c2, p0, p1) <= tol
    )


def split_cubic(p0, c1, c2, p1):
    """De Casteljau split of a cubic Bezier into two half-length cubics."""
    p01 = midpoint(p0, c1)
    c12 = midpoint(c1, c2)
    c23 = midpoint(c2, p1)
    p012 = midpoint(p01, c12)
    c123 = midpoint(c12, c23)
    p0123 = midpoint(p012, c123)
    return ((p0, p01, p012, p0123),
            (p0123, c123, c23, p1))


def flatten_cubic(p0, c1, c2, p1, tol, out):
    """Recursively flatten a cubic Bezier into line segments, appending endpoints to out."""
    if cubic_flat_enough(p0, c1, c2, p1, tol):
        out.append(p1)
    else:
        l, r = split_cubic(p0, c1, c2, p1)
        flatten_cubic(*l, tol, out)
        flatten_cubic(*r, tol, out)


def flatten_superpath_to_subpaths(superpath, tol):
    """
    Flatten a CubicSuperPath-like structure (an iterable of subpaths,
    each an iterable of [prev_ctrl, point, next_ctrl] triples) into a
    list of point-lists - one per subpath, kept separate rather than
    merged together.

    A single SVG <path> element can contain multiple disconnected
    subpaths (a compound path - e.g. a ring shape's outer contour and
    inner hole, "M...Z M...Z"). Treating them as one continuous
    contour is wrong: it merges unrelated geometry into a single
    "closed ring", so the toolpath ends up cutting straight from one
    subpath into the next with no pen-lift in between. Keeping each
    subpath separate means each one gets its own independent start
    point, so the tool lifts and travels between them instead of
    cutting through the gap that was never really there.
    """
    subpath_points = []
    for sub in superpath:
        pts = []
        p0 = sub[0][1]
        pts.append(p0)
        for i in range(1, len(sub)):
            flatten_cubic(p0, sub[i - 1][2], sub[i][0], sub[i][1], tol, pts)
            p0 = sub[i][1]
        subpath_points.append(pts)
    return subpath_points


def dedupe(points, eps=1e-6):
    """Collapse consecutive near-duplicate points."""
    out = []
    for p in points:
        if not out or abs(p[0] - out[-1][0]) > eps or abs(p[1] - out[-1][1]) > eps:
            out.append(p)
    return out


def is_cw(pts):
    """True if the closed polygon pts is wound clockwise (SVG/screen Y-down convention)."""
    area = 0.0
    for (x1, y1), (x2, y2) in zip(pts, pts[1:] + [pts[0]]):
        area += (x2 - x1) * (-(y2 + y1))
    return area > 0
