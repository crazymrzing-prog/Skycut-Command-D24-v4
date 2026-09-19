#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""
skycut_output_d24_v4.py

Delivers an assembled HPGL command stream either to disk (.plt file
under ~/Documents/Skycut Data), over a raw TCP socket to the cutter
(WiFi), or directly to the cutter's USB-Printer-class device (USB).
"""

import os
import socket
import sys
from datetime import datetime

from skycut_usb_d24_v4 import find_usbprint_paths, send_job, friendly_label, pick_and_send


def save_hpgl_file(data, cut_mode):
    """Write `data` to a timestamped .plt file. Returns the full path written."""
    outdir = os.path.join(os.path.expanduser("~"), "Documents", "Skycut Data")
    os.makedirs(outdir, exist_ok=True)
    filename = f"SkyCut_{cut_mode}_{datetime.now():%Y%m%d%H%M%S}.plt"
    path = os.path.join(outdir, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(data)
    return path


def send_hpgl(data, ip, port, connect_timeout=5, send_timeout=90):
    """
    Send `data` to the cutter over TCP. Returns (success, message) - message
    is a short, user-facing status/error string.

    connect_timeout is intentionally short: if the cutter is powered
    off or unreachable, this is where it fails, and a person watching
    the UI shouldn't have to wait ~90s just to find that out. Once
    connected, the socket is switched to send_timeout for the actual
    data transfer, since a large job can legitimately take longer to
    send than a reasonable connect timeout would allow.
    """
    try:
        with socket.create_connection((ip, port), timeout=connect_timeout) as s:
            s.settimeout(send_timeout)
            s.sendall((data + "\n").encode("utf-8"))
        return True, "Sent to SkyCut successfully"
    except Exception as e:
        return False, str(e)


def send_hpgl_usb(data, device_path=""):
    """
    Send `data` to the cutter over USB (USB-Printer class, VID
    0483:PID 5750).

    device_path: either the short label shown in the picker window
    (e.g. "skycut_5503") or the full raw device interface path - if
    given, this is resolved against whatever's currently plugged in
    and sent to directly, skipping the picker. Leave blank to
    auto-detect: every matching device found is shown in a small
    confirmation window (pick_and_send(), in skycut_usb.py) so
    nothing is written to a physical device without a visible
    confirm step, even when only one is found.

    Returns (success, message) - message is a short, user-facing
    status/error string, matching send_hpgl()'s (TCP) return shape.
    """
    if sys.platform != "win32":
        return False, "Send to Machine USB only works on Windows (uses SetupAPI/kernel32 via ctypes)."

    entered = (device_path or "").strip()
    payload = (data + "\n").encode("utf-8")

    if entered:
        # entered may be the short label from the picker window
        # (e.g. "skycut_5503") rather than the full raw device
        # path - resolve it against what's currently plugged in; if
        # nothing matches, fall back to treating it as a raw path
        # as-is (e.g. one saved from a previous run).
        try:
            candidates = find_usbprint_paths()
        except Exception:
            candidates = []
        match = next((p for p in candidates if friendly_label(p) == entered), None)
        path = match or entered
        try:
            written = send_job(path, payload)
        except OSError as e:
            return False, str(e)
        return True, f"Sent {written} bytes to {friendly_label(path)} via USB"

    try:
        paths = find_usbprint_paths()
    except Exception as e:
        return False, f"USB device search failed: {e}"

    if not paths:
        return False, (
            "No SkyCut device found (USB ID 0483:5750, usbprint.sys). "
            "Make sure it's plugged in and powered on, then try again."
        )

    try:
        return pick_and_send(paths, payload)
    except ImportError:
        if len(paths) == 1:
            try:
                written = send_job(paths[0], payload)
            except OSError as e:
                return False, str(e)
            return True, f"Sent {written} bytes to {friendly_label(paths[0])} via USB (tkinter unavailable, sent without confirmation)"
        listed = "\n".join(f"  {friendly_label(p)}" for p in paths)
        return False, (
            "tkinter isn't available, so the device picker can't be shown, and more "
            "than one SkyCut device was found - enter one of these labels into the "
            "USB Device Path field:\n" + listed
        )
