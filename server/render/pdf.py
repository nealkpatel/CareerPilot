"""HTML -> PDF using a dedicated headless Chromium thread (Playwright's sync API is thread-bound)."""

import hashlib
import logging
import os
import queue
import threading
from concurrent.futures import Future

from ..config import FILES_DIR

logger = logging.getLogger(__name__)
_CACHE = os.path.join(FILES_DIR, "pdf_cache")
os.makedirs(_CACHE, exist_ok=True)


class _PdfThread(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True, name="pdf-renderer")
        self.jobs = queue.Queue()
        self._pw = None
        self._browser = None

    def _ensure(self):
        if self._browser:
            return
        from playwright.sync_api import sync_playwright
        from ..engine.deepseek_client import _system_chrome_path, _bundled_chromium_ok

        self._pw = sync_playwright().start()
        attempts = [{}] if _bundled_chromium_ok(self._pw) else []
        chrome = _system_chrome_path()
        if chrome:
            attempts.append({"executable_path": chrome})
        attempts.append({"channel": "chrome"})
        attempts.append({"channel": "msedge"})
        last = None
        for extra in attempts:
            try:
                self._browser = self._pw.chromium.launch(headless=True, chromium_sandbox=True, **extra)
                return
            except Exception as exc:
                last = exc
        raise RuntimeError(f"Could not start a browser for PDF export: {last}")

    def run(self):
        while True:
            html, margin, fut = self.jobs.get()
            try:
                self._ensure()
                if margin == "__fetch__":
                    page = self._browser.new_page()
                    try:
                        page.goto(html, wait_until="domcontentloaded", timeout=30000)
                        try:
                            page.wait_for_load_state("networkidle", timeout=8000)
                        except Exception:
                            pass
                        fut.set_result(page.content())
                    finally:
                        page.close()
                    continue
                page = self._browser.new_page()
                try:
                    if margin == "css":
                        # styled templates define page size + margins in CSS and load fonts from
                        # /assets/fonts - give the page our server as its base URL
                        from ..config import PORT
                        html = html.replace("<head>", f'<head><base href="http://127.0.0.1:{PORT}/">', 1)
                        try:
                            page.set_content(html, wait_until="networkidle", timeout=20000)
                        except Exception:
                            page.set_content(html, wait_until="load")
                        page.emulate_media(media="print")
                        data = page.pdf(prefer_css_page_size=True, print_background=True,
                                        margin={"top": "0", "bottom": "0", "left": "0", "right": "0"})
                    else:
                        page.set_content(html, wait_until="load")
                        page.emulate_media(media="print")
                        data = page.pdf(
                            format="Letter",
                            print_background=True,
                            margin={"top": margin, "bottom": margin, "left": margin, "right": margin},
                        )
                finally:
                    page.close()
                fut.set_result(data)
            except Exception as exc:
                logger.exception("PDF render failed")
                try:
                    if self._browser:
                        self._browser.close()
                except Exception:
                    pass
                self._browser = None
                fut.set_exception(exc)


_thread = None
_lock = threading.Lock()


def _submit(payload, margin, timeout):
    global _thread
    with _lock:
        if _thread is None or not _thread.is_alive():
            _thread = _PdfThread()
            _thread.start()
    fut = Future()
    _thread.jobs.put((payload, margin, fut))
    return fut.result(timeout=timeout)


def fetch_rendered_html(url: str, timeout: int = 60) -> str:
    """Load a page in headless Chromium (for JavaScript-rendered job boards)."""
    return _submit(url, "__fetch__", timeout)


def html_to_pdf(html: str, margin: str = "0.5in", timeout: int = 90) -> bytes:
    key = hashlib.sha256(("v2|" + margin + html).encode("utf-8")).hexdigest()  # bump when rendering changes
    path = os.path.join(_CACHE, key + ".pdf")
    if os.path.isfile(path):
        with open(path, "rb") as fh:
            return fh.read()
    data = _submit(html, margin, timeout)
    with open(path, "wb") as fh:
        fh.write(data)
    return data
