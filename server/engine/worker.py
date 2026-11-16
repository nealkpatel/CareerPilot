"""Per-user DeepSeek engines.

Every user connects their own DeepSeek account. Each user gets an engine: a background thread that owns that user's
browser profile (their DeepSeek sign-in) and works through that user's queue. Playwright's sync API is bound to the
thread that started it, so every browser call (prompts, login checks) happens on the owner's engine thread. Web
requests only enqueue tasks and read state.

Users' engines run side by side, so one user's queue never waits for another's.
"""

import collections
import os
import logging
import queue
import re
import shutil
import threading
import time
import traceback

from .. import db
from ..config import ENGINE_IDLE_SECONDS, user_browser_profile
from ..render.documents import normalize_resume, validate_resume, resume_text, SAMPLE_RESUME
from . import prompts as P
from .text_clean import strip_scraped_ui_chrome

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 3
OFFLINE_RETRY_SECONDS = int(os.environ.get("CAREERPILOT_ENGINE_RETRY", "120"))
WORK_KINDS = ("generate", "answer")

CONNECT_HINT = "Open Settings → My DeepSeek account and click Connect DeepSeek to sign in."


class EngineUnavailable(RuntimeError):
    """This user's DeepSeek can't be used right now (not signed in, browser failed, network down). Work is held, not failed."""

    def __init__(self, reason, message):
        super().__init__(message)
        self.reason = reason  # "login" | "offline"


def is_connected(uid):
    return db.get_user_setting(uid, "deepseek_connected", "0") == "1"


def _set_connected(uid, connected):
    db.set_user_setting(uid, "deepseek_connected", "1" if connected else "0")
    if connected:
        db.set_user_setting(uid, "deepseek_connected_at", db.now_iso())


