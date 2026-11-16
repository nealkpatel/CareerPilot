import ctypes
import os
import re
import time
import logging
from ctypes import wintypes

logger = logging.getLogger(__name__)

from playwright.sync_api import sync_playwright

from ..config import DEEPSEEK_URL, RESPONSE_TIMEOUT, CHROME_EXECUTABLE

# Park the scraping window off-screen, then hide it so it never sits on the desktop.
_SCRAPE_WINDOW_ARGS = [
    "--window-position=-32000,-32000",
    "--window-size=1100,800",
    "--disable-session-crashed-bubble",
    "--hide-crash-restore-bubble",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-infobars",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-background-timer-throttling",
]
_ENUM_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def _user32():
    user32 = ctypes.windll.user32
    if getattr(_user32, "_ready", False):
        return user32
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsWindow.restype = wintypes.BOOL
    user32.GetParent.argtypes = [wintypes.HWND]
    user32.GetParent.restype = wintypes.HWND
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.EnumWindows.argtypes = [_ENUM_PROC, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint,
    ]
    user32.SetWindowPos.restype = wintypes.BOOL
    _user32._ready = True
    return user32


def _hide_offscreen_chrome_windows():
    """Hide top-level Chrome windows that were already moved off the desktop."""
    if os.name != "nt":
        return []
    user32 = _user32()
    found = []

    def _enum(hwnd, _lparam):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            if user32.GetParent(hwnd):
                return True
            class_name = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, class_name, 256)
            if class_name.value != "Chrome_WidgetWin_1":
                return True
            rect = wintypes.RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return True
            if rect.left <= -2000 or rect.top <= -2000:
                user32.ShowWindow(hwnd, 0)
                found.append(int(hwnd))
        except Exception:
            return True
        return True

    user32.EnumWindows(_ENUM_PROC(_enum), 0)
    return found
from .text_clean import strip_scraped_ui_chrome
class TransientError(Exception):
    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind  # offline | busy | timeout


def classify_error(exc):
    text = str(exc or "").lower()
    if any(token in text for token in (
        "err_internet", "err_name_not_resolved", "err_connection", "net::",
        "offline", "dns", "network", "connection refused", "connection reset",
        "navigator.online", "no internet",
    )):
        return "offline"
    if any(token in text for token in (
        "busy", "too many", "rate limit", "429", "503", "502", "try again later",
        "overloaded", "high demand", "server is busy",
    )):
        return "busy"
    if any(token in text for token in ("timeout", "timed out", "did not finish responding")):
        return "timeout"
    return "error"


def _bundled_chromium_ok(playwright):
    try:
        path = playwright.chromium.executable_path
    except Exception:
        return False
    return bool(path) and os.path.isfile(path)


def _system_chrome_path():
    candidates = [
        CHROME_EXECUTABLE,
        os.environ.get("CHROME_PATH", ""),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ]
    seen = set()
    for path in candidates:
        path = (path or "").strip()
        if not path or path in seen:
            continue
        seen.add(path)
        if os.path.isfile(path):
            return path
    return ""


def _browser_launch_options(playwright):
    """Prefer the installed Chrome/Edge so the automated session and the
    sign-in window (see signin.py) share one browser build and profile format."""
    chrome = _system_chrome_path()
    if chrome:
        return {"executable_path": chrome}
    if _bundled_chromium_ok(playwright):
        logger.warning("No installed Chrome/Edge found; using Playwright Chromium")
        return {}
    return {"channel": "chrome"}


