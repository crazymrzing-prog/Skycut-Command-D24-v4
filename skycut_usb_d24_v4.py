#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""
skycut_usb_d24_v4.py

Windows USB-Printer-class (usbprint.sys) device discovery and
chunked, paced CreateFile/WriteFile delivery, plus a small Tk picker
window - for sending a job to the SkyCut cutter (USB ID 0483:5750)
over USB.

Used by skycut_output.py's send_hpgl_usb(), called from
skycut_command_d24_v4.py's "Send to Machine USB" action.

Windows only. Uses ctypes (stdlib) - no third-party dependencies.
The picker window uses tkinter, which ships with Inkscape's bundled
Python on Windows.

Note: this device is USB-Printer class, not a virtual COM port -
there is no baud rate to set (that's a serial concept and doesn't
apply here).

--- Chunking / pacing (this version) ---
Confirmed via three separate USBPcap captures (two different sending
programs, job sizes from ~1.2KB to ~13.5KB) that this device NEVER
sends anything back over USB - no ACK, no status, no busy/OK, on any
endpoint, regardless of job size. So there is no real flow control
available to us; a "write, then wait for a response" design (an
earlier version of this module did that) just times out every time
and adds nothing but latency.

What a known-working sender does instead: it writes the job in fixed
1024-byte chunks, with a ~452ms pause between each chunk (measured
consistently across 13 chunks in a real capture, +/- ~3ms). That's a
blind, timing-based throttle rather than real acknowledgement - it
exists to avoid overrunning the device's onboard job buffer on
larger jobs, since the device can't tell us if it's full. Small jobs
(under ~1KB) can apparently go out as a single write with no pacing
and work fine, but chunking+pacing is the safer default for jobs of
any size, so that's what send_job() does unconditionally below.

