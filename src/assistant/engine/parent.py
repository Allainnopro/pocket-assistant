"""Is the desktop app that started the engine still running? (`assistant engine --parent-pid`)

Tauri doesn't promise to stop its child when the app exits or crashes, so the engine
looks after itself: it checks its parent every few seconds and stops when it is gone.
On Windows this only *looks* at the process (SYNCHRONIZE access): os.kill there would
terminate it.
"""

from __future__ import annotations

import os
import sys

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _kernel32.WaitForSingleObject.restype = wintypes.DWORD
    _kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _SYNCHRONIZE = 0x00100000
    _WAIT_TIMEOUT = 0x00000102

    def parent_alive(pid: int) -> bool:
        handle = _kernel32.OpenProcess(_SYNCHRONIZE, False, pid)
        if not handle:
            return False  # no such process (or one we may not see, which isn't our parent)
        try:
            return _kernel32.WaitForSingleObject(handle, 0) == _WAIT_TIMEOUT  # still running
        finally:
            _kernel32.CloseHandle(handle)

else:

    def parent_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)  # signal 0 checks existence only
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
