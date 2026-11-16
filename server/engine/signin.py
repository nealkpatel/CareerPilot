"""DeepSeek sign-in: each user signs in to their own DeepSeek account in a normal Chrome window on the server.

When a user clicks Connect DeepSeek, this opens a plain, visible Chrome window on the CareerPilot server computer,
using that user's own profile. Nothing is attached to it: no DevTools port, no Playwright. DeepSeek's captcha
(hCaptcha) rejects browsers that are being driven or inspected, even when a person solves it correctly, so the window
has to be an ordinary one. The user sees that window live in CareerPilot and works in it from their own browser:
pixels are read with PrintWindow and their clicks, drags and typing are replayed as real mouse and keyboard input
(see winremote.py). When the user clicks Done, or the window is closed, Chrome exits normally (saving the sign-in in
their profile), and their engine opens the profile once to confirm the sign-in worked.

Every call is keyed by the signed-in user's id, so nobody can open or finish another user's sign-in.
"""
import atexit
import logging
import os
import queue
import subprocess
import threading
import time
from concurrent.futures import Future

from ..config import DEEPSEEK_URL, LOGIN_WAIT_TIMEOUT, user_browser_profile
from . import winremote
from .deepseek_client import _system_chrome_path

logger = logging.getLogger(__name__)


class SignInError(RuntimeError):
    pass


class SignIns:
    def __init__(self, engines):
        self.engines = engines
        self.q = queue.Queue()
        self._sessions = {}   # user id -> {"proc", "started", "remote"}
        self._thread = threading.Thread(target=self._loop, daemon=True, name="deepseek-signin")
        atexit.register(self._kill_all)

    def start(self):
        self._thread.start()

    # ------------------------------------------------------------ any thread
    def call(self, fn, *args, timeout=20):
        fut = Future()
        self.q.put((fn, args, fut))
        return fut.result(timeout=timeout)

    def open(self, uid):
        """Open (or reuse) this user's sign-in window. Returns their session status."""
        if self.active(uid):
            return self.call("_status", uid)
        if not _system_chrome_path():
            raise SignInError("Signing in needs Google Chrome or Microsoft Edge installed on the CareerPilot server.")
        self.engines.begin_signin(uid)  # closes the user's automated browser: one Chrome per profile at a time
        try:
            return self.call("_open", uid, timeout=60)
        except Exception:
            self.engines.end_signin(uid, False)
            raise

    def active(self, uid):
        s = self._sessions.get(uid)
        return bool(s) and s["proc"].poll() is None

    def status(self, uid):
        if uid not in self._sessions:
            return {"active": False, "signed_in": False}
        return self.call("_status", uid)

    def finish(self, uid):
        """Done: close the window (Chrome saves the sign-in) and let the engine check whether it worked."""
        return self.call("_finish", uid, True, timeout=60)

    def cancel(self, uid):
        return self.call("_finish", uid, False, timeout=60)

    # ---- live view: served straight from request threads (the worker thread only opens, closes and sweeps)
    def _live(self, uid):
        s = self._sessions.get(uid)
        if not s or s["proc"].poll() is not None:
            raise LookupError("No DeepSeek sign-in window is open.")
        return s["remote"]

    def frame(self, uid, last_hash=None):
        """Latest picture of the user's sign-in window: (bytes or None if unchanged, hash, width, height, media type,
        whether the server's screen is locked)."""
        try:
            return self._live(uid).frame(last_hash)
        except winremote.RemoteError as exc:
            raise SignInError(str(exc)) from exc

    def act(self, uid, actions):
        """Replay the user's clicks, drags, wheel and typing in their window. Returns whether it had keyboard focus."""
        try:
            return self._live(uid).act(actions)
        except winremote.RemoteError as exc:
            raise SignInError(str(exc)) from exc

    # ------------------------------------------------------------ sign-in thread
    def _loop(self):
        last_sweep = 0.0
        while True:
            # Sweep on a clock, not only when idle: the dialog's own status polling must not starve it.
            if time.time() - last_sweep >= 1.5:
                self._sweep()
                last_sweep = time.time()
            try:
                fn, args, fut = self.q.get(timeout=1)
            except queue.Empty:
                continue
            try:
                fut.set_result(getattr(self, fn)(*args))
            except Exception as exc:
                logger.debug("sign-in %s failed: %s", fn, exc)
                fut.set_exception(exc)

    def _sweep(self):
        """Notice a window the user closed, or one left open too long."""
        now = time.time()
        for uid, s in list(self._sessions.items()):
            try:
                if s["proc"].poll() is not None:
                    self._finish(uid, True)   # the user closed Chrome: check whether the sign-in worked
                elif now - s["started"] > LOGIN_WAIT_TIMEOUT:
                    self._finish(uid, True)
            except Exception as exc:
                logger.debug("sign-in sweep: %s", exc)

    def _open(self, uid):
        profile = user_browser_profile(uid)
        os.makedirs(profile, exist_ok=True)
        step = 36 * (len(self._sessions) % 6)   # several users at once: cascade the windows instead of stacking them
        proc = subprocess.Popen([
            _system_chrome_path(),
            f"--user-data-dir={profile}",
            "--no-first-run", "--no-default-browser-check", "--disable-session-crashed-bubble", "--hide-crash-restore-bubble",
            f"--window-position={40 + step},{20 + step}", "--window-size=1000,720",
            "--new-window", DEEPSEEK_URL.rstrip("/") + "/sign_in",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        hwnd = winremote.find_window(proc.pid, timeout=25)
        if proc.poll() is not None or not hwnd:
            # Chrome handed the window to another Chrome already running on this profile, or failed to start.
            if proc.poll() is None:
                proc.kill()
            raise SignInError("Chrome did not start for the DeepSeek sign-in. Close any Chrome window using this profile and try again.")
        self._sessions[uid] = {"proc": proc, "started": time.time(), "remote": winremote.Remote(hwnd)}
        logger.info("DeepSeek sign-in window opened for user %s", uid)
        return self._status(uid)

    def _status(self, uid):
        s = self._sessions.get(uid)
        if not s:
            return {"active": False, "signed_in": False}
        # Stays active until the sweep hands a closed window to the engine's check, so callers never see a gap.
        return {"active": True, "signed_in": False,
                "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(s["started"]))}

    def _finish(self, uid, keep):
        s = self._sessions.get(uid)
        if not s:
            return {"active": False, "signed_in": False, "verifying": False}
        self._close(s)
        self._sessions.pop(uid, None)
        # keep: the engine opens the saved profile once to find out whether the sign-in worked.
        self.engines.end_signin(uid, None if keep else False)
        logger.info("DeepSeek sign-in window for user %s closed (%s)", uid, "checking" if keep else "cancelled")
        return {"active": False, "signed_in": False, "verifying": keep}

    @staticmethod
    def _close(s):
        """Close Chrome the normal way (like clicking X) so it writes the sign-in cookies to disk; force it only if it hangs."""
        proc = s["proc"]
        if proc.poll() is not None:
            return
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(proc.pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            else:
                proc.terminate()
        except Exception:
            pass
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()

    def _kill_all(self):
        for s in list(self._sessions.values()):
            try:
                s["proc"].kill()
            except Exception:
                pass