class UserEngine:
    """One user's DeepSeek session and queue."""

    def __init__(self, manager, user_id):
        self.manager = manager
        self.user_id = user_id
        self.profile_dir = user_browser_profile(user_id)
        self.tasks = queue.Queue()
        connected = is_connected(user_id)
        self.state = {
            "status": "idle" if connected else "not_connected",
            "message": "Ready. Your DeepSeek opens automatically when you generate." if connected
                       else "Your DeepSeek account isn't connected yet. " + CONNECT_HINT,
            "current": None,
            "browser_open": False,
            "last_ok": db.get_user_setting(user_id, "engine_last_ok", ""),
            "blocked_since": None,
            "blocked_reason": None,
        }
        self._held = []                # this user's work waiting for their DeepSeek to come back
        self._paused = False
        self._retry_at = None
        self.signing_in = False        # the sign-in browser owns the profile; never open it here meanwhile
        self.verifying = None          # after a sign-in window closed: was the user connected before? (None = not verifying)
        self.log = collections.deque(maxlen=200)
        self._client = None
        self._last_used = 0.0
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"deepseek-engine-u{user_id}")
        self._thread.start()

    # ----------------------------------------------------------- called from any thread
    def put(self, kind, ref=None):
        self.tasks.put((kind, ref))

    def close_browser(self, wait=30):
        """Close this user's automated browser (needed before the sign-in window opens on the same profile)."""
        done = threading.Event()
        self.tasks.put(("close", done))
        return done.wait(wait)

    def snapshot(self):
        with self._lock:
            data = dict(self.state)
        data["queue"] = self.tasks.qsize() + len(self._held)
        data["held"] = len(self._held)
        data["paused"] = self._paused
        data["verifying"] = self.verifying is not None
        data["log"] = list(self.log)[-60:]
        return data

    # ----------------------------------------------------------- internals
    def _set(self, **kw):
        with self._lock:
            self.state.update(kw)
            st = self.state["status"]
            if st in ("login_required", "not_connected", "offline", "error"):
                if not self.state.get("blocked_since"):
                    self.state["blocked_since"] = db.now_iso()
                self.state["blocked_reason"] = {"login_required": "login", "not_connected": "login", "offline": "offline"}.get(st, "error")
            elif st == "ready" or (st == "idle" and not self._paused):
                self.state["blocked_since"] = None
                self.state["blocked_reason"] = None

    def _log(self, text):
        stamp = time.strftime("%H:%M:%S")
        self.log.append(f"{stamp}  {text}")
        logger.info("[user %s] %s", self.user_id, text)

    def _hold(self, kind, ref):
        if (kind, ref) not in self._held:
            self._held.append((kind, ref))
        msg = "Waiting for your DeepSeek account to connect"
        if kind == "generate":
            row = db.query_one("SELECT status FROM generations WHERE id = ?", (ref,))
            if row and row["status"] in ("queued", "generating"):
                db.update("generations", ref, {"status": "queued", "progress": 0, "message": msg})
        else:
            db.execute("UPDATE answer_requests SET status = 'queued' WHERE id = ? AND status IN ('queued','running')", (ref,))

    def _release(self, why):
        if not self._held and not self._paused:
            return
        held, self._held = self._held, []
        self._paused, self._retry_at = False, None
        if held:
            self._log(f"{why} Resuming {len(held)} waiting request(s).")
        for item in held:
            self.tasks.put(item)

    def _pause(self, exc):
        self._paused = True
        self._shutdown()
        waiting = len(self._held)
        if exc.reason == "offline":
            self._retry_at = time.time() + OFFLINE_RETRY_SECONDS
            self._set(status="offline", current=None,
                      message=f"DeepSeek could not be reached ({exc}). {waiting} request(s) are waiting; retrying automatically every "
                              f"{max(1, OFFLINE_RETRY_SECONDS // 60)} minutes.")
        else:
            self._retry_at = None
            connected = is_connected(self.user_id)
            self._set(status="login_required" if connected else "not_connected", current=None,
                      message=(f"Your DeepSeek sign-in has expired. " if connected else "Your DeepSeek account isn't connected yet. ")
                              + f"{waiting} request(s) are waiting and run automatically once you connect. " + CONNECT_HINT)
        self._log(f"DeepSeek unavailable ({exc.reason}): {exc}. Holding {waiting} request(s).")

    def _run(self):
        while True:
            try:
                kind, ref = self.tasks.get(timeout=5)
            except queue.Empty:
                if self._paused and self._retry_at and time.time() >= self._retry_at:
                    self._release("Retrying the connection.")
                    continue
                if self._client and time.time() - self._last_used > ENGINE_IDLE_SECONDS:
                    self._log("Closing your DeepSeek browser after inactivity.")
                    self._shutdown()
                continue
            if kind in WORK_KINDS and (self._paused or self.signing_in):
                self._hold(kind, ref)
                if self.signing_in and not self._paused:
                    self._set(status="login_required", message="Finish signing in to DeepSeek — your requests run right after.")
                continue
            try:
                if kind == "generate":
                    self._do_generate(ref)
                elif kind == "answer":
                    try:
                        self._do_answers(ref)
                    except EngineUnavailable:
                        raise
                    except Exception:
                        db.execute("UPDATE answer_requests SET status = 'failed', error = ?, finished_at = ? WHERE id = ? AND status IN ('queued','running')",
                                   ("Could not write these answers. Try again.", db.now_iso(), ref))
                        raise
                elif kind == "check":
                    # After a sign-in: open the session once to confirm it, then run anything that was waiting.
                    self._paused = False
                    try:
                        self._ensure_client()
                    except EngineUnavailable as exc:
                        if self.verifying is not None and exc.reason == "login":
                            # The window was closed without a working sign-in: put the connection back as it was.
                            if not self.verifying:
                                _set_connected(self.user_id, False)
                            self._log("DeepSeek sign-in was not finished.")
                        raise
                    finally:
                        self.verifying = None
                elif kind == "close":
                    self._shutdown()
                    if ref is not None:
                        ref.set()
                elif kind == "forget":
                    self._shutdown()
                    self._forget_profile()
                    if ref is not None:
                        ref.set()
            except EngineUnavailable as exc:
                if kind in WORK_KINDS:
                    self._hold(kind, ref)
                self._pause(exc)
            except Exception as exc:
                self._log(f"Engine error: {exc}")
                logger.error(traceback.format_exc())
                self._set(status="error", message=str(exc), current=None)
                self._shutdown()
            finally:
                self._last_used = time.time()
                if self.state["status"] == "busy":
                    self._set(status="ready" if self._client else "idle", message="Ready for the next request.", current=None)

    def _shutdown(self):
        if self._client:
            try:
                self._client.stop()
            except Exception:
                pass
        self._client = None
        st = self.state["status"]
        keep = st in ("error", "login_required", "not_connected", "offline")
        self._set(browser_open=False, status=st if keep else "idle",
                  message=self.state["message"] if keep else "Ready. Your DeepSeek opens automatically when you generate.")

    def _forget_profile(self):
        """Disconnect: delete this user's saved DeepSeek sign-in."""
        shutil.rmtree(self.profile_dir, ignore_errors=True)
        _set_connected(self.user_id, False)
        self._paused, self._retry_at = False, None
        self._set(status="not_connected", current=None, message="Your DeepSeek account is disconnected. " + CONNECT_HINT)
        self._log("DeepSeek account disconnected; the saved sign-in was deleted.")

    def _ensure_client(self):
        from .deepseek_client import DeepSeekClient

        if self.signing_in:
            raise EngineUnavailable("login", "you are signing in to DeepSeek right now")
        if not is_connected(self.user_id):
            raise EngineUnavailable("login", "no DeepSeek account is connected")
        if self._client is None:
            self._set(status="starting", message="Opening your DeepSeek…")
            self._log("Starting your DeepSeek browser session.")
            client = DeepSeekClient(self.profile_dir)
            try:
                client.start()
            except Exception as exc:
                client.stop()
                raise EngineUnavailable("offline", f"the browser could not start: {exc}") from exc
            self._client = client
            self._set(browser_open=True)
        if not self._client.is_logged_in(10000):
            # The saved sign-in expired. The user signs in again with Connect DeepSeek.
            raise EngineUnavailable("login", "DeepSeek asked to sign in again")
        self._ok("Your DeepSeek is signed in and ready.")
        return self._client

    def _ok(self, msg):
        if self.state.get("status") != "ready":
            self._log(msg)
        stamp = db.now_iso()
        db.set_user_setting(self.user_id, "engine_last_ok", stamp)
        self._set(status="ready", message=msg, last_ok=stamp)
        self._release("Your DeepSeek is connected.")

    def _send(self, prompt, on_progress, expected):
        if self.manager.mode() == "demo":
            return None
        client = self._ensure_client()
        self._set(status="busy")
        last = None
        for attempt in range(1, 4):
            try:
                return client.send_prompt(prompt, new_chat=True, on_progress=on_progress, expected_seconds=expected)
            except Exception as exc:
                last = exc
                kind = getattr(exc, "kind", "error")
                if kind == "offline" and attempt == 3:
                    raise EngineUnavailable("offline", str(exc)) from exc
                if kind not in ("offline", "busy", "timeout") or attempt == 3:
                    raise
                self._log(f"DeepSeek {kind}: {exc}. Retrying in {10 * attempt}s…")
                time.sleep(10 * attempt)
        raise last

    # ----------------------------------------------------------- generation
    def _gen_update(self, gen_id, **kw):
        db.update("generations", gen_id, kw)

    def _do_generate(self, gen_id):
        from .deepseek_client import parse_response

        gen = db.query_one("SELECT * FROM generations WHERE id = ?", (gen_id,))
        if not gen or gen["status"] not in ("queued", "generating"):
            return
        app = db.query_one("SELECT * FROM applications WHERE id = ?", (gen["application_id"],))
        if not app:
            return
        profile = db.jload(gen["profile_json"], {})
        prompt = db.query_one("SELECT * FROM prompts WHERE id = ?", (gen["prompt_id"],)) if gen["prompt_id"] else None
        role_mode = db.get_user_setting(gen["user_id"], "role_mode", "fixed")
        label = f"{app['company']} — {app['title']}"
        self._set(status="busy", current={"type": "generate", "id": gen_id, "label": label})
        self._gen_update(gen_id, status="generating", progress=3, message="Preparing prompt", error="")
        self._log(f"Generating resume for {label}")

        text = P.build_resume_prompt(prompt["text"] if prompt else P.DEFAULT_PROMPT, app, profile, role_mode)
        last_pct = [0]

        def on_progress(frac):
            pct = round(5 + 85 * float(frac or 0))
            if pct - last_pct[0] >= 2:
                last_pct[0] = pct
                self._gen_update(gen_id, progress=pct, message="DeepSeek is writing your résumé")

        resume, letter, raw, problems = None, "", "", []
        for attempt in range(1, MAX_ATTEMPTS + 1):
            current = db.query_one("SELECT status FROM generations WHERE id = ?", (gen_id,))
            if not current or current["status"] == "cancelled":
                self._log(f"Generation #{gen_id} was cancelled.")
                return
            self._gen_update(gen_id, message=f"Waiting for DeepSeek (attempt {attempt}/{MAX_ATTEMPTS})")
            raw = self._send(text, on_progress, expected=90)
            if raw is None:  # demo mode
                raw, resume, letter = "", self._demo_resume(app, profile), self._demo_letter(app, profile)
                time.sleep(2)
                break
            json_str, letter = parse_response(raw)
            if not json_str:
                problems = ["No resume JSON was found in the reply."]
                self._log(f"Attempt {attempt}: no JSON in reply, retrying in a new chat.")
                continue
            try:
                resume = normalize_resume(json_str)
            except Exception as exc:
                problems = [f"Resume JSON was invalid: {exc}"]
                self._log(f"Attempt {attempt}: invalid JSON ({exc}), retrying.")
                resume = None
                continue
            problems = validate_resume(resume, profile)
            if problems and attempt < MAX_ATTEMPTS:
                self._log(f"Attempt {attempt}: {'; '.join(problems)} Retrying.")
                continue
            break

        if resume is None:
            self._gen_update(gen_id, status="failed", progress=0, raw_response=raw[:200000],
                             error=" ".join(problems) or "DeepSeek did not return a usable résumé.",
                             message="Failed", finished_at=db.now_iso())
            self._log(f"Generation #{gen_id} failed: {' '.join(problems)}")
            return

        letter = strip_scraped_ui_chrome(letter or "")
        self._gen_update(
            gen_id, status="completed", progress=100, message="Completed" + (" with warnings" if problems else ""),
            error=("Warnings: " + " ".join(problems)) if problems else "",
            resume_json=db.jdump(resume), cover_letter=letter, raw_response=raw[:200000], finished_at=db.now_iso(),
        )
        if app["status"] == "saved":
            db.update("applications", app["id"], {"status": "ready", "updated_at": db.now_iso()})
            db.insert("status_history", {"application_id": app["id"], "user_id": gen["user_id"], "status": "ready",
                                         "note": "Resume and cover letter generated", "created_at": db.now_iso()})
        self._log(f"Resume ready for {label}")
        db.audit(gen["user_id"], "generation.completed", f"#{gen_id} {label}")

        if (app["questions"] or "").strip():
            rid = db.insert("answer_requests", {"generation_id": gen_id, "user_id": gen["user_id"], "engine_user_id": self.user_id,
                                                "questions": app["questions"], "status": "queued", "created_at": db.now_iso()})
            self._do_answers(rid)

    def _do_answers(self, req_id):
        req = db.query_one("SELECT * FROM answer_requests WHERE id = ?", (req_id,))
        if not req or req["status"] not in ("queued", "running"):
            return
        gen = db.query_one("SELECT * FROM generations WHERE id = ?", (req["generation_id"],))
        app = db.query_one("SELECT * FROM applications WHERE id = ?", (gen["application_id"],)) if gen else None
        if not gen or not app:
            db.update("answer_requests", req_id, {"status": "failed", "error": "Resume version no longer exists."})
            return
        profile = db.jload(gen["profile_json"], {})
        prompt = db.query_one("SELECT * FROM prompts WHERE id = ?", (gen["prompt_id"],)) if gen["prompt_id"] else None
        questions = P.split_questions(req["questions"])
        if not questions:
            db.update("answer_requests", req_id, {"status": "failed", "error": "No questions were provided."})
            return
        role_mode = db.get_user_setting(gen["user_id"], "role_mode", "fixed")
        self._set(status="busy", current={"type": "answers", "id": req_id, "label": f"Answers · {app['company']}"})
        db.update("answer_requests", req_id, {"status": "running"})
        self._log(f"Answering {len(questions)} question(s) for {app['company']}")
        numbered = "\n".join(f"{i}. {q}" for i, q in enumerate(questions, 1))
        text = P.build_answer_prompt(
            prompt["question_text"] if prompt and prompt["question_text"].strip() else P.DEFAULT_QUESTION_PROMPT,
            app, profile, resume_text(gen["resume_json"], profile, role_mode), gen["cover_letter"], numbered,
        )
        try:
            raw = self._send(text, None, expected=60)
            if raw is None:
                answers = [self._demo_answer(q, app, profile) for q in questions]
            else:
                answers = P.split_answers(strip_scraped_ui_chrome(raw), len(questions))
        except EngineUnavailable:
            raise
        except Exception as exc:
            db.update("answer_requests", req_id, {"status": "failed", "error": str(exc), "finished_at": db.now_iso()})
            self._log(f"Answers failed: {exc}")
            return
        existing = db.jload(gen["answers_json"], [])
        for q, a in zip(questions, answers):
            existing.append({"q": q, "a": a.strip()})
        db.update("generations", gen["id"], {"answers_json": db.jdump(existing)})
        db.update("answer_requests", req_id, {"status": "completed", "finished_at": db.now_iso()})
        self._log(f"Answers saved for {app['company']}")

    # ----------------------------------------------------------- demo mode
    @staticmethod
    def _keywords(text):
        known = ["Python", "Java", "JavaScript", "TypeScript", "Go", "C#", ".NET", "React", "Angular", "Vue", "Node.js",
                 "Django", "FastAPI", "Spring Boot", "AWS", "Azure", "GCP", "Docker", "Kubernetes", "Terraform",
                 "PostgreSQL", "MySQL", "MongoDB", "Redis", "Kafka", "GraphQL", "REST", "CI/CD", "Microservices",
                 "SQL", "Snowflake", "Spark", "Airflow", "Machine Learning", "LLM", "Salesforce", "Figma"]
        found = [k for k in known if re.search(r"(?<![A-Za-z])" + re.escape(k) + r"(?![A-Za-z])", text or "", re.I)]
        return found or ["Python", "TypeScript", "React", "AWS", "PostgreSQL", "Docker"]

    def _demo_resume(self, app, profile):
        kw = self._keywords(app.get("description"))
        stack = ", ".join(kw[:4])
        exps = []
        for i, job in enumerate(profile.get("jobs") or [{}]):
            n = max(4, 7 - i)
            exps.append({"role": job.get("role") or app["title"], "bullets": [
                f"Delivered {['customer-facing', 'internal', 'data', 'platform'][j % 4]} features at {job.get('company') or 'the company'} using {kw[j % len(kw)]}, partnering closely with product and design to ship on schedule."
                for j in range(n)]})
        return {
            "summary": f"{profile.get('headline') or app['title']} with a track record of building reliable, scalable products. Experienced with {stack}. Known for clear communication and ownership from design through production. (Demo mode output — real tailoring is available when the platform runs in live mode.)",
            "skills": [{"Core stack": ", ".join(kw)}, {"Practices": "Code review, testing, CI/CD, agile delivery, observability"}],
            "experiences": exps or SAMPLE_RESUME["experiences"],
        }

    @staticmethod
    def _demo_letter(app, profile):
        return (f"Dear Hiring Manager,\n\nI am excited to apply for the {app['title']} position at {app['company']}. "
                "My background maps closely to the requirements you described, and I would welcome the chance to contribute.\n\n"
                "This letter was produced in demo mode. A fully tailored letter is written when the platform runs in live mode.\n\n"
                f"Sincerely,\n{profile.get('name') or ''}")

    @staticmethod
    def _demo_answer(q, app, profile):
        return f"(Demo answer) Thank you for the question. For the {app['title']} role at {app['company']}, I would draw on my recent experience to address: {q}"


