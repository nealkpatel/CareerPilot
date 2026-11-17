"""CareerPilot web server: REST API + static single-page app."""

import logging
import os
import re
import secrets
import io
import threading
import zipfile
from datetime import datetime, timedelta, timezone

from fastapi import Body, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import auth, db, warnings
from .auth import admin_user, current_user, master_user, worker_user
from .config import GOOGLE_SITE_VERIFICATION, SHARE_DAYS, WEB_DIR, PUBLIC_URL
from .engine import prompts as P
from .engine.worker import engine
from .engine.signin import SignInError, SignIns

signins = SignIns(engine)
from .render import documents as D
from .render import docx as X
from .scraping import job_pool
from .scraping.job_url import scrape_job, url_key

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = FastAPI(title="CareerPilot", docs_url="/api/docs", openapi_url="/api/openapi.json")

APP_STATUSES = ["saved", "ready", "applied", "screening", "interview", "offer", "rejected", "withdrawn"]


@app.on_event("startup")
def _startup():
    db.connect()
    job_pool.seed_sources()
    from . import logos  # noqa: F401  (applies logo-cache upgrades at startup)
    engine.start()
    signins.start()
    job_pool.start_scheduler()


@app.middleware("http")
async def _no_stale_assets(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/assets/fonts/"):
        # fonts are requested CORS-mode by the PDF renderer (its page has no origin of its own)
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Cache-Control"] = "public, max-age=604800"
    elif request.url.path.startswith("/assets/"):
        response.headers["Cache-Control"] = "no-cache"
    elif request.url.path.startswith("/api/"):
        # never let a CDN or the Netlify proxy cache an API answer
        response.headers.setdefault("Cache-Control", "no-store")
    if request.url.path.startswith(("/api/", "/r/")):
        response.headers.setdefault("X-Robots-Tag", "noindex")   # only the front page is for search engines
    return response


@app.exception_handler(ValueError)
def _value_error(_req, exc):
    return JSONResponse({"detail": str(exc)}, status_code=400)


def _now():
    return db.now_iso()


def _need(cond, code=404, msg="Not found."):
    if not cond:
        raise HTTPException(code, msg)


def _s(v, n=20000):
    return str(v or "").strip()[:n]


# ====================================================================== auth

@app.get("/api/auth/state")
def auth_state(request: Request):
    has_users = db.query_one("SELECT COUNT(*) AS n FROM users")["n"] > 0
    user = None
    try:
        user = auth.public_user(current_user(request))
    except HTTPException:
        pass
    return {"has_users": has_users, "user": user, "registration": _registration_mode(), "public_url": PUBLIC_URL}


def _seed_user(uid):
    db.insert("prompts", {"user_id": uid, "label": P.DEFAULT_PROMPT_LABEL, "text": P.DEFAULT_PROMPT,
                          "question_text": P.DEFAULT_QUESTION_PROMPT, "is_default": 1, "created_at": _now(), "updated_at": _now()})


def _registration_mode():
    """Sign-ups: "approval" (new accounts stay pending until the master approves them) or "closed".
    There is no open mode any more; a stored "open" from an earlier version counts as "approval"."""
    return "closed" if db.get_setting("registration_mode", "approval") == "closed" else "approval"


@app.post("/api/auth/register")
def register(body: dict = Body(...)):
    name, email, pw = _s(body.get("name"), 120), _s(body.get("email"), 200).lower(), str(body.get("password") or "")
    first = db.query_one("SELECT COUNT(*) AS n FROM users")["n"] == 0
    if not first and _registration_mode() == "closed":
        raise HTTPException(403, "Sign-ups are closed. Ask the platform master or your admin for an account.")
    if not name or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise HTTPException(400, "Enter your name and a valid email address.")
    if len(pw) < 8:
        raise HTTPException(400, "Use a password with at least 8 characters.")
    if db.query_one("SELECT id FROM users WHERE email = ?", (email,)):
        raise HTTPException(409, "An account with this email already exists.")
    status = "active" if first else "pending"   # only the very first account (the master) is active right away
    uid = db.insert("users", {"email": email, "name": name, "password_hash": auth.hash_password(pw),
                              "role": "master" if first else "admin", "status": status, "created_at": _now()})
    if not first:
        _seed_user(uid)
    db.audit(uid, "user.registered", email)
    if status != "active":
        return {"pending": True}
    token = auth.create_session(uid)
    resp = JSONResponse({"user": auth.public_user(db.query_one("SELECT * FROM users WHERE id = ?", (uid,)))})
    auth.set_session_cookie(resp, token)
    return resp


@app.post("/api/auth/login")
def login(body: dict = Body(...)):
    email, pw = _s(body.get("email"), 200).lower(), str(body.get("password") or "")
    u = db.query_one("SELECT * FROM users WHERE email = ?", (email,))
    if not u or not auth.verify_password(pw, u["password_hash"]):
        raise HTTPException(401, "That email and password don't match.")
    if u["status"] == "pending":
        raise HTTPException(403, "Your account is waiting for approval by the platform master.")
    if u["status"] != "active":
        raise HTTPException(403, "This account is suspended.")
    token = auth.create_session(u["id"])
    db.audit(u["id"], "user.login", email)
    resp = JSONResponse({"user": auth.public_user(u)})
    auth.set_session_cookie(resp, token)
    return resp


@app.post("/api/auth/logout")
def logout(request: Request):
    tok = request.cookies.get(auth.COOKIE)
    if tok:
        auth.drop_session(tok)
    resp = JSONResponse({"ok": True})
    auth.clear_session_cookie(resp)
    return resp


@app.put("/api/me")
def update_me(body: dict = Body(...), user=Depends(current_user)):
    data = {}
    if body.get("name"):
        data["name"] = _s(body["name"], 120)
    if body.get("new_password"):
        if not auth.verify_password(str(body.get("current_password") or ""), user["password_hash"]):
            raise HTTPException(400, "Your current password is not correct.")
        if len(body["new_password"]) < 8:
            raise HTTPException(400, "Use a password with at least 8 characters.")
        data["password_hash"] = auth.hash_password(body["new_password"])
    db.update("users", user["id"], data)
    return {"user": auth.public_user(db.query_one("SELECT * FROM users WHERE id = ?", (user["id"],)))}


# ====================================================================== access helpers

def _bidder_ids(admin_id):
    return [r["id"] for r in db.query("SELECT id FROM users WHERE parent_id = ? AND role = 'bidder'", (admin_id,))]


def _can_see(user, owner_id):
    """A user sees their own records; an admin also sees their bidders' records."""
    if owner_id == user["id"]:
        return True
    if user["role"] == "admin":
        owner = db.query_one("SELECT parent_id FROM users WHERE id = ?", (owner_id,))
        return bool(owner) and owner["parent_id"] == user["id"]
    return False


def _user_name(uid):
    r = db.query_one("SELECT name FROM users WHERE id = ?", (uid,)) if uid else None
    return r["name"] if r else ""


# ====================================================================== settings

USER_SETTINGS = {"default_profile_id": "", "default_template_id": D.DEFAULT_TEMPLATE, "role_mode": "fixed", "show_title": "1"}


def user_settings(uid):
    out = {k: db.get_user_setting(uid, k, v) for k, v in USER_SETTINGS.items()}
    out["default_template_id"] = D.resolve_template_id(out["default_template_id"])
    return out


@app.get("/api/settings")
def get_settings(user=Depends(current_user)):
    out = {"user": user_settings(user["id"])}
    if user["role"] == "master":
        out["platform"] = {
            "registration_mode": _registration_mode(),
            "engine_mode": "demo" if engine.mode() == "demo" else "live",
            "jobpool_interval_hours": str(job_pool.interval_hours()),
        }
    return out


@app.put("/api/settings")
def put_settings(body: dict = Body(...), user=Depends(current_user)):
    for k in USER_SETTINGS:
        if k in (body.get("user") or {}):
            db.set_user_setting(user["id"], k, _s(body["user"][k], 100))
    adm = body.get("platform") or {}
    if adm and user["role"] == "master":
        if adm.get("registration_mode") in ("approval", "closed"):
            db.set_setting("registration_mode", adm["registration_mode"])
        if adm.get("engine_mode") in ("live", "demo"):
            db.set_setting("engine_mode", "deepseek" if adm["engine_mode"] == "live" else "demo")
        if "jobpool_interval_hours" in adm:
            db.set_setting("jobpool_interval_hours", str(float(adm["jobpool_interval_hours"] or 0)))
        db.audit(user["id"], "settings.platform", str(adm))
    return get_settings(user)


@app.get("/api/job-filter")
def get_job_filter(user=Depends(master_user)):
    from .scraping import import_filter as F
    return {"filter": F.settings(), "defaults": F.SETTINGS}


@app.put("/api/job-filter")
def put_job_filter(body: dict = Body(...), user=Depends(master_user)):
    from .scraping import import_filter as F
    out = F.save(body or {})
    db.audit(user["id"], "settings.job_filter", ", ".join(f"{k}={'…' if len(v) > 20 else v}" for k, v in out.items()))
    return {"filter": out}


@app.post("/api/job-filter/apply")
def apply_job_filter(user=Depends(master_user)):
    """Remove pool jobs that no longer match the filter."""
    from .scraping import import_filter as F
    removed = F.apply_to_pool()
    db.audit(user["id"], "jobs.filter_applied", str(removed))
    return {"removed": removed, "total": sum(removed.values())}


@app.get("/api/warning-rules")
def get_rules(user=Depends(current_user)):
    return {"rules": warnings.rules()}


@app.put("/api/warning-rules")
def put_rules(body: dict = Body(...), user=Depends(master_user)):
    return {"rules": warnings.save_rules(body.get("rules"))}


# ====================================================================== profiles

def _profile_out(r):
    return {
        "id": r["id"], "label": r["label"], "name": r["name"], "headline": r["headline"],
        "contact": db.jload(r["contact_json"], []), "jobs": db.jload(r["jobs_json"], []),
        "education": db.jload(r["education_json"], []), "certifications": db.jload(r["certifications_json"], []),
        "background": r["background"], "prompt_id": r["prompt_id"], "template_id": r["template_id"],
        "answers": db.jload(r.get("answers_json") or "{}", {}),
        "created_at": r["created_at"], "updated_at": r["updated_at"],
    }


def _own_profile(pid, user):
    """Profile the user owns (admins edit their own profiles)."""
    r = db.query_one("SELECT * FROM profiles WHERE id = ? AND user_id = ?", (pid, user["id"]))
    _need(r, 404, "Profile not found.")
    return r


def _usable_profile(pid, user_id):
    """Profile a bidding user may use: their own, or one their admin assigned to them."""
    return db.query_one(
        """SELECT p.* FROM profiles p WHERE p.id = ? AND (p.user_id = ? OR EXISTS
           (SELECT 1 FROM profile_assignments pa WHERE pa.profile_id = p.id AND pa.user_id = ?))""",
        (pid, user_id, user_id))


def _read_profile(pid, user):
    r = _usable_profile(pid, user["id"])
    _need(r, 404, "Profile not found.")
    return r


def _clean_list(items, keys):
    out = []
    for it in items or []:
        if isinstance(it, dict):
            row = {k: _s(it.get(k), 4000) for k in keys}
            if any(row.values()):
                out.append(row)
    return out


def _profile_data(body):
    name = _s(body.get("name"), 120)
    if not name:
        raise HTTPException(400, "The profile needs the candidate's full name.")
    return {
        "label": _s(body.get("label"), 120) or name,
        "name": name,
        "headline": _s(body.get("headline"), 160),
        "contact_json": db.jdump([_s(c, 200) for c in body.get("contact") or [] if _s(c)]),
        "jobs_json": db.jdump(_clean_list(body.get("jobs"), ("role", "company", "dates", "work_method", "location", "notes"))),
        "education_json": db.jdump(_clean_list(body.get("education"), ("degree", "school", "location", "dates"))),
        "certifications_json": db.jdump(_clean_list(body.get("certifications"), ("name", "issuer", "dates"))),
        "background": _s(body.get("background"), 20000),
        "prompt_id": int(body["prompt_id"]) if str(body.get("prompt_id") or "").isdigit() else None,
        "template_id": _s(body.get("template_id"), 80),
        "updated_at": _now(),
    }


@app.get("/api/profiles")
def list_profiles(user=Depends(worker_user)):
    rows = db.query("""SELECT p.*, (SELECT COUNT(*) FROM applications a WHERE a.profile_id = p.id) AS app_count
                       FROM profiles p WHERE p.user_id = ? OR EXISTS
                       (SELECT 1 FROM profile_assignments pa WHERE pa.profile_id = p.id AND pa.user_id = ?)
                       ORDER BY label COLLATE NOCASE""", (user["id"], user["id"]))
    out = []
    for r in rows:
        item = {**_profile_out(r), "app_count": r["app_count"], "can_edit": r["user_id"] == user["id"]}
        if item["can_edit"]:
            item["assigned_to"] = [a["user_id"] for a in db.query("SELECT user_id FROM profile_assignments WHERE profile_id = ?", (r["id"],))]
        else:
            item["owner_name"] = _user_name(r["user_id"])
        out.append(item)
    return {"profiles": out}


@app.post("/api/profiles")
def create_profile(body: dict = Body(...), user=Depends(admin_user)):
    data = _profile_data(body)
    pid = db.insert("profiles", {**data, "user_id": user["id"], "created_at": _now()})
    if not db.get_user_setting(user["id"], "default_profile_id"):
        db.set_user_setting(user["id"], "default_profile_id", pid)
    db.audit(user["id"], "profile.created", data["label"])
    return {"profile": _profile_out(_own_profile(pid, user))}


@app.get("/api/profiles/{pid}")
def get_profile(pid: int, user=Depends(worker_user)):
    r = _read_profile(pid, user)
    out = {**_profile_out(r), "can_edit": r["user_id"] == user["id"]}
    if out["can_edit"]:
        out["assigned_to"] = [a["user_id"] for a in db.query("SELECT user_id FROM profile_assignments WHERE profile_id = ?", (pid,))]
    else:
        out["owner_name"] = _user_name(r["user_id"])
    return {"profile": out}


@app.put("/api/profiles/{pid}/assignments")
def assign_profile(pid: int, body: dict = Body(...), user=Depends(admin_user)):
    _own_profile(pid, user)
    mine = set(_bidder_ids(user["id"]))
    wanted = {int(x) for x in body.get("user_ids") or [] if str(x).isdigit()}
    if wanted - mine:
        raise HTTPException(400, "You can only assign profiles to your own bidders.")
    db.execute("DELETE FROM profile_assignments WHERE profile_id = ?", (pid,))
    for uid in wanted:
        db.insert("profile_assignments", {"profile_id": pid, "user_id": uid, "created_at": _now()})
    db.audit(user["id"], "profile.assigned", f"#{pid} -> {sorted(wanted)}")
    return {"assigned_to": sorted(wanted)}


@app.put("/api/profiles/{pid}")
def update_profile(pid: int, body: dict = Body(...), user=Depends(admin_user)):
    _own_profile(pid, user)
    db.update("profiles", pid, _profile_data(body))
    return {"profile": _profile_out(_own_profile(pid, user))}


@app.post("/api/profiles/{pid}/duplicate")
def duplicate_profile(pid: int, user=Depends(admin_user)):
    r = _own_profile(pid, user)
    data = {k: r[k] for k in r if k not in ("id",)}
    data.update(label=r["label"] + " (copy)", created_at=_now(), updated_at=_now())
    new = db.insert("profiles", data)
    return {"profile": _profile_out(_own_profile(new, user))}


@app.delete("/api/profiles/{pid}")
def delete_profile(pid: int, user=Depends(admin_user)):
    r = _own_profile(pid, user)
    db.execute("DELETE FROM profiles WHERE id = ?", (pid,))
    db.audit(user["id"], "profile.deleted", r["label"])
    return {"ok": True}


# ====================================================================== prompts

def _prompt_out(r):
    return {k: r[k] for k in ("id", "label", "text", "question_text", "is_default", "updated_at")}


@app.get("/api/prompts")
def list_prompts(user=Depends(admin_user)):
    rows = db.query("SELECT * FROM prompts WHERE user_id = ? ORDER BY is_default DESC, label COLLATE NOCASE", (user["id"],))
    return {"prompts": [_prompt_out(r) for r in rows], "contract": P.OUTPUT_CONTRACT.strip(),
            "placeholders": ["{job_description}", "{company}", "{job_title}", "{name}", "{headline}", "{work_history}",
                             "{background}", "{education}", "{location}", "{url}"]}


def _prompt_data(body):
    label, text = _s(body.get("label"), 120), _s(body.get("text"), 60000)
    if not label or not text:
        raise HTTPException(400, "Give the prompt a name and some instructions.")
    return {"label": label, "text": text, "question_text": _s(body.get("question_text"), 20000), "updated_at": _now()}


@app.post("/api/prompts")
def create_prompt(body: dict = Body(...), user=Depends(admin_user)):
    pid = db.insert("prompts", {**_prompt_data(body), "user_id": user["id"], "is_default": 0, "created_at": _now()})
    return {"prompt": _prompt_out(db.query_one("SELECT * FROM prompts WHERE id = ?", (pid,)))}


def _own_prompt(pid, user):
    r = db.query_one("SELECT * FROM prompts WHERE id = ? AND user_id = ?", (pid, user["id"]))
    _need(r, 404, "Prompt not found.")
    return r


@app.put("/api/prompts/{pid}")
def update_prompt(pid: int, body: dict = Body(...), user=Depends(admin_user)):
    _own_prompt(pid, user)
    db.update("prompts", pid, _prompt_data(body))
    return {"prompt": _prompt_out(_own_prompt(pid, user))}


@app.post("/api/prompts/{pid}/default")
def default_prompt(pid: int, user=Depends(admin_user)):
    _own_prompt(pid, user)
    db.execute("UPDATE prompts SET is_default = CASE WHEN id = ? THEN 1 ELSE 0 END WHERE user_id = ?", (pid, user["id"]))
    return {"ok": True}


@app.delete("/api/prompts/{pid}")
def delete_prompt(pid: int, user=Depends(admin_user)):
    r = _own_prompt(pid, user)
    if db.query_one("SELECT COUNT(*) AS n FROM prompts WHERE user_id = ?", (user["id"],))["n"] <= 1:
        raise HTTPException(400, "Keep at least one prompt.")
    db.execute("DELETE FROM prompts WHERE id = ?", (pid,))
    db.execute("UPDATE profiles SET prompt_id = NULL WHERE prompt_id = ?", (pid,))
    if r["is_default"]:
        db.execute("UPDATE prompts SET is_default = 1 WHERE id = (SELECT id FROM prompts WHERE user_id = ? ORDER BY id LIMIT 1)", (user["id"],))
    return {"ok": True}


@app.post("/api/prompts/preview")
def preview_prompt(body: dict = Body(...), user=Depends(admin_user)):
    profile = _profile_out(_own_profile(int(body["profile_id"]), user)) if body.get("profile_id") else D.SAMPLE_PROFILE
    app_ = {"company": "Acme Corp", "title": "Senior Software Engineer", "description": "(the job description is inserted here)", "url": "", "location": ""}
    text = P.build_resume_prompt(body.get("text") or P.DEFAULT_PROMPT, app_, profile, db.get_user_setting(user["id"], "role_mode", "fixed"))
    return {"text": text}


# ====================================================================== templates

_HIDDEN_TEMPLATE_KEYS = {"html", "style", "_font_link"}


@app.get("/api/templates")
def list_templates(user=Depends(current_user)):
    return {"templates": [{k: v for k, v in t.items() if k not in _HIDDEN_TEMPLATE_KEYS} for t in D.list_templates(user["id"])],
            "default_id": D.resolve_template_id(db.get_user_setting(user["id"], "default_template_id", D.DEFAULT_TEMPLATE))}


@app.get("/api/templates/options")
def template_options(user=Depends(current_user)):
    from .render import styled
    return styled.options()


def _sample_for(profile_id, user):
    """Sample résumé content, optionally shaped to one of the user's profiles."""
    profile = _profile_out(_read_profile(profile_id, user)) if profile_id else dict(D.SAMPLE_PROFILE)
    resume = dict(D.SAMPLE_RESUME)
    resume["projects"] = [{"name": "OpenTrace", "link": "github.com/jordan/opentrace", "tech": "Go, OpenTelemetry, gRPC", "dates": "2024",
                           "bullets": ["Built a lightweight distributed tracing agent now used by 40 internal teams."]}]
    resume["headline"] = profile.get("headline") or "Senior Software Engineer · Distributed Systems · Cloud Platforms"
    if profile_id:
        resume["experiences"] = [{"role": j.get("role"), "bullets": D.SAMPLE_RESUME["experiences"][min(i, 1)]["bullets"]}
                                 for i, j in enumerate(profile.get("jobs") or [])]
    else:
        profile["jobs"] = [dict(j, location=loc) for j, loc in zip(profile["jobs"], ["Austin, TX", "Remote"])]
        profile["education"] = [dict(e, location="Austin, TX") for e in profile["education"]]
    return resume, profile


@app.get("/api/templates/{tid}/preview", response_class=HTMLResponse)
def preview_template(tid: str, profile_id: int = 0, user=Depends(current_user)):
    resume, profile = _sample_for(profile_id, user)
    return D.render_resume(resume, profile, D.get_template(tid, user["id"]))


@app.post("/api/templates/{tid}/preview", response_class=HTMLResponse)
def preview_template_style(tid: str, body: dict = Body(...), user=Depends(current_user)):
    """Live preview of unsaved style changes."""
    resume, profile = _sample_for(int(body.get("profile_id") or 0), user)
    return D.render_resume(resume, profile, {"kind": "style", "style": body.get("style") or {}})


def _preset_id(tid):
    from .render import styled
    pid = D.resolve_template_id(tid)
    _need(pid in styled.PRESETS, 404, "Only built-in templates have adjustable styles.")
    return pid


@app.get("/api/templates/{tid}/style")
def get_template_style(tid: str, user=Depends(current_user)):
    from .render import styled
    pid = _preset_id(tid)
    style, customised = D.user_style(pid, user["id"])
    return {"id": pid, "label": styled.PRESETS[pid]["label"], "style": style, "preset": styled.preset_style(pid), "customised": customised}


@app.put("/api/templates/{tid}/style")
def save_template_style(tid: str, body: dict = Body(...), user=Depends(current_user)):
    from .render import styled
    pid = _preset_id(tid)
    style = styled.sanitize_style(body.get("style") or {})
    db.execute("""INSERT INTO template_styles (user_id, template_id, style_json, updated_at) VALUES (?, ?, ?, ?)
                  ON CONFLICT(user_id, template_id) DO UPDATE SET style_json = excluded.style_json, updated_at = excluded.updated_at""",
               (user["id"], pid, db.jdump(style), _now()))
    return {"style": style, "customised": True}


@app.delete("/api/templates/{tid}/style")
def reset_template_style(tid: str, user=Depends(current_user)):
    from .render import styled
    pid = _preset_id(tid)
    db.execute("DELETE FROM template_styles WHERE user_id = ? AND template_id = ?", (user["id"], pid))
    return {"style": styled.preset_style(pid), "customised": False}


@app.post("/api/templates")
def upload_template(body: dict = Body(...), user=Depends(worker_user)):
    label, html = _s(body.get("label"), 80), str(body.get("html") or "")
    if not label:
        raise HTTPException(400, "Name the template.")
    errors = D.validate_template_html(html)
    if errors:
        raise HTTPException(400, " ".join(errors))
    tid = f"u{user['id']}_" + (re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_") or "template")
    if db.query_one("SELECT id FROM custom_templates WHERE id = ?", (tid,)):
        tid += "_" + secrets.token_hex(2)
    db.insert("custom_templates", {"id": tid, "user_id": user["id"], "label": label, "html": html, "created_at": _now()})
    return {"id": tid}


@app.delete("/api/templates/{tid}")
def delete_template(tid: str, user=Depends(current_user)):
    cur = db.execute("DELETE FROM custom_templates WHERE id = ? AND user_id = ?", (tid, user["id"]))
    _need(cur.rowcount, 404, "Only your uploaded templates can be deleted.")
    return {"ok": True}


# ====================================================================== scraping

@app.post("/api/scrape")
def scrape(body: dict = Body(...), user=Depends(worker_user)):
    try:
        data = scrape_job(body.get("url"))
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except Exception as exc:
        raise HTTPException(422, f"Could not read that job page: {exc}")
    data["warnings"] = warnings.evaluate(user["id"], data["company"], url_key(data["url"]), data["description"])
    return data


# ====================================================================== applications

def _gen_summary(g):
    out = {k: g[k] for k in ("id", "version", "kind", "status", "progress", "message", "error", "template_id", "created_at", "finished_at")}
    out["template_id"] = D.resolve_template_id(out["template_id"]) if out["template_id"] else out["template_id"]
    return out


def _app_out(a, full=False):
    out = {k: a[k] for k in ("id", "profile_id", "company", "title", "url", "location", "work_arrangement", "salary", "status",
                             "source", "job_id", "applied_at", "created_at", "updated_at")}
    latest = db.query_one("SELECT * FROM generations WHERE application_id = ? ORDER BY id DESC LIMIT 1", (a["id"],))
    out["latest_generation"] = _gen_summary(latest) if latest else None
    prof = db.query_one("SELECT label FROM profiles WHERE id = ?", (a["profile_id"],)) if a["profile_id"] else None
    out["profile_label"] = prof["label"] if prof else ""
    out["owner_id"] = a["user_id"]
    out["owner_name"] = _user_name(a["user_id"])
    ready = db.query_one("""SELECT id, version, cover_letter != '' AS has_cover, answers_json != '[]' AS has_answers, answers_json
                            FROM generations WHERE application_id = ? AND status = 'completed' ORDER BY id DESC LIMIT 1""", (a["id"],))
    out["ready"] = {k: ready[k] for k in ("id", "version", "has_cover", "has_answers")} if ready else None
    out["flags"] = warnings.flags(a["description"])
    out["has_notes"] = bool((a["notes"] or "").strip())
    out["answers_count"] = len(db.jload(ready["answers_json"], [])) if ready else 0
    if full:
        out.update(description=a["description"], questions=a["questions"], notes=a["notes"])
        out["history"] = db.query("SELECT status, note, created_at FROM status_history WHERE application_id = ? ORDER BY id DESC", (a["id"],))
        out["generations"] = [_gen_summary(g) for g in db.query("SELECT * FROM generations WHERE application_id = ? ORDER BY id DESC", (a["id"],))]
        out["warnings"] = warnings.evaluate(a["user_id"], a["company"], a["url_key"], a["description"], exclude_id=a["id"])
    return out


def _own_app(aid, user):
    a = db.query_one("SELECT * FROM applications WHERE id = ?", (aid,))
    _need(a and _can_see(user, a["user_id"]), 404, "Application not found.")
    return a


@app.get("/api/applications")
def list_apps(status: str = "", q: str = "", profile_id: int = 0, scope: str = "mine", owner_id: int = 0,
              date_from: str = "", date_to: str = "", user=Depends(worker_user)):
    owners = [user["id"]]
    if user["role"] == "admin" and scope in ("team", "all"):
        team = _bidder_ids(user["id"])
        owners = team + ([user["id"]] if scope == "all" else [])
        if owner_id:
            owners = [owner_id] if owner_id in owners else []
    owners = owners or [-1]
    marks = ",".join("?" * len(owners))
    base, base_params = f"user_id IN ({marks})", list(owners)
    if re.match(r"^\d{4}-\d{2}-\d{2}$", date_from or ""):
        base += " AND substr(created_at, 1, 10) >= ?"
        base_params.append(date_from)
    if re.match(r"^\d{4}-\d{2}-\d{2}$", date_to or ""):
        base += " AND substr(created_at, 1, 10) <= ?"
        base_params.append(date_to)
    sql, params = f"SELECT * FROM applications WHERE {base}", list(base_params)
    if status:
        sql += " AND status = ?"
        params.append(status)
    if profile_id:
        sql += " AND profile_id = ?"
        params.append(profile_id)
    if q:
        sql += " AND (company LIKE ? OR title LIKE ? OR description LIKE ? OR location LIKE ?)"
        params += [f"%{q}%"] * 4
    rows = db.query(sql + " ORDER BY created_at DESC LIMIT 1000", tuple(params))
    counts = {r["status"]: r["n"] for r in db.query(
        f"SELECT status, COUNT(*) AS n FROM applications WHERE {base} GROUP BY status", tuple(base_params))}
    team = [{"id": r["id"], "name": r["name"]} for r in db.query(
        "SELECT id, name FROM users WHERE parent_id = ? AND role = 'bidder' ORDER BY name", (user["id"],))] if user["role"] == "admin" else []
    return {"applications": [_app_out(r) for r in rows], "counts": counts, "statuses": APP_STATUSES, "team": team}


@app.post("/api/applications/bulk")
def bulk_apps(body: dict = Body(...), user=Depends(worker_user)):
    """Apply one action to many applications: status, generate or delete."""
    ids = [int(x) for x in body.get("ids") or [] if str(x).isdigit()][:500]
    action = body.get("action")
    if not ids or action not in ("status", "generate", "delete"):
        raise HTTPException(400, "Choose applications and an action.")
    if action == "status" and body.get("status") not in APP_STATUSES:
        raise HTTPException(400, "Unknown status.")
    done, skipped = 0, []
    for aid in ids:
        a = db.query_one("SELECT * FROM applications WHERE id = ?", (aid,))
        if not a or not _can_see(user, a["user_id"]):
            skipped.append({"id": aid, "reason": "Not found"})
            continue
        try:
            if action == "delete":
                db.execute("DELETE FROM applications WHERE id = ?", (aid,))
            elif action == "status":
                set_status(aid, {"status": body["status"], "note": "Bulk update"}, user)
            else:
                _start_generation(a, user, {})
            done += 1
        except HTTPException as exc:
            skipped.append({"id": aid, "company": a["company"], "reason": exc.detail})
    db.audit(user["id"], f"application.bulk_{action}", f"{done} done, {len(skipped)} skipped")
    return {"done": done, "skipped": skipped}


@app.put("/api/applications/{aid}/notes")
def save_notes(aid: int, body: dict = Body(...), user=Depends(worker_user)):
    _own_app(aid, user)
    db.update("applications", aid, {"notes": _s(body.get("notes"), 20000), "updated_at": _now()})
    return {"ok": True}


@app.get("/api/applications/export.zip")
def export_zip(ids: str, docs: str = "resume,cover", fmt: str = "pdf", user=Depends(worker_user)):
    """Download the latest finished documents of several applications as one ZIP."""
    if fmt not in ("pdf", "docx", "txt"):
        raise HTTPException(400, "Choose PDF, Word or text.")
    kinds = [k for k in docs.split(",") if k in ("resume", "cover", "answers")] or ["resume"]
    buf, count, used = io.BytesIO(), 0, set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for aid in [int(x) for x in ids.split(",") if x.strip().isdigit()][:200]:
            a = db.query_one("SELECT * FROM applications WHERE id = ?", (aid,))
            if not a or not _can_see(user, a["user_id"]):
                continue
            g = db.query_one("SELECT * FROM generations WHERE application_id = ? AND status = 'completed' ORDER BY id DESC LIMIT 1", (aid,))
            if not g:
                continue
            folder = re.sub(r"[^A-Za-z0-9 ._-]+", "_", f"{a['company']} - {a['title']}").strip()[:90] or f"application-{aid}"
            while folder in used:
                folder += f" ({aid})"
            used.add(folder)
            for kind in kinds:
                if kind == "cover" and not (g["cover_letter"] or "").strip():
                    continue
                if kind == "answers" and db.jload(g["answers_json"], []) == []:
                    continue
                content, _media = _render(g, kind, fmt, user)
                z.writestr(f"{folder}/{_filename(g, kind, fmt)}", content if isinstance(content, bytes) else content.encode("utf-8"))
                count += 1
    if not count:
        raise HTTPException(404, "None of the selected applications have finished documents yet.")
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="CareerPilot-documents-{stamp}.zip"'})


@app.post("/api/applications/check")
def check_app(body: dict = Body(...), user=Depends(worker_user)):
    return {"warnings": warnings.evaluate(user["id"], body.get("company"), url_key(body.get("url")), body.get("description"), body.get("id"))}


def _app_data(body):
    company, title = _s(body.get("company"), 200), _s(body.get("title"), 200)
    if not company or not title:
        raise HTTPException(400, "Company and position are required.")
    return {
        "company": company, "title": title, "url": _s(body.get("url"), 1000), "url_key": url_key(body.get("url")),
        "location": _s(body.get("location"), 200), "work_arrangement": _s(body.get("work_arrangement"), 40),
        "salary": _s(body.get("salary"), 120), "description": _s(body.get("description"), 60000),
        "questions": _s(body.get("questions"), 20000), "notes": _s(body.get("notes"), 20000),
        "profile_id": int(body["profile_id"]) if str(body.get("profile_id") or "").isdigit() else None,
        "updated_at": _now(),
    }


@app.post("/api/applications")
def create_app(body: dict = Body(...), user=Depends(worker_user)):
    data = _app_data(body)
    if data["profile_id"]:
        _read_profile(data["profile_id"], user)
    found = warnings.evaluate(user["id"], data["company"], data["url_key"], data["description"])
    blocking = [w for w in found if w["behavior"] == "block"]
    if blocking:
        raise HTTPException(409, "Blocked by rule: " + blocking[0]["label"])
    if [w for w in found if w["behavior"] == "confirm"] and not body.get("confirm"):
        return JSONResponse({"needs_confirmation": True, "warnings": found}, status_code=409)
    aid = db.insert("applications", {**data, "user_id": user["id"], "status": "saved", "created_at": _now(),
                                     "source": "job_pool" if body.get("job_id") else ("shared" if body.get("shared_from") else ("link" if data["url"] else "manual")),
                                     "job_id": body.get("job_id")})
    db.insert("status_history", {"application_id": aid, "user_id": user["id"], "status": "saved", "note": "Application created", "created_at": _now()})
    db.audit(user["id"], "application.created", f"{data['company']} — {data['title']}")
    gen = None
    if body.get("generate"):
        gen = _start_generation(_own_app(aid, user), user, body)
    return {"application": _app_out(_own_app(aid, user), full=True), "generation": gen}


@app.get("/api/applications/{aid}")
def get_app(aid: int, user=Depends(worker_user)):
    return {"application": _app_out(_own_app(aid, user), full=True)}


@app.put("/api/applications/{aid}")
def update_app(aid: int, body: dict = Body(...), user=Depends(worker_user)):
    a = _own_app(aid, user)
    data = _app_data(body)
    if data["profile_id"] and data["profile_id"] != a["profile_id"]:
        _need(_usable_profile(data["profile_id"], a["user_id"]), 400, "That profile isn't available to this application's owner.")
    db.update("applications", aid, data)
    return {"application": _app_out(_own_app(aid, user), full=True)}


@app.delete("/api/applications/{aid}")
def delete_app(aid: int, user=Depends(worker_user)):
    a = _own_app(aid, user)
    db.execute("DELETE FROM applications WHERE id = ?", (aid,))
    db.audit(user["id"], "application.deleted", f"{a['company']} — {a['title']}")
    return {"ok": True}


@app.post("/api/applications/{aid}/status")
def set_status(aid: int, body: dict = Body(...), user=Depends(worker_user)):
    a = _own_app(aid, user)
    status = body.get("status")
    if status not in APP_STATUSES:
        raise HTTPException(400, "Unknown status.")
    data = {"status": status, "updated_at": _now()}
    if status == "applied" and not a["applied_at"]:
        data["applied_at"] = _now()
    db.update("applications", aid, data)
    db.insert("status_history", {"application_id": aid, "user_id": user["id"], "status": status, "note": _s(body.get("note"), 500), "created_at": _now()})
    if a["job_id"] and status == "applied":
        db.execute("INSERT OR REPLACE INTO job_marks (user_id, job_id, mark, created_at) VALUES (?, ?, 'applied', ?)", (user["id"], a["job_id"], _now()))
    return {"application": _app_out(_own_app(aid, user), full=True)}


def _resolve_prompt(user, profile_row, requested):
    """Requested prompt -> the profile's prompt -> profile owner's default -> requester's default.
    Bidders have no prompts of their own; they use the prompts of the admin who owns the profile."""
    owner = profile_row["user_id"] if profile_row else user["id"]
    if requested:
        r = db.query_one("SELECT id FROM prompts WHERE id = ? AND user_id IN (?, ?)", (int(requested), user["id"], owner))
        _need(r, 404, "Prompt not found.")
        return r["id"]
    if profile_row and profile_row["prompt_id"]:
        r = db.query_one("SELECT id FROM prompts WHERE id = ? AND user_id = ?", (profile_row["prompt_id"], owner))
        if r:
            return r["id"]
    for uid in (owner, user["id"]):
        r = db.query_one("SELECT id FROM prompts WHERE user_id = ? ORDER BY is_default DESC, id LIMIT 1", (uid,))
        if r:
            return r["id"]
    return None


def _start_generation(a, user, body):
    if not (a["description"] or "").strip():
        raise HTTPException(400, "Add the job description before generating.")
    owner_id = a["user_id"]  # documents always belong to the application's owner
    pid = a["profile_id"] or (int(db.get_user_setting(owner_id, "default_profile_id") or 0) or None)
    prof = _usable_profile(pid, owner_id) if pid else None
    if not prof:
        raise HTTPException(400, "Choose a candidate profile for this application first.")
    if not a["profile_id"]:
        db.update("applications", a["id"], {"profile_id": prof["id"]})
    running = db.query_one("SELECT id FROM generations WHERE application_id = ? AND status IN ('queued','generating')", (a["id"],))
    if running:
        raise HTTPException(409, "A resume is already being generated for this application.")
    template = _s(body.get("template_id"), 80) or prof["template_id"] or db.get_user_setting(owner_id, "default_template_id", D.DEFAULT_TEMPLATE)
    version = (db.query_one("SELECT MAX(version) AS v FROM generations WHERE application_id = ?", (a["id"],))["v"] or 0) + 1
    gid = db.insert("generations", {
        "application_id": a["id"], "user_id": owner_id, "engine_user_id": user["id"], "version": version, "kind": "ai", "status": "queued",
        "message": "Waiting in queue", "prompt_id": _resolve_prompt(user, prof, body.get("prompt_id")),
        "template_id": template, "profile_json": db.jdump(_profile_out(prof)), "created_at": _now(),
    })
    engine.enqueue_generation(gid)
    db.audit(user["id"], "generation.queued", f"#{gid} {a['company']}")
    return _gen_summary(db.query_one("SELECT * FROM generations WHERE id = ?", (gid,)))


@app.post("/api/applications/{aid}/generate")
def generate(aid: int, body: dict = Body(default={}), user=Depends(worker_user)):
    return {"generation": _start_generation(_own_app(aid, user), user, body or {})}


# ====================================================================== generations / documents

def _own_gen(gid, user):
    g = db.query_one("SELECT * FROM generations WHERE id = ?", (gid,))
    _need(g and _can_see(user, g["user_id"]), 404, "Resume version not found.")
    return g


@app.get("/api/generations/{gid}")
def get_gen(gid: int, user=Depends(current_user)):
    g = _own_gen(gid, user)
    out = _gen_summary(g)
    out["application_id"] = g["application_id"]
    out["queue_position"] = None
    if g["status"] == "queued":
        out["queue_position"] = db.query_one("SELECT COUNT(*) AS n FROM generations WHERE status = 'queued' AND id < ?", (gid,))["n"] + 1
    if g["status"] == "completed":
        out["resume"] = D.normalize_resume(g["resume_json"])
        out["cover_letter"] = D.clean_letter(g["cover_letter"], db.jload(g["profile_json"], {}))
        out["answers"] = db.jload(g["answers_json"], [])
    out["answer_requests"] = [dict(r) for r in db.query(
        "SELECT id, status, error, questions, created_at FROM answer_requests WHERE generation_id = ? ORDER BY id DESC", (gid,))]
    out["profile"] = db.jload(g["profile_json"], {})
    return out


@app.post("/api/generations/{gid}/cancel")
def cancel_gen(gid: int, user=Depends(current_user)):
    g = _own_gen(gid, user)
    if g["status"] in ("queued", "generating"):
        db.update("generations", gid, {"status": "cancelled", "message": "Cancelled", "finished_at": _now()})
    return {"ok": True}


@app.put("/api/generations/{gid}")
def edit_gen(gid: int, body: dict = Body(...), user=Depends(current_user)):
    """Save manual edits as a new version (no AI call)."""
    g = _own_gen(gid, user)
    if g["status"] != "completed":
        raise HTTPException(400, "Only completed versions can be edited.")
    resume = D.normalize_resume(body.get("resume") or {})
    version = (db.query_one("SELECT MAX(version) AS v FROM generations WHERE application_id = ?", (g["application_id"],))["v"] or 0) + 1
    answers = body.get("answers")
    new = db.insert("generations", {
        "application_id": g["application_id"], "user_id": g["user_id"], "version": version, "kind": "edit",
        "status": "completed", "progress": 100, "message": f"Edited from v{g['version']}",
        "prompt_id": g["prompt_id"], "template_id": _s(body.get("template_id"), 80) or g["template_id"],
        "profile_json": g["profile_json"], "resume_json": db.jdump(resume),
        "cover_letter": _s(body.get("cover_letter"), 30000) if body.get("cover_letter") is not None else g["cover_letter"],
        "answers_json": db.jdump([{"q": _s(x.get("q"), 4000), "a": _s(x.get("a"), 20000)} for x in answers]) if isinstance(answers, list) else g["answers_json"],
        "created_at": _now(), "finished_at": _now(),
    })
    return {"generation": _gen_summary(db.query_one("SELECT * FROM generations WHERE id = ?", (new,)))}


@app.put("/api/generations/{gid}/template")
def set_gen_template(gid: int, body: dict = Body(...), user=Depends(current_user)):
    _own_gen(gid, user)
    db.update("generations", gid, {"template_id": _s(body.get("template_id"), 80)})
    return {"ok": True}


@app.post("/api/generations/{gid}/answers")
def ask_answers(gid: int, body: dict = Body(...), user=Depends(current_user)):
    g = _own_gen(gid, user)
    if g["status"] != "completed":
        raise HTTPException(400, "Generate the resume first.")
    qs = _s(body.get("questions"), 20000)
    if not qs:
        raise HTTPException(400, "Enter at least one question.")
    rid = db.insert("answer_requests", {"generation_id": gid, "user_id": g["user_id"], "engine_user_id": user["id"], "questions": qs,
                                        "status": "queued", "created_at": _now()})
    engine.enqueue_answers(rid)
    return {"request_id": rid}


def _answers_thread(a, user):
    """Every question/answer for an application as one chat thread, across résumé versions (deduplicated)."""
    gens = db.query("SELECT id, version, answers_json, finished_at FROM generations WHERE application_id = ? AND status = 'completed' ORDER BY id", (a["id"],))
    seen, messages = set(), []
    for g in gens:
        for qa in db.jload(g["answers_json"], []):
            key = ((qa.get("q") or "").strip(), (qa.get("a") or "").strip())
            if not key[1] or key in seen:
                continue
            seen.add(key)
            messages.append({"q": key[0], "a": key[1], "version": g["version"], "generation_id": g["id"], "at": g["finished_at"]})
    latest = gens[-1] if gens else None
    reqs = []
    if gens:
        ids = ",".join(str(g["id"]) for g in gens)
        reqs = db.query(f"""SELECT id, status, error, questions, created_at FROM answer_requests WHERE generation_id IN ({ids})
                            AND (status IN ('queued','running') OR (status = 'failed' AND id = (SELECT MAX(id) FROM answer_requests WHERE generation_id IN ({ids}))))
                            ORDER BY id""")
    return {
        "application": {"id": a["id"], "company": a["company"], "title": a["title"], "url": a["url"]},
        "generation": {"id": latest["id"], "version": latest["version"]} if latest else None,
        "messages": messages,
        "pending": [{"id": r["id"], "questions": P.split_questions(r["questions"]), "status": r["status"], "created_at": r["created_at"]}
                    for r in reqs if r["status"] in ("queued", "running")],
        "failed": [{"id": r["id"], "questions": P.split_questions(r["questions"]), "error": r["error"]} for r in reqs if r["status"] == "failed"],
        "generating": bool(db.query_one("SELECT 1 FROM generations WHERE application_id = ? AND status IN ('queued','generating')", (a["id"],))),
        "service": _service_state(user),
    }


def _service_state(user):
    """Is this user's own DeepSeek able to take requests right now (used by the answers chat)?"""
    snap = engine.snapshot(user["id"])
    down = bool(snap.get("blocked_since")) and snap["mode"] != "demo"
    return {"available": not down, "since": snap.get("blocked_since") if down else None, "message": snap["message"] if down else ""}


@app.get("/api/applications/{aid}/answers")
def app_answers(aid: int, user=Depends(current_user)):
    return _answers_thread(_own_app(aid, user), user)


@app.post("/api/applications/{aid}/answers")
def app_ask(aid: int, body: dict = Body(...), user=Depends(current_user)):
    a = _own_app(aid, user)
    g = db.query_one("SELECT id, user_id FROM generations WHERE application_id = ? AND status = 'completed' ORDER BY id DESC LIMIT 1", (aid,))
    if not g:
        raise HTTPException(400, "Generate the résumé first — answers are written from it.")
    qs = _s(body.get("questions"), 20000)
    if not qs:
        raise HTTPException(400, "Type a question first.")
    rid = db.insert("answer_requests", {"generation_id": g["id"], "user_id": g["user_id"], "engine_user_id": user["id"], "questions": qs,
                                        "status": "queued", "created_at": _now()})
    engine.enqueue_answers(rid)
    return _answers_thread(a, user)


def _render(g, kind, fmt, user, template_id=""):
    profile = db.jload(g["profile_json"], {})
    role_mode = db.get_user_setting(g["user_id"], "role_mode", "fixed")
    show_title = db.get_user_setting(g["user_id"], "show_title", "1") == "1"
    template = D.get_template(template_id or g["template_id"], g["user_id"])
    answers = db.jload(g["answers_json"], [])
    if kind == "resume":
        if fmt == "html":
            return D.render_resume(g["resume_json"], profile, template, role_mode, show_title), "text/html"
        if fmt == "pdf":
            from .render.pdf import html_to_pdf
            return html_to_pdf(D.render_resume(g["resume_json"], profile, template, role_mode, show_title),
                               "css" if template.get("kind") == "style" else "0.4in"), "application/pdf"
        if fmt == "docx":
            return X.resume_docx(g["resume_json"], profile, role_mode, show_title, template.get("style")), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        return D.resume_text(g["resume_json"], profile, role_mode, show_title), "text/plain; charset=utf-8"
    if kind == "cover":
        if fmt in ("html", "pdf"):
            html = D.render_cover_letter(g["cover_letter"], profile, template)
            if fmt == "html":
                return html, "text/html"
            from .render.pdf import html_to_pdf
            return html_to_pdf(html, "0.8in"), "application/pdf"
        if fmt == "docx":
            return X.letter_docx(g["cover_letter"], profile), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        return D.clean_letter(g["cover_letter"], profile) + "\n", "text/plain; charset=utf-8"
    if kind == "answers":
        if fmt in ("html", "pdf"):
            html = D.render_answers(answers, profile, template)
            if fmt == "html":
                return html, "text/html"
            from .render.pdf import html_to_pdf
            return html_to_pdf(html, "0.8in"), "application/pdf"
        if fmt == "docx":
            return X.answers_docx(answers, profile), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        return D.answers_text(answers), "text/plain; charset=utf-8"
    raise HTTPException(404, "Unknown document.")


def _filename(g, kind, fmt):
    profile = db.jload(g["profile_json"], {})
    a = db.query_one("SELECT company FROM applications WHERE id = ?", (g["application_id"],)) or {"company": ""}
    base = "_".join(re.sub(r"[^A-Za-z0-9]+", "_", x).strip("_") for x in [profile.get("name") or "Candidate", {"resume": "Resume", "cover": "Cover_Letter", "answers": "Answers"}[kind], a["company"]] if x)
    return f"{base}.{fmt}"


@app.get("/api/generations/{gid}/doc/{kind}.{fmt}")
def document(gid: int, kind: str, fmt: str, template: str = "", download: int = 0, user=Depends(current_user)):
    g = _own_gen(gid, user)
    if g["status"] != "completed":
        raise HTTPException(400, "This version has not finished generating.")
    if fmt == "json" and kind == "resume":
        headers = {"Content-Disposition": f'attachment; filename="{_filename(g, kind, "json")}"'} if download else {}
        return JSONResponse(D.normalize_resume(g["resume_json"]), headers=headers)
    if fmt not in ("html", "pdf", "docx", "txt") or kind not in ("resume", "cover", "answers"):
        raise HTTPException(404, "Unknown document.")
    try:
        content, media = _render(g, kind, fmt, user, template)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"Could not build the document: {exc}")
    headers = {}
    if download or fmt in ("docx",):
        headers["Content-Disposition"] = f'attachment; filename="{_filename(g, kind, fmt)}"'
    return Response(content, media_type=media, headers=headers)


# ====================================================================== sharing

@app.post("/api/generations/{gid}/share")
def share(gid: int, user=Depends(current_user)):
    g = _own_gen(gid, user)
    if g["status"] != "completed":
        raise HTTPException(400, "Generate the resume first.")
    token = secrets.token_urlsafe(18)
    exp = (datetime.now(timezone.utc) + timedelta(days=SHARE_DAYS)).isoformat(timespec="seconds")
    db.insert("shares", {"token": token, "generation_id": gid, "user_id": user["id"], "created_at": _now(), "expires_at": exp})
    return {"token": token, "path": f"/r/{token}", "expires_at": exp}


@app.get("/api/shares")
def list_shares(user=Depends(current_user)):
    rows = db.query("""SELECT s.*, a.company, a.title, g.version FROM shares s JOIN generations g ON g.id = s.generation_id
                       JOIN applications a ON a.id = g.application_id WHERE s.user_id = ? ORDER BY s.created_at DESC""", (user["id"],))
    return {"shares": rows}


@app.delete("/api/shares/{token}")
def revoke_share(token: str, user=Depends(current_user)):
    db.execute("UPDATE shares SET revoked = 1 WHERE token = ? AND user_id = ?", (token, user["id"]))
    return {"ok": True}


@app.get("/r/{token}", response_class=HTMLResponse)
def public_resume(token: str):
    s = db.query_one("SELECT * FROM shares WHERE token = ?", (token,))
    if not s or s["revoked"] or s["expires_at"] < _now():
        return HTMLResponse("<h2 style='font-family:sans-serif;padding:40px'>This resume link has expired or was revoked.</h2>", status_code=404)
    g = db.query_one("SELECT * FROM generations WHERE id = ?", (s["generation_id"],))
    db.execute("UPDATE shares SET views = views + 1 WHERE token = ?", (token,))
    html, _ = _render(g, "resume", "html", None)
    return HTMLResponse(html.replace("<head>", '<head><meta name="robots" content="noindex">', 1))


# ====================================================================== dashboard

_STAGE_RANK = {"saved": 0, "ready": 1, "applied": 2, "screening": 3, "interview": 4, "offer": 5}


def _status_days(since_day, uid=None):
    """How many applications entered each status on each day since `since_day`, from the status history:
    {status: {day: n}}, for one user's applications or (uid None) for everyone's."""
    sql = """SELECT h.status, substr(h.created_at,1,10) AS d, COUNT(*) AS n FROM status_history h
             JOIN applications a ON a.id = h.application_id WHERE substr(h.created_at,1,10) >= ?"""
    params = [since_day]
    if uid is not None:
        sql += " AND a.user_id = ?"
        params.append(uid)
    out = {s: {} for s in APP_STATUSES}
    for r in db.query(sql + " GROUP BY h.status, d", params):
        out.setdefault(r["status"], {})[r["d"]] = r["n"]
    return out


def _analytics(uid, days):
    """KPIs for a date range plus the previous period of equal length, daily series, funnel and status mix."""
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=days - 1)
    pstart, pend = start - timedelta(days=days), start - timedelta(days=1)
    day_list = [(start + timedelta(days=i)).isoformat() for i in range(days)]

    def daily(sql, params):
        return {r["d"]: r["n"] for r in db.query(sql, params)}

    lo, plo = start.isoformat(), pstart.isoformat()
    created = daily("SELECT substr(created_at,1,10) AS d, COUNT(*) AS n FROM applications WHERE user_id = ? AND substr(created_at,1,10) >= ? GROUP BY d", (uid, plo))
    applied = daily("SELECT substr(applied_at,1,10) AS d, COUNT(*) AS n FROM applications WHERE user_id = ? AND applied_at IS NOT NULL AND substr(applied_at,1,10) >= ? GROUP BY d", (uid, plo))
    resumes = daily("""SELECT substr(COALESCE(finished_at, created_at),1,10) AS d, COUNT(*) AS n FROM generations
                       WHERE user_id = ? AND kind = 'ai' AND status = 'completed' AND substr(COALESCE(finished_at, created_at),1,10) >= ? GROUP BY d""", (uid, plo))
    interviews = daily("""SELECT substr(MIN(h.created_at),1,10) AS d, COUNT(*) AS n FROM (
                            SELECT h.application_id, MIN(h.created_at) AS created_at FROM status_history h JOIN applications a ON a.id = h.application_id
                            WHERE a.user_id = ? AND h.status IN ('interview','offer') GROUP BY h.application_id) h
                          WHERE substr(h.created_at,1,10) >= ? GROUP BY substr(h.created_at,1,10)""", (uid, plo))
    offers = daily("""SELECT substr(h.created_at,1,10) AS d, COUNT(*) AS n FROM (
                        SELECT h.application_id, MIN(h.created_at) AS created_at FROM status_history h JOIN applications a ON a.id = h.application_id
                        WHERE a.user_id = ? AND h.status = 'offer' GROUP BY h.application_id) h
                      WHERE substr(h.created_at,1,10) >= ? GROUP BY substr(h.created_at,1,10)""", (uid, plo))

    def window(series, a, b):
        return sum(v for d, v in series.items() if a <= d <= b)

    cur = (lo, today.isoformat())
    prev = (plo, pend.isoformat())

    def metric(series):
        return {"value": window(series, *cur), "previous": window(series, *prev), "spark": [series.get(d, 0) for d in day_list]}

    # how far each application has progressed (its best stage ever, from its history)
    apps = db.query("SELECT id, status, created_at, applied_at FROM applications WHERE user_id = ? AND substr(created_at,1,10) >= ?", (uid, lo))
    hist = {}
    for r in db.query("""SELECT h.application_id, h.status FROM status_history h JOIN applications a ON a.id = h.application_id
                         WHERE a.user_id = ? AND substr(a.created_at,1,10) >= ?""", (uid, lo)):
        hist.setdefault(r["application_id"], set()).add(r["status"])
    reached = {k: 0 for k in ("added", "applied", "screening", "interview", "offer")}
    mix = {}
    for a in apps:
        statuses = hist.get(a["id"], set()) | {a["status"]}
        best = max([_STAGE_RANK.get(x, -1) for x in statuses] + [2 if a["applied_at"] else -1])
        reached["added"] += 1
        for name, rank in (("applied", 2), ("screening", 3), ("interview", 4), ("offer", 5)):
            if best >= rank:
                reached[name] += 1
        mix[a["status"]] = mix.get(a["status"], 0) + 1

    def rate(sub_from, sub_to):
        return round(100 * sub_to / sub_from) if sub_from else None

    by_status = _status_days(lo, uid)   # one line per status in the activity chart
    # response rate: applications applied in the window that later reached screening or beyond
    applied_ids = [r["id"] for r in db.query("SELECT id FROM applications WHERE user_id = ? AND applied_at IS NOT NULL AND substr(applied_at,1,10) BETWEEN ? AND ?", (uid, *cur))]
    responded = 0
    for aid in applied_ids:
        st = {r["status"] for r in db.query("SELECT status FROM status_history WHERE application_id = ?", (aid,))}
        st.add(db.query_one("SELECT status FROM applications WHERE id = ?", (aid,))["status"])
        if st & {"screening", "interview", "offer"}:
            responded += 1
    return {
        "range": {"days": days, "start": lo, "end": today.isoformat(), "previous_start": plo, "previous_end": pend.isoformat()},
        "metrics": {
            "added": metric(created), "applied": metric(applied), "resumes": metric(resumes),
            "interviews": metric(interviews), "offers": metric(offers),
            "response_rate": {"value": rate(len(applied_ids), responded), "responded": responded, "applied": len(applied_ids)},
        },
        "series": [{"day": d, "added": created.get(d, 0), "applied": applied.get(d, 0), "resumes": resumes.get(d, 0),
                    "statuses": {s: by_status[s].get(d, 0) for s in APP_STATUSES}} for d in day_list],
        "funnel": [{"stage": k, "count": v} for k, v in reached.items()],
        "conversion": {"applied": rate(reached["added"], reached["applied"]), "screening": rate(reached["applied"], reached["screening"]),
                       "interview": rate(reached["screening"], reached["interview"]), "offer": rate(reached["interview"], reached["offer"])},
        "status_mix": [{"status": st, "count": mix.get(st, 0)} for st in APP_STATUSES],
    }


