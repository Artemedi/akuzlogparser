"""Windows memory snapshots for an external benchmark process and its children.

PeakWorkingSetSize is an OS process high-water mark; sampled private bytes
and simultaneous process-tree totals are lower bounds between polls.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import sys

if sys.platform == "win32":
    _kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    _psapi = ctypes.WinDLL("psapi", use_last_error=True)
    _kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel.OpenProcess.restype = wintypes.HANDLE
    _kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    _kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel.CloseHandle.restype = wintypes.BOOL

    class _Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD),
                    ("peak_ws", ctypes.c_size_t), ("ws", ctypes.c_size_t),
                    ("peak_paged", ctypes.c_size_t), ("paged", ctypes.c_size_t),
                    ("peak_nonpaged", ctypes.c_size_t), ("nonpaged", ctypes.c_size_t),
                    ("pagefile", ctypes.c_size_t), ("peak_pagefile", ctypes.c_size_t),
                    ("private", ctypes.c_size_t)]

    class _Process(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("uses", wintypes.DWORD),
                    ("pid", wintypes.DWORD), ("heap", ctypes.c_void_p),
                    ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
                    ("parent", wintypes.DWORD), ("priority", wintypes.LONG),
                    ("flags", wintypes.DWORD), ("name", wintypes.WCHAR * 260)]

    _psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE,
                                             ctypes.POINTER(_Counters), wintypes.DWORD]
    _psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    _kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Process)]
    _kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Process)]
    _kernel.Process32FirstW.restype = wintypes.BOOL
    _kernel.Process32NextW.restype = wintypes.BOOL


def sample(pid: int):
    """Read OS high-water marks and current private bytes; no third-party package."""
    if sys.platform != "win32":
        raise RuntimeError("Windows process memory counters require Windows")
    handle = _kernel.OpenProcess(0x1000 | 0x0010, False, pid)
    if not handle:
        return None  # Process exited or access was denied.
    try:
        counters = _Counters()
        counters.cb = ctypes.sizeof(counters)
        if not _psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return None
        return dict(working_set_bytes=counters.ws, private_bytes=counters.private,
                    peak_working_set_bytes=counters.peak_ws,
                    peak_pagefile_bytes=counters.peak_pagefile)
    finally:
        _kernel.CloseHandle(handle)


def tree_pids(pid: int):
    """Include launcher and descendants, including a PyInstaller one-file child."""
    if sys.platform != "win32":
        raise RuntimeError("Windows process tree enumeration requires Windows")
    handle = _kernel.CreateToolhelp32Snapshot(0x2, 0)
    if ctypes.c_void_p(handle).value == ctypes.c_void_p(-1).value:
        return {pid}
    parents = {}
    try:
        entry = _Process()
        entry.size = ctypes.sizeof(entry)
        if _kernel.Process32FirstW(handle, ctypes.byref(entry)):
            while True:
                parents[entry.pid] = entry.parent
                if not _kernel.Process32NextW(handle, ctypes.byref(entry)):
                    break
    finally:
        _kernel.CloseHandle(handle)
    result = {pid}
    while True:
        expanded = result | {child for child, parent in parents.items()
                             if parent in result}
        if expanded == result:
            return result
        result = expanded
