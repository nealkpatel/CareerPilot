"""Job Pool: import openings from free public job feeds and employer boards (no API keys)."""

import logging
import re
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

from .. import db
from ..config import SCRAPE_USER_AGENT
from .html_text import html_to_text
from .job_url import url_key

logger = logging.getLogger(__name__)

PROVIDERS = {
    "remoteok": {"name": "Remote OK", "board": False, "hint": "Remote tech jobs worldwide"},
    "remotive": {"name": "Remotive", "board": False, "hint": "Optional category, e.g. software-dev"},
    "jobicy": {"name": "Jobicy", "board": False, "hint": "Optional region: usa, uk, canada, europe, anywhere"},
    "himalayas": {"name": "Himalayas", "board": False, "hint": "Remote jobs feed"},
    "wwr": {"name": "We Work Remotely", "board": False, "hint": "Optional RSS category, e.g. remote-programming-jobs"},
    "greenhouse": {"name": "Greenhouse board", "board": True, "hint": "Board token, e.g. airbnb"},
    "lever": {"name": "Lever board", "board": True, "hint": "Company slug, e.g. palantir"},
    "ashby": {"name": "Ashby board", "board": True, "hint": "Organisation slug, e.g. ramp"},
}

DEFAULT_SOURCES = [
    ("remoteok", "", "Remote OK"),
    ("remotive", "software-dev", "Remotive · Software"),
    ("jobicy", "usa", "Jobicy · USA"),
    ("himalayas", "", "Himalayas"),
    ("wwr", "remote-programming-jobs", "We Work Remotely · Programming"),
    ("greenhouse", "gitlab", "GitLab"),
    ("lever", "palantir", "Palantir"),
    ("ashby", "ramp", "Ramp"),
]


def seed_sources():
    # The Jobright feed was read through the auto-apply browser, which was removed. Its imported jobs stay.
    db.execute("UPDATE job_sources SET enabled = 0 WHERE provider = 'jobright'")
    if db.query_one("SELECT COUNT(*) AS n FROM job_sources WHERE provider != 'jobright'")["n"]:
        return
    for provider, token, name in DEFAULT_SOURCES:
        db.insert("job_sources", {"provider": provider, "token": token, "name": name, "enabled": 1})


