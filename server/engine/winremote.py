"""Windows-level live view of, and input into, one window: the Chrome window of a user's DeepSeek sign-in.

Nothing is attached to Chrome (no DevTools, no automation), because DeepSeek's captcha rejects a browser that is being
driven or inspected even when a person solves it correctly. Instead:
  * pixels come from PrintWindow, which renders the window's own content even while it sits behind other windows;
  * clicks, drags, wheel and typing go through SendInput, which is ordinary mouse and keyboard input as far as Chrome
    and the captcha can tell.
This needs the CareerPilot server to run in a logged-in, *unlocked* desktop session (a locked screen swallows input).
On a VPS, leave Remote Desktop with deploy\\disconnect-rdp.cmd, which hands the session to the console without locking it.
"""
import ctypes
import ctypes.wintypes as wt
import hashlib
import io
import threading
import time

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class RemoteError(RuntimeError):
    pass


# ----------------------------------------------------------------------------- Win32 plumbing
# Work in physical pixels: without this, window coordinates are scaled on high-DPI screens and clicks land off target.
try:
    user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    if not user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):   # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        user32.SetProcessDPIAware()
except Exception:
    pass

# 64-bit handles must not be truncated to int: declare the pointer-sized results.
for _fn in ("GetDC", "GetForegroundWindow", "OpenInputDesktop"):
    getattr(user32, _fn).restype = ctypes.c_void_p
for _fn in ("CreateCompatibleDC", "CreateDIBSection", "SelectObject"):
    getattr(gdi32, _fn).restype = ctypes.c_void_p
user32.GetDC.argtypes = [ctypes.c_void_p]
user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
user32.PrintWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wt.UINT]
user32.GetClientRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.RECT)]
user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.RECT)]
user32.ClientToScreen.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.POINT)]
user32.IsWindow.argtypes = user32.IsWindowVisible.argtypes = user32.IsIconic.argtypes = [ctypes.c_void_p]
user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
user32.SetForegroundWindow.argtypes = user32.BringWindowToTop.argtypes = [ctypes.c_void_p]
user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.DWORD)]
user32.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
user32.AttachThreadInput.argtypes = [wt.DWORD, wt.DWORD, wt.BOOL]
user32.OpenInputDesktop.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
user32.CloseDesktop.argtypes = [ctypes.c_void_p]
user32.WindowFromPoint.argtypes = [wt.POINT]
user32.WindowFromPoint.restype = ctypes.c_void_p
user32.GetAncestor.argtypes = [ctypes.c_void_p, wt.UINT]
user32.GetAncestor.restype = ctypes.c_void_p
user32.SendMessageTimeoutW.argtypes = [ctypes.c_void_p, wt.UINT, ctypes.c_void_p, ctypes.c_void_p, wt.UINT, wt.UINT, ctypes.POINTER(ctypes.c_void_p)]
user32.SendMessageTimeoutW.restype = ctypes.c_void_p
gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
gdi32.DeleteObject.argtypes = gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
gdi32.BitBlt.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, wt.DWORD]

WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, ctypes.c_void_p, wt.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, wt.LPARAM]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


gdi32.CreateDIBSection.argtypes = [ctypes.c_void_p, ctypes.POINTER(BITMAPINFO), wt.UINT, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, wt.DWORD]

ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD), ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wt.DWORD), ("wParamL", wt.WORD), ("wParamH", wt.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = [wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wt.UINT

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
MOUSEEVENTF_MOVE, MOUSEEVENTF_ABSOLUTE, MOUSEEVENTF_VIRTUALDESK, MOUSEEVENTF_WHEEL = 0x0001, 0x8000, 0x4000, 0x0800
MOUSE_BUTTONS = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010), "middle": (0x0020, 0x0040)}
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x0001, 0x0002, 0x0004
VK = {"Enter": 0x0D, "Backspace": 0x08, "Tab": 0x09, "Escape": 0x1B, "Delete": 0x2E, "Home": 0x24, "End": 0x23,
      "PageUp": 0x21, "PageDown": 0x22, "ArrowLeft": 0x25, "ArrowUp": 0x26, "ArrowRight": 0x27, "ArrowDown": 0x28, " ": 0x20}
