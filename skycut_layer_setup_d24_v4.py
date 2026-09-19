#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""
skycut_layer_setup_d24_v4.py

Creates and updates the standard SkyCut cutter layers so a new
document starts with the right layer names/colors already set up -
the exact names skycut_command_d24_v4.py's collect_layer() searches
for (see SOURCE_LAYER_NAMES in skycut_constants.py), plus Print and
Marks which aren't cut/score/crease/draw assignments but are still
part of the standard layer set.

Final layer order (top to bottom):
    Marks
    Print
    Full Cut
    Kiss Cut
    Crease
    Draw

Callable directly as run_layer_setup(svg) - e.g. from
skycut_command_d24_v4.py's effect() when Output is set to "Layer
Setup" - or run standalone as its own Inkscape effect extension.
"""

import inkex
from inkex.styles import Style

from skycut_constants_d24_v4 import PREVIEW_COLORS, SOURCE_LAYER_NAMES

# ==========================================================
# SETTINGS
# ==========================================================
STROKE_WIDTH = "0.265mm"

# ==========================================================
# LAYER DEFINITIONS
# ==========================================================
LAYERS = [
    (SOURCE_LAYER_NAMES["draw"], PREVIEW_COLORS["draw"]),
    (SOURCE_LAYER_NAMES["crease"], PREVIEW_COLORS["crease"]),
    (SOURCE_LAYER_NAMES["score"], PREVIEW_COLORS["score"]),
    (SOURCE_LAYER_NAMES["cut"], PREVIEW_COLORS["cut"]),
    ("Print", PREVIEW_COLORS["print"]),
    ("Marks", PREVIEW_COLORS["marks"]),
]


# ------------------------------------------------------
# Create new layer
# ------------------------------------------------------
def _create_layer(svg, name, colour):
    layer = inkex.Layer.new(name)
    # Layer name
    layer.set("inkscape:label", name)
    # Inkscape layer type
    layer.set("inkscape:groupmode", "layer")
    # Layer marker colour
    layer.set("inkscape:highlight-color", colour)
    # Default style for objects
    layer.style = Style({
        "stroke": colour,
        "stroke-width": STROKE_WIDTH,
        "fill": "none",
    })
    svg.append(layer)


# ------------------------------------------------------
# Update existing layer
# ------------------------------------------------------
def _update_layer(layer, colour):
    # Update layer marker colour
    layer.set("inkscape:highlight-color", colour)
    # Update default layer style
    layer.style = Style({
        "stroke": colour,
        "stroke-width": STROKE_WIDTH,
        "fill": "none",
    })


# ------------------------------------------------------
# Main
# ------------------------------------------------------
def run_layer_setup(svg):
    """Create any missing standard layers; refresh color/style on any that already exist."""
    existing = {}
    # Find existing Inkscape layers
    for layer in svg.xpath('//svg:g[@inkscape:groupmode="layer"]'):
        label = layer.get("inkscape:label")
        if label:
            existing[label] = layer

    # Update existing layers or create missing ones
    for name, colour in LAYERS:
        if name in existing:
            _update_layer(existing[name], colour)
        else:
            _create_layer(svg, name, colour)


# ==========================================================
# STANDALONE EXTENSION (optional - Output = "Layer Setup" in
# skycut_command_d24_v4.py calls run_layer_setup() directly instead)
# ==========================================================
class LayerSetup(inkex.EffectExtension):
    def add_arguments(self, pars):
        pass

    def effect(self):
        run_layer_setup(self.svg)


if __name__ == "__main__":
    LayerSetup().run()