class DeepSeekClient:
    """
    Drives the DeepSeek web chat via a real Chromium browser using one user's
    persistent profile, so each user signs in once (in the sign-in
    window CareerPilot opens and shows them live) and every later run reuses their saved session.

    IMPORTANT: this automates a website's UI, not an official API. It is
    slower and more fragile than an API call, and DeepSeek's front-end
    markup can change at any time, which will break the CSS selectors
    below (marked with comments) — update them if that happens. It may
    also be against DeepSeek's terms of service to automate the site this
    way; that's on you to check and accept.
    """

    def __init__(self, profile_dir):
        self.profile_dir = profile_dir  # this user's own DeepSeek sign-in
        self._pw = None
        self.browser = None
        self.page = None
        self.pdf_browser = None
        self._concealed_hwnds = []

    def start(self):
        self._pw = sync_playwright().start()
        os.makedirs(self.profile_dir, exist_ok=True)
        options = _browser_launch_options(self._pw)
        last_error = None
        attempts = [dict(options)]
        if _bundled_chromium_ok(self._pw) and {} not in attempts:
            attempts.append({})

        self.browser = None
        used = {}
        for extra in attempts:
            try:
                used = extra
                self.browser = self._pw.chromium.launch_persistent_context(
                    self.profile_dir,
                    headless=False,
                    viewport={"width": 1100, "height": 800},
                    args=list(_SCRAPE_WINDOW_ARGS),
                    chromium_sandbox=True,  # Playwright adds --no-sandbox otherwise
                    **extra,
                )
                break
            except Exception as e:
                last_error = e
                logger.warning("Browser launch failed (%s): %s", extra, e)
        if self.browser is None:
            raise RuntimeError(
                "Could not open Chrome. Playwright Chromium is not installed "
                f"and system Chrome launch failed. Last error: {last_error}"
            ) from last_error
        self.page = self.browser.pages[0] if self.browser.pages else self.browser.new_page()
        self.browser.on("page", lambda page: self._conceal_window(page))
        self._conceal_window()
        self._goto_with_retries(DEEPSEEK_URL)
        self._conceal_window()

        # A second, plain (non-persistent) Chromium instance used only
        # for rendering resume/cover-letter HTML to PDF (see
        # pdf_builder.py). This reuses the SAME Playwright driver
        # (self._pw) rather than calling sync_playwright() again --
        # only one sync_playwright() driver can be active per thread at
        # a time, and starting a second one raises "Playwright Sync API
        # inside the asyncio loop".
        pdf_kw = {"headless": True, "chromium_sandbox": True, **used}
        self.pdf_browser = self._pw.chromium.launch(**pdf_kw)

    def _goto_with_retries(self, url, attempts=3, timeout_ms=60000):
        """
        Navigates to `url`, retrying on timeout with a longer wait each
        time. Uses wait_until="domcontentloaded" instead of the default
        "load", since DeepSeek's page can keep background connections
        open (chat streaming, analytics, etc.) that stop "load" from
        ever firing even though the page is fully usable.
        """
        last_error = None
        for attempt in range(1, attempts + 1):
            try:
                self.page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
                return
            except Exception as e:
                last_error = e
                print(f"[DeepSeekClient] goto attempt {attempt}/{attempts} failed: {e}")
                timeout_ms *= 2  # back off: 60s, 120s, 240s
        raise TimeoutError(
            f"Could not reach {url} after {attempts} attempts. "
            f"Check your network/VPN connection to chat.deepseek.com. "
            f"Last error: {last_error}"
        )

    def _conceal_window(self, page=None):
        """Keep the scraping Chrome window off-screen and hidden."""
        page = page or self.page
        try:
            if page and self.browser:
                session = self.browser.new_cdp_session(page)
                info = session.send("Browser.getWindowForTarget")
                session.send(
                    "Browser.setWindowBounds",
                    {
                        "windowId": info["windowId"],
                        "bounds": {
                            "left": -32000,
                            "top": -32000,
                            "width": 1100,
                            "height": 800,
                            "windowState": "normal",
                        },
                    },
                )
        except Exception as exc:
            logger.debug("Could not park the scraping window: %s", exc)
        try:
            for hwnd in _hide_offscreen_chrome_windows():
                if hwnd not in self._concealed_hwnds:
                    self._concealed_hwnds.append(hwnd)
        except Exception as exc:
            logger.debug("Could not hide the scraping window: %s", exc)

    def is_logged_in(self, timeout_ms=5000):
        try:
            self.page.wait_for_selector("textarea", timeout=timeout_ms)
            return True
        except Exception:
            return False

    def send_prompt(self, prompt: str, new_chat: bool = True, on_progress=None, expected_seconds=60) -> str:
        self._conceal_window()
        last_error = None
        for attempt in range(1, 4):
            try:
                self._assert_chat_ready()
                if on_progress:
                    on_progress(0.02)
                return self._send_prompt_once(
                    prompt,
                    new_chat=new_chat,
                    on_progress=on_progress,
                    expected_seconds=expected_seconds,
                )
            except Exception as e:
                kind = classify_error(e)
                last_error = TransientError(kind, str(e)) if kind != "error" else e
                if kind == "error" or attempt >= 3:
                    raise last_error
                time.sleep(4 * attempt)
        raise last_error

    def _assert_chat_ready(self):
        try:
            online = self.page.evaluate("() => navigator.onLine")
        except Exception as e:
            raise TransientError("offline", f"Lost the browser connection ({e}).") from e
        if online is False:
            raise TransientError("offline", "This computer looks offline. Check Wi-Fi or Ethernet.")
        try:
            snippet = (self.page.inner_text("body") or "")[:2500].lower()
        except Exception:
            snippet = ""
        busy_hints = (
            "server busy", "too many requests", "try again later",
            "high demand", "overloaded", "service unavailable",
        )
        if any(hint in snippet for hint in busy_hints):
            raise TransientError("busy", "DeepSeek is busy. Wait a moment and retry.")

    def _send_prompt_once(self, prompt: str, new_chat: bool = True, on_progress=None, expected_seconds=60) -> str:
        """
        Sends a prompt and returns the assistant's full response text.
        new_chat=True starts a fresh conversation. new_chat=False continues
        the current thread (used for question answers after resume PDFs).
        """
        previous_count = 0
        previous_last = ""
        if new_chat:
            try:
                self.page.click("text=New chat", timeout=3000)
            except Exception:
                pass
        else:
            bubbles = self.page.query_selector_all(
                "div.ds-markdown.ds-assistant-message-main-content"
            )
            previous_count = len(bubbles)
            if bubbles:
                try:
                    previous_last = bubbles[-1].inner_text()
                except Exception:
                    previous_last = ""

        if on_progress:
            on_progress(0.04)
        textarea = self._wait_for_input_ready()
        textarea.click()
        textarea.fill(prompt)
        textarea.press("Enter")
        if on_progress:
            on_progress(0.08)

        return self._wait_for_response(
            previous_count=previous_count,
            previous_last=previous_last,
            on_progress=on_progress,
            expected_seconds=expected_seconds,
        )

    def _wait_for_input_ready(self, timeout=30):
        textarea = self.page.wait_for_selector("textarea", timeout=15000)
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                disabled = textarea.get_attribute("disabled")
                if not disabled:
                    return textarea
            except Exception:
                textarea = self.page.query_selector("textarea") or textarea
            time.sleep(0.3)
        return textarea

    def _wait_for_response(self, previous_count=0, previous_last="", on_progress=None, expected_seconds=60):
        """
        Polls the last assistant message bubble until its text stops
        changing (streaming finished), or until RESPONSE_TIMEOUT elapses.
        When continuing a chat, ignores the previous assistant bubble.
        """
        started = time.time()
        deadline = started + RESPONSE_TIMEOUT
        last_text = None
        stable_polls = 0
        last_hide = 0.0
        expected = max(18.0, float(expected_seconds or 60))

        def tick():
            if not on_progress:
                return
            elapsed = max(0.0, time.time() - started)
            time_frac = min(0.96, elapsed / expected)
            chars = len(last_text or "")
            text_frac = min(0.96, 0.12 + chars / 8000.0) if chars else 0.0
            on_progress(max(0.08, time_frac, text_frac))

        # IMPORTANT: this must match ONLY the assistant's main response
        # container, not every element inside it. A loose selector like
        # "[class*='markdown']" also matches DeepSeek's per-paragraph
        # class "ds-markdown-paragraph" (it contains the substring
        # "markdown" too), so query_selector_all returns the container
        # AND every paragraph inside it — and bubbles[-1] then grabs the
        # LAST paragraph only (e.g. just the signature line), not the
        # full reply. Requiring both classes together avoids that, since
        # a single paragraph never carries both.
        while time.time() < deadline:
            tick()
            now = time.time()
            if now - last_hide > 1.5:
                self._conceal_window()
                last_hide = now
            bubbles = self.page.query_selector_all(
                "div.ds-markdown.ds-assistant-message-main-content"
            )
            if bubbles:
                try:
                    current = bubbles[-1].inner_text()
                except Exception:
                    time.sleep(0.4)
                    continue
                if (
                    len(bubbles) <= previous_count
                    and (not current.strip() or current == previous_last)
                ):
                    time.sleep(0.4)
                    continue
                if current == previous_last:
                    time.sleep(0.4)
                    continue
                if current == last_text and current.strip():
                    stable_polls += 1
                    if stable_polls >= 3:
                        if on_progress:
                            on_progress(0.97)
                        return current
                else:
                    stable_polls = 0
                    last_text = current
            time.sleep(0.4)

        raise TransientError("timeout", "DeepSeek did not finish responding in time. The server may be busy.")

    def stop(self):
        for closer in (
            lambda: self.pdf_browser and self.pdf_browser.close(),
            lambda: self.browser and self.browser.close(),
            lambda: self._pw and self._pw.stop(),
        ):
            try:
                closer()
            except Exception as exc:
                logger.debug("Engine shutdown step failed: %s", exc)
        self.pdf_browser = self.browser = self.page = self._pw = None