EXTENDED = {"Delete", "Home", "End", "PageUp", "PageDown", "ArrowLeft", "ArrowUp", "ArrowRight", "ArrowDown"}
VK_SHIFT, VK_CONTROL, VK_MENU = 0x10, 0x11, 0x12
SW_SHOWNOACTIVATE, SW_MINIMIZE, SW_RESTORE = 4, 6, 9
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x0001, 0x0002, 0x0010, 0x0040
HWND_TOPMOST = ctypes.c_void_p(-1)
WM_NCHITTEST, HTCAPTION, SMTO_ABORTIFHUNG = 0x0084, 2, 0x0002

_INPUT_LOCK = threading.Lock()   # one window at a time: foreground + SendInput are machine-wide


def _check(ok, what):
    if not ok:
        raise RemoteError(f"{what} failed (Windows error {ctypes.get_last_error()}).")


# ----------------------------------------------------------------------------- windows
def find_window(pid, timeout=20.0):
    """The visible, titled, largest top-level window of a process (Chrome's browser window), or 0 if none appears."""
    deadline = time.time() + timeout
    while True:
        best = [0, 0]

        def cb(hwnd, _lp):
            owner = wt.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value == pid and user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0:
                r = wt.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(r))
                area = max(0, r.right - r.left) * max(0, r.bottom - r.top)
                if area > best[1]:
                    best[0], best[1] = hwnd, area
            return True

        user32.EnumWindows(WNDENUMPROC(cb), 0)
        if best[0] or time.time() > deadline:
            return best[0]
        time.sleep(0.25)


wtsapi32 = ctypes.WinDLL("wtsapi32", use_last_error=True)
wtsapi32.WTSQuerySessionInformationW.argtypes = [ctypes.c_void_p, wt.DWORD, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wt.DWORD)]
wtsapi32.WTSFreeMemory.argtypes = [ctypes.c_void_p]


def screen_locked():
    """True when this Windows session sits at the lock screen or has no usable desktop (then input is swallowed).

    Windows 11's lock overlay (LockApp) runs on the normal desktop, so OpenInputDesktop alone says "unlocked"; the
    session flags from WTSQuerySessionInformation(WTSSessionInfoEx) know better."""
    buf, size = ctypes.c_void_p(), wt.DWORD()
    if wtsapi32.WTSQuerySessionInformationW(None, 0xFFFFFFFF, 25, ctypes.byref(buf), ctypes.byref(size)):   # current session, WTSSessionInfoEx
        try:
            if size.value >= 20:
                # WTSINFOEXW: DWORD Level; then WTSINFOEX_LEVEL1_W { SessionId, SessionState, LONG SessionFlags, ... } at offset 8
                if ctypes.c_long.from_address(buf.value + 16).value == 0:   # WTS_SESSIONSTATE_LOCK
                    return True
        finally:
            wtsapi32.WTSFreeMemory(buf)
    h = user32.OpenInputDesktop(0, False, 0x0100)   # DESKTOP_SWITCHDESKTOP: fails on the secure desktop or without a desktop
    if not h:
        return True
    user32.CloseDesktop(h)
    return False


LOCKED_MESSAGE = ("The server computer's screen is locked, so the window can't receive clicks or typing. Unlock it there "
                  "— on a VPS, leave Remote Desktop with deploy\\disconnect-rdp.cmd instead of locking or signing out.")


def _virtual_screen():
    return tuple(user32.GetSystemMetrics(i) for i in (76, 77, 78, 79))   # x, y, width, height


def fit_on_screen(hwnd):
    """Keep the whole window inside the virtual screen: input to points off screen gets clamped and lands elsewhere."""
    vx, vy, vw, vh = _virtual_screen()
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    w2, h2 = min(w, vw), min(h, vh)
    x = max(vx, min(r.left, vx + vw - w2))
    y = max(vy, min(r.top, vy + vh - h2))
    if (x, y, w2, h2) != (r.left, r.top, w, h):
        user32.SetWindowPos(hwnd, None, x, y, w2, h2, SWP_SHOWWINDOW)


