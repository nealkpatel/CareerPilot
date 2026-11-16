"""Application warning rules (clearance, citizenship, onsite, travel…) and duplicate detection."""

import re

from . import db

DEFAULT_RULES = [
    {"id": "clearance", "label": "Security clearance required", "behavior": "confirm",
     "keywords": "security clearance, secret clearance, top secret, ts/sci, active clearance, clearance required, polygraph"},
    {"id": "citizenship", "label": "Citizenship / work authorization restriction", "behavior": "warn",
     "keywords": "us citizen, u.s. citizen, citizenship required, green card, no sponsorship, unable to sponsor, will not sponsor"},
    {"id": "onsite", "label": "Not fully remote (onsite or hybrid)", "behavior": "warn",
     "keywords": "onsite, on-site, in office, in-office, hybrid, relocation required, must be located"},
    {"id": "travel", "label": "Significant travel", "behavior": "warn",
     "keywords": "travel required, 50% travel, 25% travel, frequent travel, travel up to"},
    {"id": "background", "label": "Background / trust check", "behavior": "warn",
     "keywords": "public trust, background investigation, drug test, fingerprint"},
]


def rules():
    return db.jload(db.get_setting("warning_rules", ""), None) or DEFAULT_RULES


def save_rules(items):
    clean = []
    for r in items or []:
        label = str(r.get("label") or "").strip()
        kw = str(r.get("keywords") or "").strip()
        if not label or not kw:
            continue
        beh = r.get("behavior") if r.get("behavior") in ("warn", "confirm", "block") else "warn"
        clean.append({"id": re.sub(r"[^a-z0-9]+", "_", (r.get("id") or label).lower()).strip("_"), "label": label, "behavior": beh, "keywords": kw})
    db.set_setting("warning_rules", db.jdump(clean))
    return clean


def flags(description):
    """Labels of keyword rules that match (no duplicate lookup) — used for list rows."""
    text = (description or "").lower()
    if not text:
        return []
    out = []
    for r in rules():
        if any(k.strip() and re.search(r"(?<![a-z])" + re.escape(k.strip().lower()) + r"(?![a-z])", text) for k in r["keywords"].split(",")):
            out.append(r["label"])
    return out


def evaluate(user_id, company, url_key, description, exclude_id=None):
    found = []
    text = (description or "").lower()
    for r in rules():
        hits = [k.strip() for k in r["keywords"].split(",") if k.strip() and re.search(r"(?<![a-z])" + re.escape(k.strip().lower()) + r"(?![a-z])", text)]
        if hits:
            found.append({"id": r["id"], "label": r["label"], "behavior": r["behavior"], "matches": hits[:5]})
    params = [user_id, (company or "").strip()]
    sql = "SELECT id, company, title, status, created_at FROM applications WHERE user_id = ? AND (lower(company) = lower(?)"
    if url_key:
        sql += " OR url_key = ?"
        params.append(url_key)
    sql += ")"
    if exclude_id:
        sql += " AND id != ?"
        params.append(exclude_id)
    dups = db.query(sql + " ORDER BY created_at DESC LIMIT 5", tuple(params)) if (company or url_key) else []
    if dups:
        same_link = bool(url_key) and any(True for _ in dups) and db.query_one(
            "SELECT 1 AS x FROM applications WHERE user_id = ? AND url_key = ?" + (" AND id != ?" if exclude_id else ""),
            tuple([user_id, url_key] + ([exclude_id] if exclude_id else []))) is not None
        found.insert(0, {
            "id": "duplicate", "behavior": "confirm",
            "label": "You already applied with this job link" if same_link else f"You already have {len(dups)} application(s) at {company}",
            "matches": [f"{d['title']} · {d['status']}" for d in dups],
            "applications": dups,
        })
    return found