class EngineManager:
    """Routes work to the requesting user's engine. Engines start lazily, the first time a user needs one."""

    def __init__(self):
        self._engines = {}
        self._lock = threading.Lock()

    def mode(self):
        return db.get_setting("engine_mode", "deepseek")

    def for_user(self, uid):
        uid = int(uid)
        with self._lock:
            eng = self._engines.get(uid)
            if eng is None:
                eng = self._engines[uid] = UserEngine(self, uid)
            return eng

    def start(self):
        """Requeue work that was waiting when the server stopped, each on its requester's engine."""
        for row in db.query("""SELECT id, COALESCE(engine_user_id, user_id) AS uid FROM generations
                               WHERE status IN ('queued', 'generating') AND kind = 'ai' ORDER BY id"""):
            db.update("generations", row["id"], {"status": "queued", "progress": 0, "message": "Waiting in queue"})
            self.for_user(row["uid"]).put("generate", row["id"])
        for row in db.query("""SELECT id, COALESCE(engine_user_id, user_id) AS uid FROM answer_requests
                               WHERE status IN ('queued', 'running') ORDER BY id"""):
            db.update("answer_requests", row["id"], {"status": "queued"})
            self.for_user(row["uid"]).put("answer", row["id"])

    def enqueue_generation(self, gen_id):
        row = db.query_one("SELECT COALESCE(engine_user_id, user_id) AS uid FROM generations WHERE id = ?", (gen_id,))
        if row:
            eng = self.for_user(row["uid"])
            eng.put("generate", gen_id)
            eng._log(f"Queued resume generation #{gen_id}")

    def enqueue_answers(self, req_id):
        row = db.query_one("SELECT COALESCE(engine_user_id, user_id) AS uid FROM answer_requests WHERE id = ?", (req_id,))
        if row:
            eng = self.for_user(row["uid"])
            eng.put("answer", req_id)
            eng._log(f"Queued application answers #{req_id}")

    def snapshot(self, uid):
        """One user's engine state. Doesn't start an engine thread for a user who never used one."""
        with self._lock:
            eng = self._engines.get(int(uid))
        if eng:
            data = eng.snapshot()
        else:
            connected = is_connected(uid)
            data = {"status": "idle" if connected else "not_connected",
                    "message": "Ready. Your DeepSeek opens automatically when you generate." if connected
                               else "Your DeepSeek account isn't connected yet. " + CONNECT_HINT,
                    "current": None, "browser_open": False, "last_ok": db.get_user_setting(uid, "engine_last_ok", ""),
                    "blocked_since": None, "blocked_reason": None, "queue": 0, "held": 0, "paused": False, "verifying": False, "log": []}
        data["mode"] = self.mode()
        data["connected"] = is_connected(uid)
        data["connected_at"] = db.get_user_setting(uid, "deepseek_connected_at", "")
        return data

    def overview(self):
        """Master: every bidding user's connection and queue state."""
        rows = db.query("SELECT id, name, email, role, status FROM users WHERE role IN ('admin', 'bidder') ORDER BY role, name COLLATE NOCASE")
        out = []
        for r in rows:
            s = self.snapshot(r["id"])
            out.append({"id": r["id"], "name": r["name"], "email": r["email"], "role": r["role"], "account_status": r["status"],
                        "connected": s["connected"], "connected_at": s["connected_at"], "status": s["status"],
                        "queue": s["queue"], "last_ok": s["last_ok"]})
        return out

    # ------------------------------------------------- sign-in hand-off (see signin.py)
    def begin_signin(self, uid):
        eng = self.for_user(uid)
        eng.signing_in = True
        if eng._client is not None and not eng.close_browser(wait=60):
            eng.signing_in = False
            raise RuntimeError("Your DeepSeek is busy with a request. Try connecting again in a minute.")

    def end_signin(self, uid, signed_in):
        """signed_in: True / False, or None = unknown (the window was closed): open the saved session once to find out."""
        eng = self.for_user(uid)
        eng.signing_in = False
        if signed_in is None:
            eng.verifying = is_connected(uid)
            _set_connected(uid, True)
            eng._set(status="starting", message="Checking your DeepSeek sign-in…")
            eng._log("Sign-in window closed; checking the DeepSeek sign-in.")
            eng.put("check")
        elif signed_in:
            _set_connected(uid, True)
            eng._log("Signed in to DeepSeek.")
            eng.put("check")
        else:
            eng._set(status="login_required" if is_connected(uid) else "not_connected",
                     message="DeepSeek sign-in was not finished. " + CONNECT_HINT)
            if eng._held:
                eng._paused = True

    def disconnect(self, uid):
        eng = self.for_user(uid)
        done = threading.Event()
        eng.put("forget", done)
        if not done.wait(60):
            raise RuntimeError("Your DeepSeek is busy with a request. Try again in a minute.")

    def request_close(self, uid):
        self.for_user(uid).put("close")


engine = EngineManager()