def window_at(x, y):
    """The top-level window under a screen point (a popup or menu counts as its owner), or None."""
    h = user32.WindowFromPoint(wt.POINT(int(x), int(y)))
    return user32.GetAncestor(h, 3) if h else None   # GA_ROOTOWNER


def _pid_of(hwnd):
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def owns_point(hwnd, x, y):
    """Whether a click at this screen point reaches hwnd (or a dropdown/bubble of the same Chrome) and not some other window."""
    top = window_at(x, y)
    return bool(top) and (top == hwnd or _pid_of(top) == _pid_of(hwnd))


def _is_foreground(hwnd, wait=0.0):
    deadline = time.time() + wait
    while True:
        if user32.GetForegroundWindow() == hwnd:
            return True
        if time.time() >= deadline:
            return False
        time.sleep(0.02)


# ---- ways a background process may still make a window the foreground one (Windows normally refuses), cheapest first
def _activate_after_input(hwnd):
    # Windows refuses SetForegroundWindow from a background process, but allows it to the process that produced the
    # latest input: a relative mouse move of (0, 0) is input that moves nothing.
    inp = INPUT(type=INPUT_MOUSE)
    inp.mi.dwFlags = MOUSEEVENTF_MOVE
    _send([inp])
    user32.SetForegroundWindow(hwnd)


def _activate_attached(hwnd):
    fg = user32.GetForegroundWindow()
    fg_thread = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    me = kernel32.GetCurrentThreadId()
    if not fg_thread or fg_thread == me or not user32.AttachThreadInput(fg_thread, me, True):
        return
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        user32.AttachThreadInput(fg_thread, me, False)


_caption_click = [0.0, 0]   # when and with which offset the last title-bar click happened: two must never make a double-click


def _activate_caption_click(hwnd):
    """A real click on the window's own title bar: Windows activates it and nothing inside the page is touched.
    Only at a spot the window itself reports as caption and that no other window covers."""
    rc = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rc))
    now = time.time()
    shift = 24 if now - _caption_click[0] < 1.0 and _caption_click[1] == 0 else 0
    for dx in (170, 220, 270, 320, 400, 500):
        if rc.right - dx - shift < 10:
            return
        pt = wt.POINT(rc.right - dx - shift, 10)
        user32.ClientToScreen(hwnd, ctypes.byref(pt))
        hit = ctypes.c_void_p()
        lparam = ctypes.c_void_p(((pt.y & 0xFFFF) << 16) | (pt.x & 0xFFFF))
        if not user32.SendMessageTimeoutW(hwnd, WM_NCHITTEST, None, lparam, SMTO_ABORTIFHUNG, 200, ctypes.byref(hit)):
            return
        if (hit.value or 0) == HTCAPTION and window_at(pt.x, pt.y) == hwnd:
            _caption_click[:] = [now, shift]
            down, up = MOUSE_BUTTONS["left"]
            _mouse(0, pt.x, pt.y)
            time.sleep(0.04)
            _mouse(down, pt.x, pt.y)
            time.sleep(0.05)
            _mouse(up, pt.x, pt.y)
            return


def _activate_restore(hwnd):
    """Minimising and restoring makes Windows itself activate the window (a brief flicker on the server's screen)."""
    user32.ShowWindow(hwnd, SW_MINIMIZE)
    time.sleep(0.25)
    user32.ShowWindow(hwnd, SW_RESTORE)


def focus(hwnd):
    """Bring the window to the front and give it the keyboard, pressing no key in it. Returns whether it is now the
    foreground window. (Tapping Alt would also lift Windows' foreground lock, but Chrome moves its focus to the menu
    button on an Alt tap, and the typing that follows is lost.)"""
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    fit_on_screen(hwnd)
    # Stay above the server's other windows: injected clicks land on whatever is on top at that spot.
    user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW | SWP_NOACTIVATE)
    if _is_foreground(hwnd):
        return True
    for step in (_activate_after_input, _activate_attached, _activate_caption_click, _activate_restore):
        try:
            step(hwnd)
        except RemoteError:
            continue
        if _is_foreground(hwnd, 0.3):
            time.sleep(0.25)   # Chrome hands the keyboard to the page a moment after it becomes foreground
            return True
    return False


