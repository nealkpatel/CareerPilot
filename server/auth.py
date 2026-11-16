"""Password hashing, sessions and FastAPI auth dependencies."""

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request

from . import db
from .config import SECURE_COOKIES, SESSION_DAYS

COOKIE = "cp_session"
_ITER = 200_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _ITER).hex()
    return f"pbkdf2${_ITER}${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _algo, iters, salt, digest = stored.split("$")
        check = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iters)).hex()
        return hmac.compare_digest(check, digest)
    except Exception:
        return False


def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    db.insert("sessions", {
        "token": token,
        "user_id": user_id,
        "created_at": now.isoformat(timespec="seconds"),
        "expires_at": (now + timedelta(days=SESSION_DAYS)).isoformat(timespec="seconds"),
    })
    db.update("users", user_id, {"last_login": db.now_iso()})
    return token


def drop_session(token: str):
    db.execute("DELETE FROM sessions WHERE token = ?", (token,))


def set_session_cookie(resp, token: str):
    """SameSite=Lax works both when the server is opened directly and behind the Netlify proxy (same origin there)."""
    resp.set_cookie(COOKIE, token, httponly=True, samesite="lax", secure=SECURE_COOKIES, max_age=60 * 60 * 24 * SESSION_DAYS)


def clear_session_cookie(resp):
    resp.delete_cookie(COOKIE, httponly=True, samesite="lax", secure=SECURE_COOKIES)


def public_user(u):
    return {k: u.get(k) for k in ("id", "email", "name", "role", "status", "parent_id", "created_at", "last_login")}


def current_user(request: Request):
    token = request.cookies.get(COOKIE) or ""
    if not token:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
    if not token:
        raise HTTPException(401, "Please sign in.")
    row = db.query_one(
        """SELECT u.*, s.expires_at FROM sessions s JOIN users u ON u.id = s.user_id
           WHERE s.token = ?""",
        (token,),
    )
    if not row:
        raise HTTPException(401, "Your session has ended. Please sign in again.")
    if row["expires_at"] < datetime.now(timezone.utc).isoformat(timespec="seconds"):
        drop_session(token)
        raise HTTPException(401, "Your session has expired. Please sign in again.")
    if row["status"] == "pending":
        raise HTTPException(403, "Your account is waiting for approval by the platform master.")
    if row["status"] != "active":
        raise HTTPException(403, "This account is suspended. Contact an administrator.")
    return row


ROLES = ("master", "admin", "bidder")


def master_user(user=Depends(current_user)):
    """Platform manager: users, platform settings, job sources, rules, audit."""
    if user["role"] != "master":
        raise HTTPException(403, "Only the platform master can do this.")
    return user


def admin_user(user=Depends(current_user)):
    """Admins manage their own bidders, profiles and prompts."""
    if user["role"] != "admin":
        raise HTTPException(403, "Only admins can do this.")
    return user


def worker_user(user=Depends(current_user)):
    """Anyone who bids (admins and bidders). The master manages the platform only."""
    if user["role"] not in ("admin", "bidder"):
        raise HTTPException(403, "The master account manages the platform and does not bid. Use an admin or bidder account.")
    return user