If you have a job that reliably fails or cuts incorrectly even with
this pacing, the next things worth checking (in order): (1) whether
CHUNK_DELAY_S needs to be larger for your particular device/firmware
revision, (2) a USBPcap capture of that specific failing job to see
whether it truly still has zero read-back for your firmware, since
this was only confirmed on one physical unit.
"""

import ctypes
import time
from ctypes import wintypes

VENDOR_ID = 0x0483
PRODUCT_ID = 0x5750

DIGCF_PRESENT = 0x00000002
DIGCF_DEVICEINTERFACE = 0x00000010
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
FILE_FLAG_OVERLAPPED = 0x40000000
WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102
ERROR_IO_PENDING = 997

# Matches the chunk size and pacing measured from a real, working
# capture (see module docstring). Change CHUNK_DELAY_S up if you see
# jobs stall/misbehave on your specific machine - down only after
# you've confirmed on your own hardware it's safe to.
CHUNK_SIZE = 1024
CHUNK_DELAY_S = 0.452

# How long a single chunk write is allowed to hang before we give up
# and report a real I/O failure, rather than blocking forever.
WRITE_TIMEOUT_MS = 5000


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


GUID_DEVINTERFACE_USBPRINT = GUID(
    0x28D78FAD, 0x5A12, 0x11D1,
    (ctypes.c_ubyte * 8)(0xAE, 0x5B, 0x00, 0x00, 0xF8, 0x03, 0xA8, 0xC2),
)


class SP_DEVINFO_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("ClassGuid", GUID),
        ("DevInst", wintypes.DWORD),
        ("Reserved", ctypes.c_void_p),
    ]


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("InterfaceClassGuid", GUID),
        ("Flags", wintypes.DWORD),
        ("Reserved", ctypes.c_void_p),
    ]


class OVERLAPPED(ctypes.Structure):
    _fields_ = [
        ("Internal", ctypes.c_void_p),
        ("InternalHigh", ctypes.c_void_p),
        ("Offset", wintypes.DWORD),
        ("OffsetHigh", wintypes.DWORD),
        ("hEvent", wintypes.HANDLE),
    ]


def _get_win32():
    # use_last_error=True is required for ctypes.get_last_error() to
    # return the real Win32 error code. Without it (e.g. the plain
    # ctypes.windll.kernel32 shortcut), get_last_error() always reads
    # back stale/zero data, which is why failures were showing up as
    # "WinError 0" regardless of what actually went wrong.
    setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    setupapi.SetupDiGetClassDevsW.restype = wintypes.HANDLE
    setupapi.SetupDiGetClassDevsW.argtypes = [
        ctypes.POINTER(GUID), wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD
    ]
    setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(SP_DEVINFO_DATA), ctypes.POINTER(GUID),
        wintypes.DWORD, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA)
    ]
    setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(SP_DEVINFO_DATA)
    ]
    setupapi.SetupDiDestroyDeviceInfoList.argtypes = [wintypes.HANDLE]

    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE
    ]

    kernel32.CreateEventW.restype = wintypes.HANDLE
    kernel32.CreateEventW.argtypes = [
        ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR
    ]

    kernel32.WriteFile.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(OVERLAPPED)
    ]
    kernel32.GetOverlappedResult.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(OVERLAPPED),
        ctypes.POINTER(wintypes.DWORD), wintypes.BOOL
    ]
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(OVERLAPPED)]

    return setupapi, kernel32


def _detail_data_cbsize():
    return 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6


def find_usbprint_paths(vid=VENDOR_ID, pid=PRODUCT_ID):
    """Return device interface paths for every USB-Printer-class device matching vid:pid."""
    setupapi, kernel32 = _get_win32()
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    h_devinfo = setupapi.SetupDiGetClassDevsW(
        ctypes.byref(GUID_DEVINTERFACE_USBPRINT), None, None,
        DIGCF_PRESENT | DIGCF_DEVICEINTERFACE
    )
    if h_devinfo == INVALID_HANDLE_VALUE or not h_devinfo:
        return []

    results = []
    index = 0
    try:
        while True:
            ifdata = SP_DEVICE_INTERFACE_DATA()
            ifdata.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DATA)
            ok = setupapi.SetupDiEnumDeviceInterfaces(
                h_devinfo, None, ctypes.byref(GUID_DEVINTERFACE_USBPRINT),
                index, ctypes.byref(ifdata)
            )
            if not ok:
                break

            required = wintypes.DWORD(0)
            setupapi.SetupDiGetDeviceInterfaceDetailW(
                h_devinfo, ctypes.byref(ifdata), None, 0,
                ctypes.byref(required), None
            )
            buf = ctypes.create_string_buffer(required.value)
            ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD))[0] = _detail_data_cbsize()

            devinfo_data = SP_DEVINFO_DATA()
            devinfo_data.cbSize = ctypes.sizeof(SP_DEVINFO_DATA)

            ok2 = setupapi.SetupDiGetDeviceInterfaceDetailW(
                h_devinfo, ctypes.byref(ifdata), buf, required.value,
                None, ctypes.byref(devinfo_data)
            )
            if ok2:
                path = ctypes.wstring_at(ctypes.addressof(buf) + ctypes.sizeof(wintypes.DWORD))
                vid_str = f"vid_{vid:04x}"
                pid_str = f"pid_{pid:04x}"
                if vid_str in path.lower() and pid_str in path.lower():
                    results.append(path)

            index += 1
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(h_devinfo)

    return results


def _overlapped_write(kernel32, handle, chunk, timeout_ms=WRITE_TIMEOUT_MS):
    ov = OVERLAPPED()
    ov.hEvent = kernel32.CreateEventW(None, True, False, None)
    if not ov.hEvent:
        raise OSError(f"CreateEvent failed (WinError {ctypes.get_last_error()})")

    try:
        written = wintypes.DWORD(0)
        ok = kernel32.WriteFile(handle, chunk, len(chunk), ctypes.byref(written), ctypes.byref(ov))
        if not ok:
            err = ctypes.get_last_error()
            if err != ERROR_IO_PENDING:
                raise OSError(f"WriteFile failed (WinError {err})")
            wait = kernel32.WaitForSingleObject(ov.hEvent, timeout_ms)
            if wait == WAIT_TIMEOUT:
                kernel32.CancelIoEx(handle, ctypes.byref(ov))
                raise OSError("WriteFile timed out - device not responding")
            if wait != WAIT_OBJECT_0:
                kernel32.CancelIoEx(handle, ctypes.byref(ov))
                raise OSError(f"WaitForSingleObject (write) failed (WinError {ctypes.get_last_error()})")
            if not kernel32.GetOverlappedResult(handle, ctypes.byref(ov), ctypes.byref(written), False):
                raise OSError(f"GetOverlappedResult (write) failed (WinError {ctypes.get_last_error()})")
        return written.value
    finally:
        kernel32.CloseHandle(ov.hEvent)


def send_job(device_path, payload, on_progress=None,
             chunk_size=CHUNK_SIZE, chunk_delay_s=CHUNK_DELAY_S):
    """
    Open device_path and write payload (bytes) in chunk_size chunks,
    pausing chunk_delay_s between each write. No read-back is
    attempted - confirmed via USBPcap that this device never sends
    anything over USB regardless of job size (see module docstring).

    on_progress, if given, is called after each chunk as:
        on_progress(bytes_sent: int, total_bytes: int)
    so a caller (e.g. the Tk picker below) can show live progress
    even though the device itself gives no feedback.

    Returns the total number of bytes written (== len(payload) on
    success - anything less would only happen if a partial write
    occurred without WriteFile itself reporting failure, which is
    checked for here and raises OSError instead of returning short).

    Raises OSError on any real I/O failure (device unplugged mid-job,
    open failure, a chunk write timing out, etc). Chunking makes a
    mid-job unplug much easier to detect and report accurately than
    the previous single-giant-write approach did.
    """
    setupapi, kernel32 = _get_win32()
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    handle = kernel32.CreateFileW(
        device_path, GENERIC_READ | GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING,
        FILE_FLAG_OVERLAPPED, None
    )
    if handle == INVALID_HANDLE_VALUE or not handle:
        err = ctypes.get_last_error()
        raise OSError(f"CreateFile failed (WinError {err})")

    try:
        total = len(payload)
        sent = 0
        for offset in range(0, total, chunk_size):
            chunk = payload[offset:offset + chunk_size]
            written = _overlapped_write(kernel32, handle, chunk)
            if written != len(chunk):
                raise OSError(
                    f"Partial chunk write at offset {offset}: "
                    f"wrote {written} of {len(chunk)} bytes"
                )
            sent += written

            if on_progress:
                on_progress(sent, total)

            # Pace the sends - no ACK to wait for, so we throttle on
            # a fixed delay instead (see module docstring). Skip the
            # trailing delay after the very last chunk.
            if offset + chunk_size < total:
                time.sleep(chunk_delay_s)

        return sent
    finally:
        kernel32.CloseHandle(handle)


def friendly_label(path):
    # Device path looks like:
    #   \\?\usb#vid_0483&pid_5750#skycut_5503#{28d78fad-5a12-11d1-ae5b-0000f803a8c2}
    # Pull out the serial-ish middle segment for a readable label.
    parts = path.strip("\\").lstrip("?\\").split("#")
    ident = parts[2] if len(parts) > 2 else path
    return f"{ident}"


def pick_and_send(paths, payload, title="SkyCut - Send to Machine USB"):
    """
    Show a small Tk window listing `paths` (device interface paths
    from find_usbprint_paths()), let the user pick one and click
    Send, and write `payload` (bytes) to it via send_job(), showing
    live chunk-progress as it goes. Blocks until the window closes.

    Always shown when there's at least one candidate device - even
    just one - so there's a visible confirmation step before
    anything is written to a physical device, matching the original
    "Send Test Job (USB)" picker's behavior.

    Returns (success, message) - message is a short, user-facing
    status/error string. success is False if the window was closed,
    Cancel was clicked without sending, or the write failed. Note
    "success" here means "all bytes were written without an I/O
    error" - the device itself never confirms job success, so this
    can't and doesn't claim to know the cut actually completed
    correctly.

    Raises ImportError if tkinter isn't available - callers should
    catch this and fall back to a non-interactive path.
    """
    import tkinter as tk

    result = {"success": False, "message": "Cancelled - no job sent."}

    root = tk.Tk()
    root.title(title)
    root.resizable(False, False)
    root.attributes("-topmost", True)
    root.lift()
    root.after(0, lambda: root.attributes("-topmost", False))

    tk.Label(
        root,
        text="SkyCut(s) found (USB-Printer class, VID 0483:PID 5750) - pick one:",
        anchor="w",
    ).pack(fill="x", padx=12, pady=(12, 4))

    listbox = tk.Listbox(root, width=70, height=min(6, max(2, len(paths))), exportselection=False)
    for p in paths:
        listbox.insert(tk.END, friendly_label(p))
    listbox.selection_set(0)
    listbox.pack(padx=12, pady=(0, 4))

    tk.Label(root, text="Connection: USB (Printer class) -- no baud rate; not a serial port.",
             fg="gray30", anchor="w").pack(fill="x", padx=12, pady=(0, 8))

    status = tk.Label(root, text="", anchor="w", justify="left", wraplength=420)
    status.pack(fill="x", padx=12, pady=(0, 8))

    def on_send():
        sel = listbox.curselection()
        if not sel:
            status.config(text="Select a device first.", fg="red")
            return
        device_path = paths[sel[0]]
        status.config(text="Sending...", fg="blue")
        root.update_idletasks()

        def on_progress(sent, total):
            pct = (sent / total * 100.0) if total else 100.0
            status.config(text=f"Sending... {sent}/{total} bytes ({pct:.0f}%)", fg="blue")
            status.update_idletasks()

        try:
            written = send_job(device_path, payload, on_progress=on_progress)
            result["success"] = True
            result["message"] = (
                f"Sent {written} bytes to {friendly_label(device_path)} via USB. "
                f"(Device gives no completion confirmation - check the machine itself.)"
            )
            status.config(text=result["message"], fg="green")
            root.after(1500, root.destroy)
        except OSError as e:
            result["success"] = False
            result["message"] = str(e)
            status.config(text=f"Failed: {e}", fg="red")

    btn_frame = tk.Frame(root)
    btn_frame.pack(pady=(0, 12))
    tk.Button(btn_frame, text="Send", command=on_send, width=14).pack(side="left", padx=6)
    tk.Button(btn_frame, text="Cancel", command=root.destroy, width=10).pack(side="left", padx=6)

    root.mainloop()
    return result["success"], result["message"]