@app.get("/api/dashboard")
def dashboard(days: int = 30, user=Depends(worker_user)):
    uid = user["id"]
    range_days = days if days in (7, 30, 90) else 30
    counts = {r["status"]: r["n"] for r in db.query("SELECT status, COUNT(*) AS n FROM applications WHERE user_id = ? GROUP BY status", (uid,))}
    total = sum(counts.values())
    gens = db.query_one("""SELECT COUNT(*) AS total, SUM(status = 'completed' AND kind = 'ai') AS done,
                           SUM(status = 'failed') AS failed, SUM(status IN ('queued','generating')) AS active
                           FROM generations WHERE user_id = ?""", (uid,))
    since = (datetime.now(timezone.utc) - timedelta(days=29)).date().isoformat()
    created = {r["d"]: r["n"] for r in db.query("SELECT substr(created_at,1,10) AS d, COUNT(*) AS n FROM applications WHERE user_id = ? AND created_at >= ? GROUP BY d", (uid, since))}
    applied = {r["d"]: r["n"] for r in db.query("SELECT substr(applied_at,1,10) AS d, COUNT(*) AS n FROM applications WHERE user_id = ? AND applied_at >= ? GROUP BY d", (uid, since))}
    days = [(datetime.now(timezone.utc) - timedelta(days=i)).date().isoformat() for i in range(29, -1, -1)]
    reached = lambda statuses: sum(counts.get(s, 0) for s in statuses)
    applied_total = reached(["applied", "screening", "interview", "offer", "rejected"])
    recent = [_app_out(a) for a in db.query("SELECT * FROM applications WHERE user_id = ? ORDER BY updated_at DESC LIMIT 6", (uid,))]
    profiles = db.query_one("""SELECT COUNT(*) AS n FROM profiles p WHERE p.user_id = ? OR EXISTS
                               (SELECT 1 FROM profile_assignments pa WHERE pa.profile_id = p.id AND pa.user_id = ?)""", (uid, uid))["n"]
    edited_prompt = db.query_one("SELECT COUNT(*) AS n FROM prompts WHERE user_id = ? AND (updated_at != created_at OR is_default = 0)", (uid,))["n"]
    new_jobs = db.query_one("SELECT COUNT(*) AS n FROM jobs WHERE imported_at >= ?", ((datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds"),))["n"]
    return {
        "user": {"name": user["name"]},
        "kpis": {
            "applications": total, "resumes": gens["done"] or 0, "applied": applied_total,
            "interviews": reached(["interview", "offer"]), "offers": counts.get("offer", 0),
            "response_rate": round(100 * reached(["screening", "interview", "offer"]) / applied_total) if applied_total else 0,
            "active_generations": gens["active"] or 0, "failed_generations": gens["failed"] or 0,
        },
        "pipeline": [{"status": s, "count": counts.get(s, 0)} for s in APP_STATUSES],
        "series": [{"day": d, "created": created.get(d, 0), "applied": applied.get(d, 0)} for d in days],
        "recent": recent,
        "onboarding": {"profile": profiles > 0, "prompt": edited_prompt > 0, "engine": engine.snapshot(uid)["connected"] or engine.mode() == "demo", "application": total > 0},
        "new_jobs_24h": new_jobs,
        "engine": {k: engine_state(user)[k] for k in ("status", "message", "mode", "queue", "connected", "signing_in")},
        "team": _team_rows(user["id"]) if user["role"] == "admin" else None,
        "analytics": _analytics(uid, range_days),
    }


def _team_rows(admin_id):
    rows = db.query("""SELECT u.id, u.name, u.email, u.status, u.last_login,
                       (SELECT COUNT(*) FROM applications a WHERE a.user_id = u.id) AS applications,
                       (SELECT COUNT(*) FROM applications a WHERE a.user_id = u.id AND a.applied_at IS NOT NULL) AS applied,
                       (SELECT COUNT(*) FROM generations g WHERE g.user_id = u.id AND g.kind = 'ai' AND g.status = 'completed') AS resumes,
                       (SELECT MAX(a.created_at) FROM applications a WHERE a.user_id = u.id) AS last_activity
                       FROM users u WHERE u.parent_id = ? AND u.role = 'bidder' ORDER BY u.name COLLATE NOCASE""", (admin_id,))
    for r in rows:
        r["profiles"] = [x["profile_id"] for x in db.query("SELECT profile_id FROM profile_assignments WHERE user_id = ?", (r["id"],))]
    return rows


@app.get("/api/platform/overview")
def platform_overview(user=Depends(master_user)):
    roles = {r["role"]: r["n"] for r in db.query("SELECT role, COUNT(*) AS n FROM users GROUP BY role")}
    pending = db.query("SELECT id, name, email, created_at FROM users WHERE status = 'pending' ORDER BY created_at")
    since = (datetime.now(timezone.utc) - timedelta(days=29)).date().isoformat()
    day_ago = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
    created = {r["d"]: r["n"] for r in db.query("SELECT substr(created_at,1,10) AS d, COUNT(*) AS n FROM applications WHERE created_at >= ? GROUP BY d", (since,))}
    gens_by_day = {r["d"]: r["n"] for r in db.query("SELECT substr(created_at,1,10) AS d, COUNT(*) AS n FROM generations WHERE kind = 'ai' AND created_at >= ? GROUP BY d", (since,))}
    days = [(datetime.now(timezone.utc) - timedelta(days=i)).date().isoformat() for i in range(29, -1, -1)]
    by_status = _status_days(since)
    g = db.query_one("""SELECT SUM(status = 'completed') AS done, SUM(status = 'failed') AS failed,
                        SUM(status IN ('queued','generating')) AS active, SUM(created_at >= ?) AS today
                        FROM generations WHERE kind = 'ai'""", (day_ago,))
    admins = db.query("""SELECT u.id, u.name, u.email, u.status,
                         (SELECT COUNT(*) FROM users b WHERE b.parent_id = u.id AND b.role = 'bidder') AS bidders,
                         (SELECT COUNT(*) FROM applications a WHERE a.user_id = u.id
                            OR a.user_id IN (SELECT b.id FROM users b WHERE b.parent_id = u.id)) AS applications,
                         (SELECT COUNT(*) FROM generations x WHERE x.kind = 'ai' AND x.status = 'completed' AND (x.user_id = u.id
                            OR x.user_id IN (SELECT b.id FROM users b WHERE b.parent_id = u.id))) AS resumes
                         FROM users u WHERE u.role = 'admin' ORDER BY applications DESC, u.name""")
    return {
        "users": {"masters": roles.get("master", 0), "admins": roles.get("admin", 0), "bidders": roles.get("bidder", 0),
                  "pending": len(pending)},
        "pending": pending,
        "applications": db.query_one("SELECT COUNT(*) AS n FROM applications")["n"],
        "generations": {"completed": g["done"] or 0, "failed": g["failed"] or 0, "active": g["active"] or 0, "last_24h": g["today"] or 0},
        "series": [{"day": d, "created": created.get(d, 0), "applied": gens_by_day.get(d, 0), "resumes": gens_by_day.get(d, 0),
                    "statuses": {s: by_status[s].get(d, 0) for s in APP_STATUSES}} for d in days],
        "admins": admins,
        "jobs": db.query_one("SELECT COUNT(*) AS n FROM jobs")["n"],
        "last_import": db.get_setting("jobpool_last_import", ""),
        "engine": {"mode": "demo" if engine.mode() == "demo" else "live", "users": engine.overview()},
        "recent_events": db.query("""SELECT a.action, a.detail, a.created_at, u.name AS user_name FROM audit a
                                     LEFT JOIN users u ON u.id = a.user_id ORDER BY a.id DESC LIMIT 8"""),
    }


# ====================================================================== engine (each user's own DeepSeek account)

@app.get("/api/engine")
def engine_state(user=Depends(current_user)):
    """The signed-in user's own DeepSeek: connection, queue and sign-in state."""
    snap = engine.snapshot(user["id"])
    snap["mode"] = "demo" if snap["mode"] == "demo" else "live"
    snap["provider"], snap["provider_url"] = "DeepSeek", "chat.deepseek.com"
    snap["needs_account"] = user["role"] in ("admin", "bidder")
    snap["signing_in"] = signins.active(user["id"])
    return snap


def _signin_call(fn, *args):
    try:
        return fn(*args)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except Exception as exc:
        raise HTTPException(503, str(exc)[:300])


@app.post("/api/engine/connect")
def engine_connect(user=Depends(current_user)):
    """Open a normal Chrome window on the server computer for this user's own DeepSeek sign-in."""
    if engine.mode() == "demo":
        raise HTTPException(400, "The platform runs in demo mode, so no DeepSeek account is needed right now.")
    out = _signin_call(signins.open, user["id"])
    db.audit(user["id"], "engine.signin_opened")
    return out


@app.get("/api/engine/signin")
def engine_signin_status(user=Depends(current_user)):
    return _signin_call(signins.status, user["id"])


@app.post("/api/engine/signin/done")
def engine_signin_done(user=Depends(current_user)):
    out = _signin_call(signins.finish, user["id"])
    db.audit(user["id"], "engine.signin_closed")
    return out


@app.post("/api/engine/signin/cancel")
def engine_signin_cancel(user=Depends(current_user)):
    return _signin_call(signins.cancel, user["id"])


@app.get("/api/engine/signin/frame")
def engine_signin_frame(h: str = "", user=Depends(current_user)):
    """Latest picture of this user's DeepSeek sign-in window. 204 when nothing changed since hash `h`; 404 once it's closed."""
    try:
        data, digest, w, hgt, mime, locked = signins.frame(user["id"], h or None)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except SignInError as exc:
        raise HTTPException(409, str(exc))
    headers = {"X-Frame-Hash": digest, "X-Frame-Width": str(w), "X-Frame-Height": str(hgt),
               "X-Server-Locked": "1" if locked else "0", "Cache-Control": "no-store"}
    if data is None:
        return Response(status_code=204, headers=headers)
    return Response(content=data, media_type=mime, headers=headers)


@app.post("/api/engine/signin/input")
def engine_signin_input(body: dict = Body(...), user=Depends(current_user)):
    """Replay the user's clicks, drags, wheel and typing in their own sign-in window as real input on the server."""
    actions = body.get("actions")
    if not isinstance(actions, list) or not actions or len(actions) > 60:
        raise HTTPException(400, "Send 1–60 actions.")
    try:
        return {"ok": True, "focused": signins.act(user["id"], actions)}
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except (SignInError, ValueError) as exc:
        raise HTTPException(409, str(exc))


@app.post("/api/engine/disconnect")
def engine_disconnect(user=Depends(current_user)):
    """Forget this user's DeepSeek sign-in (deletes their saved browser profile)."""
    if signins.active(user["id"]):
        _signin_call(signins.cancel, user["id"])
    _signin_call(engine.disconnect, user["id"])
    db.audit(user["id"], "engine.disconnected")
    return engine_state(user)


@app.post("/api/engine/close")
def engine_close(user=Depends(current_user)):
    engine.request_close(user["id"])
    return {"ok": True}


@app.get("/api/engine/users")
def engine_users(user=Depends(master_user)):
    """Master: which users have connected their own DeepSeek account (no account details are shown)."""
    return {"users": engine.overview(), "mode": "demo" if engine.mode() == "demo" else "live"}


# ====================================================================== job pool

@app.get("/api/jobs")
def list_jobs(q: str = "", hours: int = 0, remote: int = 0, provider: str = "", mark: str = "not_applied",
              offset: int = 0, limit: int = 40, user=Depends(current_user)):
    sql = """SELECT j.id, j.provider, j.url, j.title, j.company, j.location, j.remote, j.tags, j.salary, j.posted_at,
                    j.imported_at, substr(j.description, 1, 320) AS snippet, m.mark
             FROM jobs j LEFT JOIN job_marks m ON m.job_id = j.id AND m.user_id = ? WHERE 1=1"""
    params = [user["id"]]
    if q:
        for word in q.split()[:6]:
            sql += " AND (j.title LIKE ? OR j.company LIKE ? OR j.tags LIKE ? OR j.description LIKE ?)"
            params += [f"%{word}%"] * 4
    if hours:
        sql += " AND j.posted_at >= ?"
        params.append((datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds"))
    if remote:
        sql += " AND j.remote = 1"
    if provider:
        sql += " AND j.provider = ?"
        params.append(provider)
    if mark == "not_applied":
        sql += " AND (m.mark IS NULL OR m.mark = 'saved')"
    elif mark in ("applied", "saved", "hidden"):
        sql += " AND m.mark = ?"
        params.append(mark)
    else:
        sql += " AND (m.mark IS NULL OR m.mark != 'hidden')"
    total = db.query_one(f"SELECT COUNT(*) AS n FROM ({sql})", tuple(params))["n"]
    rows = db.query(sql + " ORDER BY j.posted_at IS NULL, j.posted_at DESC, j.id DESC LIMIT ? OFFSET ?", tuple(params + [min(limit, 100), offset]))
    providers = db.query("SELECT provider, COUNT(*) AS n FROM jobs GROUP BY provider ORDER BY n DESC")
    return {"jobs": rows, "total": total, "providers": [{"id": p["provider"], "name": job_pool.PROVIDERS.get(p["provider"], {}).get("name", {"jobright": "Jobright (earlier imports)"}.get(p["provider"], p["provider"])), "count": p["n"]} for p in providers],
            "last_import": db.get_setting("jobpool_last_import", ""), "importing": job_pool.import_running()}


# ---------------------------------------------------------------- shared by users
# Job links people added to their applications that are not already in the imported pool.
# Only public posting details are shared - never who added them, profiles, notes or documents.

_SHARED_BASE = """
    WITH g AS (
        SELECT url_key, MAX(id) AS rep_id, MIN(created_at) AS first_added,
               COUNT(DISTINCT user_id) AS applicants, MAX(user_id = ?) AS mine
        FROM applications
        WHERE url_key != '' AND url_key NOT IN (SELECT url_key FROM jobs WHERE url_key != '')
        GROUP BY url_key)
    SELECT g.rep_id AS id, g.first_added, g.applicants, g.mine AS applied_by_me, a.company, a.title, a.location,
           a.work_arrangement, a.salary, a.url, substr(a.description, 1, 320) AS snippet
    FROM g JOIN applications a ON a.id = g.rep_id WHERE 1 = 1"""


@app.get("/api/jobs/shared")
def list_shared_jobs(q: str = "", hours: int = 0, remote: int = 0, mark: str = "not_applied",
                     offset: int = 0, limit: int = 40, user=Depends(current_user)):
    sql, params = _SHARED_BASE, [user["id"]]
    for word in q.split()[:6]:
        sql += " AND (a.title LIKE ? OR a.company LIKE ? OR a.description LIKE ? OR a.location LIKE ?)"
        params += [f"%{word}%"] * 4
    if hours:
        sql += " AND g.first_added >= ?"
        params.append((datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds"))
    if remote:
        sql += " AND (a.work_arrangement = 'Remote' OR a.location LIKE '%remote%')"
    if mark == "not_applied":
        sql += " AND g.mine = 0"
    elif mark == "applied":
        sql += " AND g.mine = 1"
    total = db.query_one(f"SELECT COUNT(*) AS n FROM ({sql})", tuple(params))["n"]
    rows = db.query(sql + " ORDER BY g.first_added DESC LIMIT ? OFFSET ?", tuple(params + [min(limit, 100), offset]))
    everyone = db.query_one(f"SELECT COUNT(*) AS n FROM ({_SHARED_BASE})", (user["id"],))["n"]
    for r in rows:
        r["applied_by_me"] = bool(r["applied_by_me"])
    return {"jobs": rows, "total": total, "all_shared": everyone}


@app.get("/api/jobs/shared/{rep_id}")
def get_shared_job(rep_id: int, user=Depends(current_user)):
    a = db.query_one("""SELECT * FROM applications WHERE id = ? AND url_key != ''
                        AND url_key NOT IN (SELECT url_key FROM jobs WHERE url_key != '')""", (rep_id,))
    _need(a, 404, "This shared job is no longer available.")
    stats = db.query_one("""SELECT MIN(created_at) AS first_added, COUNT(DISTINCT user_id) AS applicants,
                            MAX(user_id = ?) AS mine FROM applications WHERE url_key = ?""", (user["id"], a["url_key"]))
    return {"job": {
        "id": a["id"], "company": a["company"], "title": a["title"], "location": a["location"],
        "work_arrangement": a["work_arrangement"], "salary": a["salary"], "url": a["url"], "description": a["description"],
        "first_added": stats["first_added"], "applicants": stats["applicants"], "applied_by_me": bool(stats["mine"]),
        "warnings": warnings.evaluate(user["id"], a["company"], a["url_key"], a["description"]),
    }}


@app.get("/api/jobs/{jid}")
def get_job(jid: int, user=Depends(current_user)):
    j = db.query_one("SELECT * FROM jobs WHERE id = ?", (jid,))
    _need(j, 404, "Job not found.")
    j["warnings"] = warnings.evaluate(user["id"], j["company"], url_key(j["url"]), j["description"])
    return {"job": j}


@app.post("/api/jobs/{jid}/mark")
def mark_job(jid: int, body: dict = Body(...), user=Depends(current_user)):
    mark = body.get("mark")
    if mark in ("applied", "saved", "hidden"):
        db.execute("INSERT OR REPLACE INTO job_marks (user_id, job_id, mark, created_at) VALUES (?, ?, ?, ?)", (user["id"], jid, mark, _now()))
    else:
        db.execute("DELETE FROM job_marks WHERE user_id = ? AND job_id = ?", (user["id"], jid))
    return {"ok": True}


@app.get("/api/job-sources")
def list_sources(user=Depends(master_user)):
    rows = db.query("SELECT s.*, (SELECT COUNT(*) FROM jobs j WHERE j.source_id = s.id) AS jobs FROM job_sources s WHERE provider != 'jobright' ORDER BY provider, name")
    return {"sources": rows, "providers": job_pool.PROVIDERS, "importing": job_pool.import_running(),
            "interval_hours": str(job_pool.interval_hours()), "last_import": db.get_setting("jobpool_last_import", "")}


@app.post("/api/job-sources")
def add_source(body: dict = Body(...), user=Depends(master_user)):
    provider = body.get("provider")
    if provider not in job_pool.PROVIDERS:
        raise HTTPException(400, "Choose a provider.")
    token = _s(body.get("token"), 120)
    if job_pool.PROVIDERS[provider]["board"] and not token:
        raise HTTPException(400, "This provider needs a board token / company slug.")
    if db.query_one("SELECT id FROM job_sources WHERE provider = ? AND token = ?", (provider, token)):
        raise HTTPException(409, "That source already exists.")
    sid = db.insert("job_sources", {"provider": provider, "token": token, "enabled": 1,
                                    "name": _s(body.get("name"), 120) or f"{job_pool.PROVIDERS[provider]['name']}{' · ' + token if token else ''}"})
    if body.get("test"):
        return {"id": sid, "result": job_pool.import_source(db.query_one("SELECT * FROM job_sources WHERE id = ?", (sid,)))}
    return {"id": sid}


@app.put("/api/job-sources/{sid}")
def update_source(sid: int, body: dict = Body(...), user=Depends(master_user)):
    data = {}
    if "enabled" in body:
        data["enabled"] = 1 if body["enabled"] else 0
    if body.get("name"):
        data["name"] = _s(body["name"], 120)
    db.update("job_sources", sid, data)
    return {"ok": True}


@app.delete("/api/job-sources/{sid}")
def delete_source(sid: int, user=Depends(master_user)):
    db.execute("DELETE FROM job_sources WHERE id = ?", (sid,))
    return {"ok": True}


@app.post("/api/job-sources/import")
def import_now(user=Depends(master_user)):
    if job_pool.import_running():
        return {"running": True}
    threading.Thread(target=job_pool.import_all, daemon=True).start()
    db.audit(user["id"], "jobpool.import")
    return {"started": True}


# ====================================================================== users (master) and team (admin)

def _new_user(body, role, parent_id, actor):
    name, email, pw = _s(body.get("name"), 120), _s(body.get("email"), 200).lower(), str(body.get("password") or "")
    if not name or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise HTTPException(400, "Enter a name and a valid email address.")
    if len(pw) < 8:
        raise HTTPException(400, "The temporary password needs at least 8 characters.")
    if db.query_one("SELECT id FROM users WHERE email = ?", (email,)):
        raise HTTPException(409, "An account with this email already exists.")
    uid = db.insert("users", {"email": email, "name": name, "password_hash": auth.hash_password(pw), "role": role,
                              "status": "active", "parent_id": parent_id, "created_at": _now()})
    if role == "admin":
        _seed_user(uid)
    db.audit(actor["id"], "user.created", f"{email} as {role}")
    return uid


def _user_rows(where="1=1", params=()):
    rows = db.query(f"""SELECT u.*, p.name AS parent_name,
                        (SELECT COUNT(*) FROM users b WHERE b.parent_id = u.id AND b.role = 'bidder') AS bidders,
                        (SELECT COUNT(*) FROM applications a WHERE a.user_id = u.id) AS applications,
                        (SELECT COUNT(*) FROM generations g WHERE g.user_id = u.id AND g.kind = 'ai' AND g.status = 'completed') AS resumes
                        FROM users u LEFT JOIN users p ON p.id = u.parent_id WHERE {where}
                        ORDER BY CASE u.role WHEN 'master' THEN 0 WHEN 'admin' THEN 1 ELSE 2 END, u.name COLLATE NOCASE""", params)
    return [{**auth.public_user(r), **{k: r[k] for k in ("parent_name", "bidders", "applications", "resumes")}} for r in rows]


@app.get("/api/users")
def list_users(user=Depends(master_user)):
    return {"users": _user_rows()}


@app.post("/api/users")
def create_user(body: dict = Body(...), user=Depends(master_user)):
    role = body.get("role") if body.get("role") in auth.ROLES else "admin"
    parent = None
    if role == "bidder":
        parent = int(body["parent_id"]) if str(body.get("parent_id") or "").isdigit() else None
        _need(parent and db.query_one("SELECT id FROM users WHERE id = ? AND role = 'admin'", (parent,)), 400, "Choose the admin this bidder belongs to.")
    return {"id": _new_user(body, role, parent, user)}


@app.put("/api/users/{uid}")
def update_user(uid: int, body: dict = Body(...), user=Depends(master_user)):
    target = db.query_one("SELECT * FROM users WHERE id = ?", (uid,))
    _need(target, 404, "User not found.")
    if uid == user["id"] and ((body.get("role") and body["role"] != "master") or body.get("status") in ("suspended", "pending")):
        raise HTTPException(400, "You can't demote or suspend your own master account.")
    data = {}
    if body.get("role") in auth.ROLES:
        data["role"] = body["role"]
        if body["role"] != "bidder":
            data["parent_id"] = None
        if body["role"] == "admin" and not db.query_one("SELECT id FROM prompts WHERE user_id = ?", (uid,)):
            _seed_user(uid)
        if target["role"] == "admin" and body["role"] != "admin":
            db.execute("UPDATE users SET parent_id = NULL WHERE parent_id = ?", (uid,))
    if "parent_id" in body and (data.get("role") or target["role"]) == "bidder":
        pid = int(body["parent_id"]) if str(body.get("parent_id") or "").isdigit() else None
        if pid:
            _need(db.query_one("SELECT id FROM users WHERE id = ? AND role = 'admin'", (pid,)), 400, "That admin doesn't exist.")
        if pid != target["parent_id"]:
            db.execute("DELETE FROM profile_assignments WHERE user_id = ?", (uid,))
        data["parent_id"] = pid
    if body.get("status") in ("active", "suspended", "pending"):
        data["status"] = body["status"]
        if body["status"] != "active":
            db.execute("DELETE FROM sessions WHERE user_id = ?", (uid,))
    if body.get("password"):
        if len(body["password"]) < 8:
            raise HTTPException(400, "Use a password with at least 8 characters.")
        data["password_hash"] = auth.hash_password(body["password"])
    db.update("users", uid, data)
    db.audit(user["id"], "user.updated", f"#{uid} {sorted(k for k in data if k != 'password_hash')}{' +password' if body.get('password') else ''}")
    return {"ok": True}


@app.delete("/api/users/{uid}")
def delete_user(uid: int, user=Depends(master_user)):
    if uid == user["id"]:
        raise HTTPException(400, "You can't delete your own account.")
    db.execute("UPDATE users SET parent_id = NULL WHERE parent_id = ?", (uid,))
    db.execute("DELETE FROM users WHERE id = ?", (uid,))
    db.audit(user["id"], "user.deleted", f"#{uid}")
    return {"ok": True}


def _own_bidder(uid, user):
    b = db.query_one("SELECT * FROM users WHERE id = ? AND parent_id = ? AND role = 'bidder'", (uid, user["id"]))
    _need(b, 404, "Bidder not found in your team.")
    return b


@app.get("/api/team")
def list_team(user=Depends(admin_user)):
    profiles = db.query("SELECT id, label FROM profiles WHERE user_id = ? ORDER BY label COLLATE NOCASE", (user["id"],))
    return {"bidders": _team_rows(user["id"]), "profiles": profiles}


@app.post("/api/team")
def add_bidder(body: dict = Body(...), user=Depends(admin_user)):
    uid = _new_user(body, "bidder", user["id"], user)
    mine = {p["id"] for p in db.query("SELECT id FROM profiles WHERE user_id = ?", (user["id"],))}
    for pid in {int(x) for x in body.get("profile_ids") or [] if str(x).isdigit()} & mine:
        db.insert("profile_assignments", {"profile_id": pid, "user_id": uid, "created_at": _now()})
    return {"id": uid}


@app.put("/api/team/{uid}")
def update_bidder(uid: int, body: dict = Body(...), user=Depends(admin_user)):
    _own_bidder(uid, user)
    data = {}
    if body.get("name"):
        data["name"] = _s(body["name"], 120)
    if body.get("status") in ("active", "suspended"):
        data["status"] = body["status"]
        if body["status"] != "active":
            db.execute("DELETE FROM sessions WHERE user_id = ?", (uid,))
    if body.get("password"):
        if len(body["password"]) < 8:
            raise HTTPException(400, "Use a password with at least 8 characters.")
        data["password_hash"] = auth.hash_password(body["password"])
    if "profile_ids" in body:
        mine = {p["id"] for p in db.query("SELECT id FROM profiles WHERE user_id = ?", (user["id"],))}
        db.execute("DELETE FROM profile_assignments WHERE user_id = ?", (uid,))
        for pid in {int(x) for x in body.get("profile_ids") or [] if str(x).isdigit()} & mine:
            db.insert("profile_assignments", {"profile_id": pid, "user_id": uid, "created_at": _now()})
    db.update("users", uid, data)
    db.audit(user["id"], "team.updated", f"#{uid}")
    return {"ok": True}


@app.delete("/api/team/{uid}")
def remove_bidder(uid: int, user=Depends(admin_user)):
    b = _own_bidder(uid, user)
    db.execute("DELETE FROM users WHERE id = ?", (uid,))
    db.audit(user["id"], "team.deleted", b["email"])
    return {"ok": True}


@app.get("/api/audit")
def audit_log(user=Depends(master_user)):
    return {"events": db.query("""SELECT a.*, u.name AS user_name FROM audit a LEFT JOIN users u ON u.id = a.user_id
                                  ORDER BY a.id DESC LIMIT 300""")}


# ====================================================================== company logos

_LOGO_POOL = None


@app.get("/api/logo")
async def company_logo(company: str = "", url: str = "", job_id: int = 0, user=Depends(current_user)):
    """Company logo for a job or application (cached; 404 when none is found, so the UI shows initials).
    Lookups run on a small dedicated pool so a page full of logos never starves the main API threads."""
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    global _LOGO_POOL
    if _LOGO_POOL is None:
        _LOGO_POOL = ThreadPoolExecutor(max_workers=6, thread_name_prefix="logo")
    return await asyncio.get_running_loop().run_in_executor(_LOGO_POOL, _company_logo, company, url, job_id)


def _company_logo(company, url, job_id):
    from . import logos
    feed, description = "", ""
    if job_id:
        j = db.query_one("SELECT company, url, logo, description FROM jobs WHERE id = ?", (job_id,))
        if j:
            company, url, feed, description = j["company"], j["url"], j["logo"], j["description"]
    company = _s(company, 200)
    if not company:
        raise HTTPException(404, "No company.")
    if not description:
        # job descriptions often name the company's website ("visit acme.com", "jobs@acme.io")
        row = db.query_one("""SELECT description FROM applications WHERE company = ? ORDER BY (url = ?) DESC, id DESC LIMIT 1""", (company, url or ""))             or db.query_one("SELECT description FROM jobs WHERE company = ? ORDER BY id DESC LIMIT 1", (company,))
        description = row["description"] if row else ""
    try:
        path = logos.get_logo(company, _s(url, 1000), feed, description or "")
    except Exception as exc:
        logging.getLogger(__name__).warning("Logo lookup failed for %s: %s", company, exc)
        path = None
    if not path:
        return Response(status_code=404, headers={"Cache-Control": "private, max-age=3600"})
    return FileResponse(path, media_type=logos.sniff_type(path) if logos.sniff_type(path) != "application/octet-stream" else "image/png",
                        headers={"Cache-Control": "private, max-age=604800", "X-Content-Type-Options": "nosniff"})


# ====================================================================== static SPA

app.mount("/assets", StaticFiles(directory=os.path.join(WEB_DIR, "assets")), name="assets")


def _site_page(name, request):
    """index.html, robots.txt or sitemap.xml with the site's own address filled in (the Netlify build does the same
    at deploy time), so canonical links, the social preview and the sitemap point at the right place."""
    with open(os.path.join(WEB_DIR, name), encoding="utf-8") as f:
        text = f.read()
    site = PUBLIC_URL or str(request.base_url).rstrip("/")
    modified = datetime.fromtimestamp(os.path.getmtime(os.path.join(WEB_DIR, "index.html")), timezone.utc).strftime("%Y-%m-%d")
    text = text.replace("__PUBLIC_URL__", site).replace("__LASTMOD__", modified)
    tag = re.sub(r"[^A-Za-z0-9_-]", "", GOOGLE_SITE_VERIFICATION)
    return text.replace("<!--seo-extra-->", f'<meta name="google-site-verification" content="{tag}">' if tag else "")


def _public_page(path):
    """web/<path>/index.html or web/<path>.html for a public page such as /features/ or /guides/…, else None."""
    clean = path.strip("/")
    if clean.endswith("/index.html"):
        clean = clean[:-len("/index.html")]
    if not clean or ".." in clean or clean.startswith(("assets/", "api/", "r/")):
        return None
    root = os.path.realpath(WEB_DIR)
    for candidate in (os.path.join(clean, "index.html"), clean + ".html"):
        full = os.path.realpath(os.path.join(root, candidate))
        if full.startswith(root + os.sep) and os.path.isfile(full):
            return candidate.replace(os.sep, "/")
    return None


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str, request: Request):
    if path.startswith("api/"):
        raise HTTPException(404, "Not found.")
    if path in ("", "index.html"):
        return HTMLResponse(_site_page("index.html", request), headers={"Cache-Control": "no-cache"})
    page = _public_page(path)
    if page:
        return HTMLResponse(_site_page(page, request), headers={"Cache-Control": "no-cache"})
    if path == "robots.txt":
        return Response(_site_page(path, request), media_type="text/plain; charset=utf-8", headers={"Cache-Control": "public, max-age=3600"})
    if path == "sitemap.xml":
        return Response(_site_page(path, request), media_type="application/xml; charset=utf-8", headers={"Cache-Control": "public, max-age=3600"})
    if path == "manifest.webmanifest":
        return FileResponse(os.path.join(WEB_DIR, path), media_type="application/manifest+json", headers={"Cache-Control": "no-cache"})
    if path == "favicon.ico":
        return FileResponse(os.path.join(WEB_DIR, "assets", "img", "favicon.ico"), media_type="image/x-icon", headers={"Cache-Control": "public, max-age=604800"})
    # The app has no other pages (its screens live behind #/…): a real 404 keeps search engines from indexing junk URLs.
    with open(os.path.join(WEB_DIR, "404.html"), encoding="utf-8") as f:
        return HTMLResponse(f.read(), status_code=404, headers={"X-Robots-Tag": "noindex"})
