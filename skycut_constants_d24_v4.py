#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""
skycut_constants_d24_v4.py

Master switches and shared numeric constants for the Skycut Command
D24-v4 extension (CUT + SCORE + CONTOUR).

Kept in one place so every module (and the main effect script) agrees
on the same values instead of redefining them.
"""

import math

# Debug / capture only - never exposed in the INX UI.
CAPTURE_ENABLE_MASTER = False

# HPGL units-per-mm scale factor.
SCALE = 40

# Bezier flattening tolerance, in mm.
FLATTEN_TOLERANCE_MM = 0.005

# Stroke width used for debug/preview capture layers, in mm.
CAPTURE_STROKE_WIDTH = 0.05

# Corners sharper... err, wider than this angle are ignored for
# knife-offset overshoot/return generation.
MIN_CORNER_ANGLE = math.radians(15)

# Fraction of the knife offset used for the "return" leg of a corner.
RETURN_TRIM = 1.0

# ----------------------------------------------------------------
# Preview colors - fixed (not user-configurable) per-kind colors for
# the Developer Use preview layer and the Preview Path viewer only.
# No effect on the physical cutter. Edit the hex values here to
# change what shows up in both places.
#
# Kind names map to real-world vinyl/sign terms: "cut" = Full Cut,
# "score" = Kiss Cut.
PREVIEW_COLORS = {
    "cut":    "#FF00FF",  # Full Cut
    "score":  "#FF0000",  # Kiss Cut
    "draw":   "#00B050",  # Draw
    "crease": "#00B7EB",  # Crease
    "marks":  "#FFFF00",  # Contour Cut registration marks
    "print":  "#0038A8",  # Print layer (Layer Setup only)
}

# Display names matching the Layer Selection option labels in the
# INX GUI - used for preview-layer/legend text so wording stays
# consistent with what's shown in Left/Right Tool.
LAYER_DISPLAY_NAMES = {
    "cut": "Full Cut",
    "score": "Kiss Cut",
    "cut_score": "Full Cut & Kiss Cut",
    "crease": "Crease Tool",
    "draw": "Draw",
}

# For a given Left/Right Task Selection value, the ordered list of
# real source-layer kinds it actually pulls geometry from and emits
# HPGL for. Order matters for "cut_score": Kiss Cut is emitted first
# and Full Cut data is appended after it, on the same physical tool
# head (see skycut_hpgl_d24_v4.py's _emit_side_block).
REAL_KINDS_FOR_TASK = {
    "cut": ["cut"],
    "score": ["score"],
    "cut_score": ["score", "cut"],
    "crease": ["crease"],
    "draw": ["draw"],
    "none": [],
}

# Actual SVG layer names collect_layer() searches the document for.
# These match what skycut_layer_setup.py creates - NOT always the
# same text as LAYER_DISPLAY_NAMES above (the Crease *tool* is
# described as "Crease Tool" in the dropdown, but the layer itself is
# just named "Crease").
SOURCE_LAYER_NAMES = {
    "cut": "Full Cut",
    "score": "Kiss Cut",
    "crease": "Crease",
    "draw": "Draw",
}