def _uncovered(hwnd, w, h):
    """Whether the window is in front with nothing over its client area, so the screen itself shows it."""
    if user32.GetForegroundWindow() != hwnd:
        return False
    for cx, cy in ((1, 1), (w - 2, 1), (1, h - 2), (w - 2, h - 2), (w // 2, h // 2)):
        pt = wt.POINT(cx, cy)
        user32.ClientToScreen(hwnd, ctypes.byref(pt))
        if not owns_point(hwnd, pt.x, pt.y):
            return False
    return True


# ----------------------------------------------------------------------------- pixels
def capture(hwnd):
    """BGRA pixels of the window's client area (top-down) with its width and height. Works behind other windows."""
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
    rc = wt.RECT()
    _check(user32.GetClientRect(hwnd, ctypes.byref(rc)), "GetClientRect")
    w, h = rc.right, rc.bottom
    if w <= 0 or h <= 0:
        raise RemoteError("The sign-in window has no visible area right now.")
    wdc = user32.GetDC(hwnd)
    _check(wdc, "GetDC")
    mdc = hbmp = None
    try:
        mdc = gdi32.CreateCompatibleDC(wdc)
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth, bmi.bmiHeader.biHeight = w, -h
        bmi.bmiHeader.biPlanes, bmi.bmiHeader.biBitCount = 1, 32
        bits = ctypes.c_void_p()
        hbmp = gdi32.CreateDIBSection(wdc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
        _check(hbmp, "CreateDIBSection")
        old = gdi32.SelectObject(mdc, hbmp)

        def from_window():   # PrintWindow renders the window even behind others, but not Chrome's own popups
            ok = user32.PrintWindow(hwnd, mdc, 3)   # PW_CLIENTONLY | PW_RENDERFULLCONTENT
            return ctypes.string_at(bits, w * h * 4) if ok else b""

        def from_screen():   # the screen shows dropdowns and bubbles too, but only while nothing covers the window
            pt = wt.POINT(0, 0)
            user32.ClientToScreen(hwnd, ctypes.byref(pt))
            sdc = user32.GetDC(None)
            try:
                gdi32.BitBlt(mdc, 0, 0, w, h, sdc, pt.x, pt.y, 0x00CC0020)   # SRCCOPY
            finally:
                user32.ReleaseDC(None, sdc)
            return ctypes.string_at(bits, w * h * 4)

        first, second = (from_screen, from_window) if _uncovered(hwnd, w, h) else (from_window, from_screen)
        data = first()
        if not data or not any(data[::1013]):   # refused, or an all-black frame: use the other source
            data = second()
        gdi32.SelectObject(mdc, old)
        return data, w, h
    finally:
        if hbmp:
            gdi32.DeleteObject(hbmp)
        if mdc:
            gdi32.DeleteDC(mdc)
        user32.ReleaseDC(hwnd, wdc)


def encode(data, w, h, max_width=1100, quality=68):
    """Compress a BGRA frame for the browser: WebP when Pillow has it, else JPEG. Returns (bytes, media type)."""
    try:
        from PIL import Image
    except ImportError as exc:
        raise RemoteError("The live sign-in view needs the Pillow package on the server: pip install pillow") from exc
    img = Image.frombuffer("RGBA", (w, h), data, "raw", "BGRA", 0, 1).convert("RGB")
    if w > max_width:
        img = img.resize((max_width, max(1, round(h * max_width / w))), Image.BILINEAR)
    buf = io.BytesIO()
    try:
        img.save(buf, "WEBP", quality=quality, method=0)
        return buf.getvalue(), "image/webp"
    except Exception:
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=quality, optimize=False)
        return buf.getvalue(), "image/jpeg"


# ----------------------------------------------------------------------------- input
def _send(inputs):
    arr = (INPUT * len(inputs))(*inputs)
    sent = user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))
    if sent != len(inputs):
        raise RemoteError(f"Windows refused the input (error {ctypes.get_last_error()}). Is the server's screen locked, "
                          "a secure prompt open, or CareerPilot running with fewer rights than Chrome?")


