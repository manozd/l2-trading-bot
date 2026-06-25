"""Windows keyboard input for market search (supports ()-% and spaces)."""

from __future__ import annotations

import sys
import time


def _require_win32():
    if sys.platform != "win32":
        raise OSError("PC keyboard helpers require Windows")
    import ctypes

    return ctypes.windll.user32


def _vk(code: int, *, down: bool) -> None:
    user32 = _require_win32()
    flags = 0 if down else 2
    user32.keybd_event(code, 0, flags, 0)


def tap_vk(vk: int, *, hold_s: float = 0.02) -> None:
    _vk(vk, down=True)
    time.sleep(hold_s)
    _vk(vk, down=False)
    time.sleep(0.02)


def ctrl_a() -> None:
    VK_CONTROL, VK_A = 0x11, 0x41
    _vk(VK_CONTROL, down=True)
    tap_vk(VK_A)
    _vk(VK_CONTROL, down=False)
    time.sleep(0.04)


def ctrl_v() -> None:
    VK_CONTROL, VK_V = 0x11, 0x56
    _vk(VK_CONTROL, down=True)
    tap_vk(VK_V)
    _vk(VK_CONTROL, down=False)
    time.sleep(0.04)


def set_clipboard_text(text: str) -> None:
    """Put UTF-16 text on the Windows clipboard."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002

    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE

    if not user32.OpenClipboard(None):
        raise OSError("OpenClipboard failed")
    try:
        user32.EmptyClipboard()
        payload = text.encode("utf-16-le") + b"\x00\x00"
        h_mem = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(payload))
        if not h_mem:
            raise OSError("GlobalAlloc failed")
        p_mem = kernel32.GlobalLock(h_mem)
        if not p_mem:
            kernel32.GlobalFree(h_mem)
            raise OSError("GlobalLock failed")
        ctypes.memmove(p_mem, payload, len(payload))
        kernel32.GlobalUnlock(h_mem)
        if not user32.SetClipboardData(CF_UNICODETEXT, h_mem):
            kernel32.GlobalFree(h_mem)
            raise OSError("SetClipboardData failed")
    finally:
        user32.CloseClipboard()


def paste_search_text(text: str) -> None:
    """Copy to clipboard and paste into focused search field (Ctrl+A, Ctrl+V)."""
    validate_search_text(text)
    set_clipboard_text(text)
    ctrl_a()
    ctrl_v()
    time.sleep(0.05)


def type_search_text(text: str) -> None:
    """Type into focused search field (Ctrl+A first so each query replaces the old one)."""
    import ctypes
    from ctypes import wintypes

    validate_search_text(text)
    user32 = _require_win32()
    ctrl_a()

    KEYEVENTF_UNICODE = 0x0004
    KEYEVENTF_KEYUP = 0x0002

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        ]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("ki", KEYBDINPUT)]

    def uni_char(ch: str, *, keyup: bool) -> None:
        inp = INPUT(type=1, ki=KEYBDINPUT(0, ord(ch), KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if keyup else 0), 0, None))
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))

    for ch in text:
        uni_char(ch, keyup=False)
        time.sleep(0.012)
        uni_char(ch, keyup=True)
        time.sleep(0.012)
    time.sleep(0.05)


def tap_enter() -> None:
    tap_vk(0x0D)


def clear_field() -> None:
    ctrl_a()
    tap_vk(0x08)  # Backspace
    time.sleep(0.05)


# Allowed in L2 item names for search.
SEARCH_ALLOWED = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789()- %:'")


def validate_search_text(text: str) -> str:
    bad = {c for c in text if c not in SEARCH_ALLOWED}
    if bad:
        raise ValueError(f"Item name contains unsupported characters: {sorted(bad)!r}")
    return text
