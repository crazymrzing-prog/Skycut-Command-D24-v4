#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""
skycut_reference_d24_v4.py

Computes the reference bounding box used to translate Inkscape's
top-left/Y-down document coordinates into the cutter's
bottom-left/Y-up machine coordinates.

Inkscape coordinate system:
    (0,0) = top-left, Y increases downward
Machine coordinate system:
    (0,0) = bottom-left, Y increases upward

Each cut_mode picks a different source for that reference box:
  - wysiwyg:    the SVG page itself
  - origin:     the bounding box of the artwork geometry
  - contourcut: four registration marks in a "Marks" layer
"""

import inkex
from inkex import PathElement
from inkex.paths import CubicSuperPath

from skycut_geometry_d24_v4 import flatten_superpath_to_subpaths
from skycut_constants_d24_v4 import FLATTEN_TOLERANCE_MM


class ReferenceError(Exception):
    """Raised when the reference box can't be determined; message is user-facing."""
    pass


def compute_reference_box(svg, all_paths, cut_mode):
    """
    Return (min_x, min_y, max_x, max_y) for the given cut_mode.
    Raises ReferenceError with a user-facing message on failure.

    `all_paths` is a single combined list of (pts, kind) tuples -
    cut, score, and draw geometry together - since "origin" mode's
    bounding box should reflect everything actually being sent to
    the machine, regardless of which layer it came from.
    """
    if cut_mode == "wysiwyg":
        return _reference_wysiwyg(svg)
    elif cut_mode == "origin":
        return _reference_origin(all_paths)
    elif cut_mode == "contourcut":
        return _reference_contourcut(svg)
    else:
        raise ReferenceError(f"Unknown cut_mode: {cut_mode}")


def _reference_wysiwyg(svg):
    # WYSIWYG: use the PAGE as reference. Artwork position on the page
    # is preserved; bottom-right of the PAGE maps to machine (0,0)
    # once the SkyCut axis-swap is applied to the real output.
    #
    # svg.viewport_width/viewport_height reportedly returned values on
    # a different scale than the path coordinate space (~10x off) on
    # at least one real document, so page dimensions are read via
    # svg.unittouu() on the raw width/height attribute strings
    # instead - the standard, explicit way inkex converts a physical
    # unit (mm, in, etc.) into the same "user units" the path data
    # itself is already in. Falls back to viewport_width/height only
    # if the width/height attributes aren't present at all.
    try:
        width_attr = svg.get("width")
        height_attr = svg.get("height")
        if width_attr and height_attr:
            page_width = float(svg.unittouu(width_attr))
            page_height = float(svg.unittouu(height_attr))
        else:
            page_width = float(svg.viewport_width)
            page_height = float(svg.viewport_height)
    except Exception:
        raise ReferenceError(
            "Could not determine page size (no width/height or viewport information found)"
        )
    return 0.0, 0.0, page_width, page_height


def _reference_origin(all_paths):
    # ORIGIN: ignore the page completely, use only the artwork geometry.
    all_pts = [p for pts, _ in all_paths for p in pts]
    if not all_pts:
        raise ReferenceError("No geometry found for origin reference")

    min_x = min(p[0] for p in all_pts)
    max_x = max(p[0] for p in all_pts)
    min_y = min(p[1] for p in all_pts)
    max_y = max(p[1] for p in all_pts)
    return min_x, min_y, max_x, max_y


def get_contour_marks(svg):
    """
    Locate the 4 required registration marks ("bottomleft",
    "bottomright", "topleft", "topright", labeled via
    inkscape:label) inside a "Marks" layer.

    Returns a dict {label: (x, y)}. Raises ReferenceError if the
    layer or any required mark is missing.
    """
    marks_layer = None
    for layer in svg.xpath("//svg:g[@inkscape:groupmode='layer']"):
        if layer.get("inkscape:label", "") == "Marks":
            marks_layer = layer
            break

    if marks_layer is None:
        raise ReferenceError("ContourCut mode requires 'Marks' layer")

    required = {
        "bottomleft": None,
        "bottomright": None,
        "topleft": None,
        "topright": None
    }

    for elem in marks_layer.iterdescendants():
        if not isinstance(elem, PathElement):
            continue

        label = (elem.get("inkscape:label") or "").strip().lower()
        if label not in required:
            continue

        for seg in elem.path.to_absolute():
            if hasattr(seg, "x"):
                required[label] = (seg.x, seg.y)
                break

    missing = [k for k, v in required.items() if v is None]
    if missing:
        raise ReferenceError("Missing required mark(s):\n" + "\n".join(missing))

    return required


def _reference_contourcut(svg):
    # CONTOURCUT: use registration marks to define the reference area.
    marks = get_contour_marks(svg)
    xs = [p[0] for p in marks.values()]
    ys = [p[1] for p in marks.values()]
    return min(xs), min(ys), max(xs), max(ys)


def get_contour_mark_paths(svg):
    """
    Like get_contour_marks(), but returns each mark's full flattened
    path geometry instead of a single point - so the viewer can draw
    the actual mark shape (e.g. a crosshair or arrow indicating
    orientation, the "mark vector") rather than reducing it to a dot.

    Returns a dict {label: [(x, y), ...]}. Raises ReferenceError under
    the same conditions as get_contour_marks().
    """
    marks_layer = None
    for layer in svg.xpath("//svg:g[@inkscape:groupmode='layer']"):
        if layer.get("inkscape:label", "") == "Marks":
            marks_layer = layer
            break

    if marks_layer is None:
        raise ReferenceError("ContourCut mode requires 'Marks' layer")

    required = {
        "bottomleft": None,
        "bottomright": None,
        "topleft": None,
        "topright": None
    }

    for elem in marks_layer.iterdescendants():
        if not isinstance(elem, PathElement):
            continue

        label = (elem.get("inkscape:label") or "").strip().lower()
        if label not in required:
            continue

        superpath = CubicSuperPath(elem.path.to_absolute())
        subpaths = flatten_superpath_to_subpaths(superpath, FLATTEN_TOLERANCE_MM)
        if subpaths:
            # A mark is expected to be a single simple subpath; if it
            # has more than one, just use the first.
            required[label] = subpaths[0]

    missing = [k for k, v in required.items() if v is None]
    if missing:
        raise ReferenceError("Missing required mark(s):\n" + "\n".join(missing))

    return required
