"""Auto-shrink Roblox windows to cut GPU/VRAM use. Window-level only — never touches the process.

Usage:
    python roblox_resizer.py                  # shrink client area to 200x150, keep watching
    python roblox_resizer.py --size 120x90    # smaller render area
    python roblox_resizer.py --bare           # strip title bar/borders (window = pure render pixels)
    python roblox_resizer.py --tile           # tile windows in grid from top-left
    python roblox_resizer.py --list           # list Roblox windows, change nothing
    python roblox_resizer.py --once           # single pass, then exit
"""
import argparse
import ctypes
import ctypes.wintypes as wt
import sys
import time

user32 = ctypes.windll.user32

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
SWP_NOSENDCHANGING = 0x0400  # skip WM_WINDOWPOSCHANGING -> bypasses Roblox 800x600 min clamp

GWL_STYLE = -16
WS_CAPTION = 0x00C00000
WS_THICKFRAME = 0x00040000
WS_SYSMENU = 0x00080000
WS_MINMAXBOX = 0x00030000

ROBLOX_CLASS = "WINDOWSCLIENT"


def find_roblox_windows():
    hwnds = []
    buf = ctypes.create_unicode_buffer(256)

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            user32.GetClassNameW(hwnd, buf, 256)
            if buf.value == ROBLOX_CLASS:
                hwnds.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    return hwnds


def get_rect(hwnd):
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def get_client_size(hwnd):
    r = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(r))
    return r.right, r.bottom


def strip_frame(hwnd):
    style = user32.GetWindowLongW(hwnd, GWL_STYLE)
    new = style & ~(WS_CAPTION | WS_THICKFRAME | WS_SYSMENU | WS_MINMAXBOX)
    if new != style:
        user32.SetWindowLongW(hwnd, GWL_STYLE, new)
        user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0,
                            SWP_NOZORDER | SWP_NOACTIVATE | SWP_NOMOVE |
                            SWP_NOSIZE | SWP_FRAMECHANGED | SWP_NOSENDCHANGING)


def resize_client(hwnd, cw, ch, pos=None):
    """Size the CLIENT area (render pixels) to cw x ch."""
    ow, oh = get_rect(hwnd)[2:]
    ccw, cch = get_client_size(hwnd)
    dw, dh = ow - ccw, oh - cch  # border/caption overhead
    flags = SWP_NOZORDER | SWP_NOACTIVATE | SWP_NOSENDCHANGING
    if pos is None:
        user32.SetWindowPos(hwnd, 0, 0, 0, cw + dw, ch + dh, flags | SWP_NOMOVE)
    else:
        user32.SetWindowPos(hwnd, 0, pos[0], pos[1], cw + dw, ch + dh, flags)
    return get_client_size(hwnd)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", default="200x150", help="target client WxH, e.g. 120x90")
    ap.add_argument("--bare", action="store_true", help="strip title bar and borders")
    ap.add_argument("--tile", action="store_true", help="tile windows in a grid")
    ap.add_argument("--list", action="store_true", help="list windows only")
    ap.add_argument("--once", action="store_true", help="single pass, no watch loop")
    ap.add_argument("--interval", type=float, default=3.0, help="watch loop seconds")
    args = ap.parse_args()

    try:
        w, h = (int(v) for v in args.size.lower().split("x"))
    except ValueError:
        sys.exit(f"bad --size {args.size!r}, want WxH like 120x90")

    while True:
        hwnds = find_roblox_windows()
        if args.list:
            for hw in hwnds:
                x, y, ow, oh = get_rect(hw)
                cw, ch = get_client_size(hw)
                print(f"hwnd={hw:#x} outer {ow}x{oh} client {cw}x{ch} at ({x},{y})")
            if not hwnds:
                print("no Roblox windows found")
            return

        for i, hw in enumerate(hwnds):
            if args.bare:
                strip_frame(hw)
            cw, ch = get_client_size(hw)
            if not (cw == w and ch == h) or args.tile:
                pos = ((i % 8) * (w + 4), (i // 8) * (h + 4)) if args.tile else None
                aw, ah = resize_client(hw, w, h, pos)
                print(f"hwnd={hw:#x} client -> {aw}x{ah}")

        if args.once:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