def parse_response(raw_text: str):
    """
    Splits scraped DeepSeek page text into (resume_json_str, cover_letter_text).
    Handles scraped plain text (no markdown fences) where the JSON is followed
    by a standalone "COVER LETTER" heading and then the letter body.
    """
    raw_text = raw_text.strip()

    # Strip markdown fences if present (covers raw API responses too)
    cleaned = re.sub(r"```json|```", "", raw_text, flags=re.IGNORECASE)

    # --- Extract the JSON object via brace-matching ---
    resume_json_str = ""
    start = cleaned.find("{")
    end = None
    if start != -1:
        depth = 0
        for i, ch in enumerate(cleaned[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end is not None:
            resume_json_str = cleaned[start:end + 1].strip()

    remainder = cleaned[end + 1:] if end is not None else ""

    heading_match = re.search(r"(?:cover letter|求职信|封面信)\s*\n", remainder, flags=re.IGNORECASE)
    if heading_match:
        remainder = remainder[heading_match.end():]
    else:
        remainder = re.sub(
            r"^\s*(?:cover letter|求职信|封面信)\s*",
            "",
            remainder,
            flags=re.IGNORECASE,
        )

    cover_letter = strip_scraped_ui_chrome(remainder)
    if re.fullmatch(r"(?:cover letter|求职信|封面信)[:：\s]*", cover_letter, flags=re.IGNORECASE):
        cover_letter = ""

    if not resume_json_str:
        logger.error(
            "parse_response could not find resume JSON.\n"
            "--- RAW RESPONSE START ---\n%s\n--- RAW RESPONSE END ---",
            raw_text,
        )

    return resume_json_str, cover_letter