def _mouse(flags, x=None, y=None, data=0):
    inp = INPUT(type=INPUT_MOUSE)
    if x is not None:
        vx, vy, vw, vh = _virtual_screen()
        inp.mi.dx = int(round((x - vx) * 65535 / max(1, vw - 1)))
        inp.mi.dy = int(round((y - vy) * 65535 / max(1, vh - 1)))
        flags |= MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
    inp.mi.dwFlags = flags
    inp.mi.mouseData = data & 0xFFFFFFFF
    _send([inp])


def _key(vk, up=False, extended=False):
    inp = INPUT(type=INPUT_KEYBOARD)
    inp.ki.wVk = vk
    inp.ki.wScan = user32.MapVirtualKeyW(vk, 0)
    inp.ki.dwFlags = (KEYEVENTF_KEYUP if up else 0) | (KEYEVENTF_EXTENDEDKEY if extended else 0)
    _send([inp])


def _unicode(unit, up=False):
    inp = INPUT(type=INPUT_KEYBOARD)
    inp.ki.wScan = unit
    inp.ki.dwFlags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0)
    _send([inp])


def type_text(text):
    for ch in text[:2000]:
        if ch in "\r\n":
            if ch == "\n":
                _key(VK["Enter"])
                _key(VK["Enter"], up=True)
            continue
        for unit in [int.from_bytes(ch.encode("utf-16-le")[i:i + 2], "little") for i in range(0, len(ch.encode("utf-16-le")), 2)]:
            _unicode(unit)
            _unicode(unit, up=True)
        time.sleep(0.012)


def press(key, ctrl=False, shift=False, alt=False):
    """A named key (Enter, Backspace, arrows…) or a single character, with optional modifiers (Ctrl+A, Shift+Tab…)."""
    if key in VK:
        vk, ext = VK[key], key in EXTENDED
    elif len(key) == 1:
        if not ctrl and not alt:
            return type_text(key)
        code = user32.VkKeyScanW(ord(key))
        if code == -1:
            raise ValueError(f"No key for {key!r}")
        vk, ext = code & 0xFF, False
        shift = shift or bool(code & 0x100)
    else:
        raise ValueError(f"Unsupported key {key!r}")
    mods = [m for m, on in ((VK_CONTROL, ctrl), (VK_SHIFT, shift), (VK_MENU, alt)) if on]
    for m in mods:
        _key(m)
    _key(vk, extended=ext)
    _key(vk, up=True, extended=ext)
    for m in reversed(mods):
        _key(m, up=True)


