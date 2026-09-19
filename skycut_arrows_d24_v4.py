#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""
skycut_arrows_d24_v4.py

Debug-only helpers that build little arrowhead polylines along a path,
used by the capture/preview layers to show cut direction.
"""

from skycut_geometry_d24_v4 import unit


def make_arrow(p, v, size=1.2):
    """Build a 3-point (left, tip, right) arrowhead at point p pointing along unit vector v."""
    perp = (-v[1], v[0])
    tip = (p[0] + v[0] * size, p[1] + v[1] * size)
    left = (p[0] - v[0] * size * 0.4 + perp[0] * size * 0.4,
            p[1] - v[1] * size * 0.4 + perp[1] * size * 0.4)
    right = (p[0] - v[0] * size * 0.4 - perp[0] * size * 0.4,
             p[1] - v[1] * size * 0.4 - perp[1] * size * 0.4)
    return [left, tip, right]


def direction_arrows(pts, step=12):
    """Return a list of arrowhead polylines sampled every `step` points along pts."""
    arrows = []
    for i in range(0, len(pts) - 1, step):
        v = unit((pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]))
        arrows.append(make_arrow(pts[i], v))
    return arrows