def _iso(value):
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):
            v = value / 1000 if value > 1e11 else value
            return datetime.fromtimestamp(v, timezone.utc).replace(microsecond=0).isoformat()
        text = str(value).strip()
        if re.match(r"^\d{4}-\d{2}-\d{2}", text):
            dt = datetime.fromisoformat(text.replace("Z", "+00:00").replace(" ", "T"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()
        return parsedate_to_datetime(text).astimezone(timezone.utc).replace(microsecond=0).isoformat()
    except Exception:
        return None


def _is_remote(*texts):
    return 1 if any(re.search(r"\bremote|anywhere|worldwide\b", str(t or ""), re.I) for t in texts) else 0


_MOJIBAKE = re.compile("[ÂÃâð][-¿‘-›Œ-Ÿˆ˜™ŠšŽž]")


def _fix_text(text):
    """Repair UTF-8 text that a feed double-encoded (e.g. 'MÃ¼nchen' -> 'München') and HTML entities."""
    import html as _html
    text = str(text or "")
    if _MOJIBAKE.search(text):
        for enc in ("cp1252", "latin-1"):
            try:
                fixed = text.encode(enc).decode("utf-8")
                if not _MOJIBAKE.search(fixed):
                    text = fixed
                    break
            except (UnicodeEncodeError, UnicodeDecodeError):
                continue
    return _html.unescape(text)


def _job(ext, url, title, company, location="", remote=None, tags="", salary="", description="", posted=None, logo=""):
    title, company, location, description = (_fix_text(x) for x in (title, company, location, description))
    if not isinstance(tags, str):
        tags = [_fix_text(t) for t in tags or []]
    return {
        "external_id": str(ext), "url": url or "", "title": (title or "").strip()[:300],
        "company": (company or "").strip()[:200], "location": (location or "").strip()[:200],
        "remote": _is_remote(location, title) if remote is None else int(bool(remote)),
        "tags": tags if isinstance(tags, str) else ", ".join(str(t) for t in tags or [])[:400],
        "salary": salary or "", "description": (description or "")[:30000], "posted_at": _iso(posted),
        "logo": logo if isinstance(logo, str) and logo.startswith("https://") else "",
        "url_key": url_key(url or ""),
    }


def fetch_provider(provider, token):
    headers = {"User-Agent": SCRAPE_USER_AGENT, "Accept": "application/json, text/xml;q=0.9, */*;q=0.8"}
    with httpx.Client(timeout=30, follow_redirects=True, headers=headers) as c:
        if provider == "remoteok":
            rows = c.get("https://remoteok.com/api").json()
            return [_job(r.get("id"), r.get("url") or r.get("apply_url"), r.get("position"), r.get("company"),
                         r.get("location") or "Remote", 1, r.get("tags") or [],
                         f"${r['salary_min']:,}-${r['salary_max']:,}" if r.get("salary_min") else "",
                         html_to_text(r.get("description") or ""), r.get("epoch") or r.get("date"), r.get("company_logo") or r.get("logo") or "")
                    for r in rows if isinstance(r, dict) and r.get("position")]
        if provider == "remotive":
            url = "https://remotive.com/api/remote-jobs" + (f"?category={token}" if token else "?limit=300")
            rows = c.get(url).json().get("jobs") or []
            return [_job(r.get("id"), r.get("url"), r.get("title"), r.get("company_name"),
                         r.get("candidate_required_location") or "Remote", 1, r.get("tags") or [], r.get("salary") or "",
                         html_to_text(r.get("description") or ""), r.get("publication_date"), r.get("company_logo") or "") for r in rows]
        if provider == "jobicy":
            url = "https://jobicy.com/api/v2/remote-jobs?count=100" + (f"&geo={token}" if token else "")
            rows = c.get(url).json().get("jobs") or []
            return [_job(r.get("id"), r.get("url"), r.get("jobTitle"), r.get("companyName"), r.get("jobGeo") or "Remote", 1,
                         r.get("jobIndustry") or [],
                         f"{r.get('salaryCurrency', '')} {r.get('annualSalaryMin')}-{r.get('annualSalaryMax')}" if r.get("annualSalaryMin") else "",
                         html_to_text(r.get("jobDescription") or r.get("jobExcerpt") or ""), r.get("pubDate"), r.get("companyLogo") or "") for r in rows]
        if provider == "himalayas":
            rows = c.get("https://himalayas.app/jobs/api?limit=100").json().get("jobs") or []
            return [_job(r.get("guid") or r.get("applicationLink"), r.get("applicationLink"), r.get("title"), r.get("companyName"),
                         ", ".join(r.get("locationRestrictions") or []) or "Remote", 1, r.get("categories") or [],
                         f"{r.get('currency', '')} {r.get('minSalary')}-{r.get('maxSalary')}" if r.get("minSalary") else "",
                         html_to_text(r.get("description") or r.get("excerpt") or ""), r.get("pubDate"), r.get("companyLogo") or "") for r in rows]
        if provider == "wwr":
            url = f"https://weworkremotely.com/categories/{token}.rss" if token else "https://weworkremotely.com/remote-jobs.rss"
            root = ET.fromstring(c.get(url).content)
            out = []
            for item in root.iter("item"):
                title = item.findtext("title") or ""
                company, _, role = title.partition(":")
                out.append(_job(item.findtext("guid") or item.findtext("link"), item.findtext("link"), role.strip() or title,
                                company.strip(), item.findtext("region") or "Remote", 1, item.findtext("category") or "", "",
                                html_to_text(item.findtext("description") or ""), item.findtext("pubDate")))
            return out
        if provider == "greenhouse":
            data = c.get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true").json()
            return [_job(r.get("id"), r.get("absolute_url"), r.get("title"), r.get("company_name") or token.title(),
                         (r.get("location") or {}).get("name", ""), None, [d.get("name") for d in r.get("departments") or []], "",
                         html_to_text(r.get("content") or ""), r.get("first_published") or r.get("updated_at"))
                    for r in data.get("jobs") or []]
        if provider == "lever":
            rows = c.get(f"https://api.lever.co/v0/postings/{token}?mode=json").json()
            return [_job(r.get("id"), r.get("hostedUrl"), r.get("text"), token.replace("-", " ").title(),
                         (r.get("categories") or {}).get("location", ""),
                         (r.get("workplaceType") == "remote") or None, (r.get("categories") or {}).get("team", ""), "",
                         r.get("descriptionPlain") or html_to_text(r.get("description") or ""), r.get("createdAt"))
                    for r in rows if isinstance(r, dict)]
        if provider == "ashby":
            rows = c.get(f"https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true").json().get("jobs") or []
            return [_job(r.get("id"), r.get("jobUrl"), r.get("title"), token.replace("-", " ").title(), r.get("location") or "",
                         r.get("isRemote") or None, r.get("department") or "",
                         (r.get("compensation") or {}).get("compensationTierSummary") or "",
                         r.get("descriptionPlain") or html_to_text(r.get("descriptionHtml") or ""), r.get("publishedAt"))
                    for r in rows]
    raise ValueError(f"Unknown provider {provider}")


def import_source(src):
    started = time.time()
    try:
        jobs = fetch_provider(src["provider"], src["token"])
    except Exception as exc:
        db.update("job_sources", src["id"], {"last_run": db.now_iso(), "last_status": f"Error: {exc}"[:300], "last_count": 0})
        return {"source": src["name"], "ok": False, "error": str(exc)}
    now = db.now_iso()
    added = skipped = 0
    from . import import_filter as F
    cfg = F.settings()
    for j in jobs:
        if not j["external_id"] or not j["title"] or not j["url"]:
            continue
        exists = db.query_one("SELECT id, posted_at FROM jobs WHERE provider = ? AND external_id = ?", (src["provider"], j["external_id"]))
        ok, _why = F.check(j, cfg)
        if not ok:  # the platform's import filter (US remote, software, blocked title keywords)
            skipped += 1
            if exists:
                db.execute("DELETE FROM jobs WHERE id = ?", (exists["id"],))
            continue
        if exists:
            db.update("jobs", exists["id"], {k: v for k, v in j.items() if k not in ("external_id",) and (k != "posted_at" or v)})
        else:
            db.insert("jobs", {**j, "source_id": src["id"], "provider": src["provider"], "imported_at": now})
            added += 1
    db.update("job_sources", src["id"], {"last_run": now, "last_status": f"OK · {len(jobs)} read, {added} new, {skipped} filtered out in {time.time() - started:.1f}s",
                                         "last_count": len(jobs)})
    return {"source": src["name"], "ok": True, "read": len(jobs), "added": added, "filtered": skipped}


_import_lock = threading.Lock()


def import_all():
    if not _import_lock.acquire(blocking=False):
        return {"running": True}
    try:
        results = [import_source(s) for s in db.query("SELECT * FROM job_sources WHERE enabled = 1 AND provider != 'jobright'")]
        db.set_setting("jobpool_last_import", db.now_iso())
        return {"results": results}
    finally:
        _import_lock.release()


def import_running():
    return _import_lock.locked()


def _scheduler():
    while True:
        time.sleep(60)
        try:
            hours = float(db.get_setting("jobpool_interval_hours", "0") or 0)
            if hours <= 0:
                continue
            last = db.get_setting("jobpool_last_import", "")
            due = True
            if last:
                elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds()
                due = elapsed >= hours * 3600
            if due:
                logger.info("Running scheduled job pool import")
                import_all()
        except Exception:
            logger.exception("Scheduled import failed")


def start_scheduler():
    threading.Thread(target=_scheduler, daemon=True, name="jobpool-scheduler").start()