class Remote:
    """One window being viewed and driven. Coordinates in every action are client-area pixels of that window."""

    def __init__(self, hwnd):
        self.hwnd = hwnd
        self._cache = (0.0, None, b"", 0, 0, "")   # time, hash, encoded, w, h, media type
        self._lock = threading.Lock()

    def alive(self):
        return bool(user32.IsWindow(self.hwnd))

    def frame(self, last_hash=None, max_age=0.15):
        """(encoded, hash, w, h, media type, locked); encoded is None when the picture hasn't changed since last_hash."""
        with self._lock:
            t, digest, enc, w, h, mime = self._cache
            if time.time() - t > max_age:
                data, w, h = capture(self.hwnd)
                digest = hashlib.blake2b(data, digest_size=8).hexdigest()
                if digest != self._cache[1]:
                    enc, mime = encode(data, w, h)
                self._cache = (time.time(), digest, enc, w, h, mime)
            return (None if digest == last_hash else enc), digest, w, h, mime, screen_locked()

    def _screen_point(self, x, y, w, h):
        pt = wt.POINT(max(0, min(int(x), w - 1)), max(0, min(int(y), h - 1)))
        user32.ClientToScreen(self.hwnd, ctypes.byref(pt))
        return pt.x, pt.y

    def _on_top(self, x, y):
        """Never click where another window covers ours: the click would land in that window instead."""
        if owns_point(self.hwnd, x, y):
            return
        focus(self.hwnd)
        if not owns_point(self.hwnd, x, y):
            raise RemoteError("Another window on the server computer is covering the DeepSeek sign-in window at that "
                              "spot, so the click would go there instead. Move it aside on the server and try again.")

    def act(self, actions):
        """Replay a batch of browser-side events as real input. Returns whether the window had keyboard focus."""
        if not self.alive():
            raise RemoteError("The sign-in window is gone.")
        if screen_locked():
            raise RemoteError(LOCKED_MESSAGE)
        with _INPUT_LOCK:
            focused = focus(self.hwnd)
            rc = wt.RECT()
            user32.GetClientRect(self.hwnd, ctypes.byref(rc))
            w, h = max(1, rc.right), max(1, rc.bottom)
            for a in actions:
                kind = a.get("type")
                if kind == "click":
                    down, up = MOUSE_BUTTONS.get(a.get("button", "left"), MOUSE_BUTTONS["left"])
                    x, y = self._screen_point(a.get("x", 0), a.get("y", 0), w, h)
                    self._on_top(x, y)
                    _mouse(0, x, y)
                    time.sleep(0.04)
                    for i in range(1 if int(a.get("count", 1)) < 2 else 2):
                        _mouse(down, x, y)
                        time.sleep(0.05)
                        _mouse(up, x, y)
                        time.sleep(0.07)
                    focused = True   # a click inside the window focuses it
                elif kind == "drag":
                    pts = [p for p in a.get("points", []) if isinstance(p, (list, tuple)) and len(p) >= 2][:400]
                    if len(pts) < 2:
                        continue
                    down, up = MOUSE_BUTTONS.get(a.get("button", "left"), MOUSE_BUTTONS["left"])
                    if len(pts) == 2:   # a bare start/end: walk there in steps, like a hand does
                        (x0, y0), (x1, y1) = pts[0][:2], pts[1][:2]
                        pts = [(x0 + (x1 - x0) * i / 14, y0 + (y1 - y0) * i / 14, i * 18) for i in range(15)]
                    x, y = self._screen_point(pts[0][0], pts[0][1], w, h)
                    self._on_top(x, y)
                    _mouse(0, x, y)
                    time.sleep(0.05)
                    _mouse(down, x, y)
                    time.sleep(0.06)
                    prev_t = pts[0][2] if len(pts[0]) > 2 else 0
                    for p in pts[1:]:
                        t = p[2] if len(p) > 2 else prev_t + 16
                        time.sleep(min(0.08, max(0.004, (t - prev_t) / 1000)))
                        prev_t = t
                        x, y = self._screen_point(p[0], p[1], w, h)
                        _mouse(0, x, y)
                    time.sleep(0.06)
                    _mouse(up, x, y)
                    focused = True
                elif kind == "scroll":
                    x, y = self._screen_point(a.get("x", w // 2), a.get("y", h // 2), w, h)
                    self._on_top(x, y)
                    _mouse(0, x, y)
                    dy = float(a.get("dy", 0))
                    notches = max(1, min(10, round(abs(dy) / 100)))
                    for _ in range(notches):
                        _mouse(MOUSEEVENTF_WHEEL, data=(-120 if dy > 0 else 120))
                        time.sleep(0.02)
                elif kind == "type":
                    if focused:
                        type_text(str(a.get("text", "")))
                elif kind == "key":
                    if focused:
                        press(str(a.get("key", "")), bool(a.get("ctrl")), bool(a.get("shift")), bool(a.get("alt")))
                else:
                    raise ValueError(f"Unknown action {kind!r}")
                time.sleep(0.1 if kind in ("click", "drag") else 0.02)   # let Chrome settle a press before the next one
            return focused
