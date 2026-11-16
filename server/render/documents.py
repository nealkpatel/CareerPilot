"""Render resume JSON + profile facts into HTML templates, cover letters, answers and plain text."""

import html as H
import json
import os
import re

from .. import db
from ..engine.text_clean import strip_scraped_ui_chrome

META_RE = re.compile(r"<!--\s*([a-z_]+)\s*:\s*(.*?)\s*-->", re.IGNORECASE)
PLACEHOLDER_RE = re.compile(r"\{\{[a-z0-9_]+\}\}", re.IGNORECASE)
REQUIRED = ("name", "contact", "summary", "experience", "skills", "education")
DEFAULT_TEMPLATE = "ats_classic"

SAMPLE_PROFILE = {
    "name": "Jordan Rivera",
    "headline": "Senior Software Engineer",
    "contact": ["jordan.rivera@email.com", "(555) 010-2030", "linkedin.com/in/jordanrivera", "Austin, TX"],
    "jobs": [
        {"role": "Senior Software Engineer", "company": "Northwind Cloud", "dates": "Jan 2022 - Present", "work_method": "Remote"},
        {"role": "Software Engineer", "company": "Contoso Health", "dates": "Jun 2018 - Dec 2021", "work_method": "Hybrid"},
    ],
    "education": [{"degree": "B.S. Computer Science", "school": "University of Texas at Austin", "dates": "2014 - 2018"}],
    "certifications": [{"name": "AWS Certified Solutions Architect", "issuer": "Amazon Web Services", "dates": "2023"}],
}
SAMPLE_RESUME = {
    "summary": "Senior software engineer with 7 years of experience building cloud-native platforms for healthcare and SaaS. Designed event-driven services on AWS serving millions of requests per day. Strong background in TypeScript, Python, PostgreSQL and Kubernetes.",
    "skills": [
        {"Languages": "TypeScript, Python, Go, SQL"},
        {"Frameworks & Libraries": "React, Next.js, FastAPI, Node.js, Express"},
        {"Cloud & DevOps": "AWS (Lambda, ECS, S3, RDS), Docker, Kubernetes, Terraform, GitHub Actions"},
        {"Databases": "PostgreSQL, Redis, DynamoDB"},
    ],
    "experiences": [
        {"role": "Senior Software Engineer", "bullets": [
            "Architected an event-driven billing platform on AWS Lambda and SQS that processes 2.4 million transactions every day.",
            "Led the migration of 14 legacy services to Kubernetes, introducing Helm charts and automated canary deployments.",
            "Mentored five engineers through design reviews, pairing sessions and a shared engineering handbook.",
        ]},
        {"role": "Software Engineer", "bullets": [
            "Built patient scheduling APIs in Python and FastAPI that integrate with three hospital EHR systems.",
            "Reduced report generation time from 40 seconds to 6 seconds by redesigning PostgreSQL indexes and queries.",
        ]},
    ],
}


# ------------------------------------------------------------------ templates
# Built-in templates are style presets (render/styled.py) that each user can customise.
# Templates uploaded as HTML files still use the placeholder renderer below.

db.execute("""CREATE TABLE IF NOT EXISTS template_styles (
    user_id INTEGER NOT NULL, template_id TEXT NOT NULL, style_json TEXT NOT NULL, updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, template_id))""")


def _meta(html):
    return {m.group(1).lower(): m.group(2).strip() for m in META_RE.finditer(html or "")}


def _tpl(tid, html, label="", custom=False, owner=None):
    meta = _meta(html)
    return {
        "id": tid,
        "kind": "html",
        "label": meta.get("label") or label or tid.replace("_", " ").title(),
        "description": meta.get("description") or ("Your uploaded template" if custom else ""),
        "tags": [t.strip() for t in (meta.get("tags") or "").split(",") if t.strip()],
        "font_family": meta.get("font_family") or '"Segoe UI", Calibri, Arial, sans-serif',
        "text_color": meta.get("text_color") or "#1C1917",
        "muted_color": meta.get("muted_color") or "#57534E",
        "accent_color": meta.get("accent_color") or "#0F766E",
        "custom": custom,
        "owner": owner,
        "html": html,
    }


def resolve_template_id(tid):
    from . import styled
    tid = tid or DEFAULT_TEMPLATE
    return styled.LEGACY_ALIASES.get(tid, tid)


def user_style(tid, user_id):
    """Effective style for a preset: the user's saved customisation, else the preset itself."""
    from . import styled
    pid = resolve_template_id(tid)
    if user_id is not None:
        row = db.query_one("SELECT style_json FROM template_styles WHERE user_id = ? AND template_id = ?", (user_id, pid))
        if row:
            return styled.sanitize_style(db.jload(row["style_json"], {})), True
    return styled.preset_style(pid), False


def _style_tpl(pid, user_id):
    from . import styled
    preset = styled.PRESETS[pid]
    style, customised = user_style(pid, user_id)
    out = {"id": pid, "kind": "style", "label": preset["label"], "description": preset["description"], "tags": preset["tags"],
           "source": preset.get("source", ""), "custom": False, "customised": customised, "style": style,
           "accent_color": style["colors"]["accent"], "text_color": style["colors"]["body"]}
    out.update(styled.legacy_template_dict(style))
    return out


def builtin_templates(user_id=None):
    from . import styled
    return [_style_tpl(pid, user_id) for pid in styled.PRESETS]


def list_templates(user_id=None):
    items = builtin_templates(user_id)
    if user_id is not None:
        for row in db.query("SELECT * FROM custom_templates WHERE user_id = ? ORDER BY label", (user_id,)):
            items.append(_tpl(row["id"], row["html"], row["label"], custom=True, owner=row["user_id"]))
    return items


def get_template(tid, user_id=None):
    from . import styled
    pid = resolve_template_id(tid)
    if pid in styled.PRESETS:
        return _style_tpl(pid, user_id)
    row = db.query_one("SELECT * FROM custom_templates WHERE id = ?", (tid,))
    if row and (user_id is None or row["user_id"] == user_id):
        return _tpl(row["id"], row["html"], row["label"], custom=True, owner=row["user_id"])
    return _style_tpl(styled.DEFAULT_TEMPLATE_ID, user_id)


def validate_template_html(html):
    errors = []
    lower = (html or "").lower()
    if not lower.strip():
        return ["The HTML is empty."]
    if len(html.encode("utf-8", "replace")) > 1_000_000:
        errors.append("The file is larger than 1 MB.")
    if "<body" not in lower:
        errors.append("The HTML must include a <body> section.")
    found = {m.lower() for m in re.findall(r"\{\{([a-z0-9_]+)\}\}", html, re.IGNORECASE)}
    if "resume" not in found:
        missing = [n for n in REQUIRED if n not in found]
        if missing:
            errors.append("Missing placeholders: " + ", ".join("{{%s}}" % n for n in missing) + " (or use one {{resume}} block).")
    if re.search(r"<script\b", lower):
        errors.append("Scripts are not allowed in resume templates.")
    return errors


# ------------------------------------------------------------------ resume JSON

def loads_resume_json(text):
    raw = (text or "").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return json.loads(re.sub(r",\s*([}\]])", r"\1", raw))


def normalize_resume(data):
    """Return {summary, skills:[{cat: str}], experiences:[{role, bullets[]}]}."""
    if isinstance(data, str):
        data = loads_resume_json(data)
    data = data or {}
    skills = []
    for entry in data.get("skills") or []:
        if isinstance(entry, dict):
            for k, v in entry.items():
                if isinstance(v, (list, tuple)):
                    v = ", ".join(str(i) for i in v)
                skills.append({str(k): str(v)})
        elif entry:
            skills.append({"Skills": str(entry)})
    exps = []
    for entry in data.get("experiences") or data.get("experience") or []:
        if isinstance(entry, dict):
            bullets = entry.get("bullets") or entry.get("sentences") or []
            if not isinstance(bullets, (list, tuple)):
                bullets = [bullets]
            exps.append({"role": str(entry.get("role") or "").strip(), "bullets": [str(b).strip() for b in bullets if str(b).strip()]})
        elif isinstance(entry, (list, tuple)):
            exps.append({"role": "", "bullets": [str(b).strip() for b in entry if str(b).strip()]})
    out = {"summary": str(data.get("summary") or "").strip(), "skills": skills, "experiences": exps}
    for extra in ("headline", "projects"):
        if data.get(extra):
            out[extra] = data[extra]
    return out


def validate_resume(data, profile):
    """Return list of problems (empty = OK)."""
    problems = []
    if not data.get("summary"):
        problems.append("The summary is empty.")
    if not data.get("skills"):
        problems.append("No skills were returned.")
    jobs = (profile or {}).get("jobs") or []
    exps = data.get("experiences") or []
    if jobs and len(exps) < len(jobs):
        problems.append(f"Expected {len(jobs)} experiences but got {len(exps)}.")
    thin = [i + 1 for i, e in enumerate(exps) if len(e.get("bullets") or []) <= 1]
    if thin:
        problems.append("Experience " + ", ".join(map(str, thin)) + " has one bullet or less.")
    return problems


def _role(meta, exp, role_mode):
    model_role = (exp or {}).get("role") or ""
    if role_mode == "match" and model_role:
        return model_role
    return meta.get("role") or model_role


def _skills_html(skills):
    rows = []
    for entry in skills:
        for cat, items in entry.items():
            rows.append(
                f'<div class="skill-item"><span class="skill-category">{H.escape(cat)}:</span> '
                f'<span class="skill-list">{H.escape(items)}</span></div>'
            )
    return f'<div class="skills-container">{"".join(rows)}</div>' if rows else ""


def _experience_html(exps, jobs, role_mode):
    blocks = []
    count = max(len(exps), len(jobs))
    for i in range(count):
        meta = jobs[i] if i < len(jobs) else {}
        exp = exps[i] if i < len(exps) else {"role": "", "bullets": []}
        role = _role(meta, exp, role_mode)
        company = meta.get("company") or ""
        loc = ", ".join(x for x in [meta.get("location") or ""] if x)
        method = meta.get("work_method") or ""
        bullets = "".join(f"<li>{H.escape(b)}</li>" for b in exp.get("bullets") or [])
        blocks.append(
            '<div class="experience-item"><div class="exp-header"><div class="exp-lead">'
            f'<span class="exp-title">{H.escape(role)}</span>'
            + (f'<span class="exp-company-name">{H.escape(company)}</span>' if company else "")
            + '</div><div class="exp-meta">'
            f'<span class="exp-date">{H.escape(meta.get("dates") or "")}</span>'
            + (f'<span class="exp-work-method">{H.escape(method)}</span>' if method else "")
            + (f'<span class="exp-work-method">{H.escape(loc)}</span>' if loc else "")
            + f'</div></div><ul class="exp-bullets">{bullets}</ul></div>'
        )
    return "".join(blocks)


def _edu_html(items):
    out = []
    for e in items or []:
        out.append(
            '<div class="education-item">'
            f'<div class="edu-degree"><strong>{H.escape(e.get("degree") or "")}</strong></div>'
            f'<div class="edu-school">{H.escape(e.get("school") or "")}</div>'
            f'<div class="edu-date">{H.escape(e.get("dates") or "")}</div></div>'
        )
    return "".join(out)


def _cert_html(items):
    blocks = []
    for c in items or []:
        if not any((c.get(k) or "").strip() for k in ("name", "issuer", "dates")):
            continue
        blocks.append(
            '<div class="education-item">'
            f'<div class="edu-degree"><strong>{H.escape(c.get("name") or "")}</strong></div>'
            f'<div class="edu-school">{H.escape(c.get("issuer") or "")}</div>'
            f'<div class="edu-date">{H.escape(c.get("dates") or "")}</div></div>'
        )
    if not blocks:
        return ""
    return '<div class="resume-section"><div class="section-title">Certifications</div>' + "".join(blocks) + "</div>"


def _projects_html(projects):
    blocks = []
    for p in projects or []:
        if not isinstance(p, dict) or not (p.get("name") or "").strip():
            continue
        bullets = "".join(f"<li>{H.escape(str(b))}</li>" for b in p.get("bullets") or [] if str(b).strip())
        tech = f' <span class="exp-work-method">{H.escape(p.get("tech") or "")}</span>' if p.get("tech") else ""
        desc = f'<div class="summary-text">{H.escape(p.get("description") or "")}</div>' if p.get("description") else ""
        blocks.append(
            f'<div class="experience-item"><div class="exp-header"><div class="exp-lead"><span class="exp-title">{H.escape(p["name"])}</span>{tech}</div>'
            f'<div class="exp-meta"><span class="exp-date">{H.escape(p.get("dates") or "")}</span></div></div>{desc}<ul class="exp-bullets">{bullets}</ul></div>'
        )
    if not blocks:
        return ""
    return '<div class="resume-section"><div class="section-title">Projects</div>' + "".join(blocks) + "</div>"


def headline_for(profile, data, role_mode):
    if data.get("headline"):
        return str(data["headline"])
    jobs = profile.get("jobs") or []
    exps = data.get("experiences") or []
    if jobs or exps:
        role = _role(jobs[0] if jobs else {}, exps[0] if exps else {}, role_mode)
        if role:
            return role
    return profile.get("headline") or ""


def fragments(data, profile, role_mode="fixed", show_title=True):
    data = normalize_resume(data)
    name = H.escape(profile.get("name") or "")
    headline = headline_for(profile, data, role_mode) if show_title else ""
    if headline:
        name += (
            '<span class="header-headline" style="display:block;font-size:0.52em;font-weight:500;'
            'letter-spacing:0.4px;text-transform:none;margin-top:5px;opacity:0.85">'
            f"{H.escape(headline)}</span>"
        )
    contact = "  ·  ".join(H.escape(str(c)) for c in (profile.get("contact") or []) if str(c).strip())
    return {
        "name": name,
        "contact": contact,
        "summary": H.escape(data["summary"]),
        "experience": _experience_html(data["experiences"], profile.get("jobs") or [], role_mode),
        "skills": _skills_html(data["skills"]),
        "education": _edu_html(profile.get("education")),
        "certifications": _cert_html(profile.get("certifications")) + _projects_html(data.get("projects")),
    }


def render_resume(data, profile, template, role_mode="fixed", show_title=True):
    if template.get("kind") == "style":
        from .styled import render_resume_styled
        return render_resume_styled(data, profile, template["style"], role_mode, show_title)
    parts = fragments(data, profile, role_mode, show_title)
    parts["resume"] = (
        '<div class="resume-output"><div class="resume-header">'
        f'<div class="header-name">{parts["name"]}</div><div class="header-contact">{parts["contact"]}</div></div>'
        f'<div class="resume-section"><div class="section-title">Summary</div><div class="summary-text">{parts["summary"]}</div></div>'
        f'<div class="resume-section"><div class="section-title">Experience</div>{parts["experience"]}</div>'
        f'<div class="resume-section"><div class="section-title">Technical Skills</div>{parts["skills"]}</div>'
        f'<div class="resume-section"><div class="section-title">Education</div>{parts["education"]}</div>'
        f'{parts["certifications"]}</div>'
    )
    html = template.get("html") or ""
    for key, value in parts.items():
        html = html.replace("{{" + key + "}}", value)
    return PLACEHOLDER_RE.sub("", html)


# ------------------------------------------------------------------ letters

def clean_letter(text, profile):
    name = profile.get("name") or "Applicant"
    text = strip_scraped_ui_chrome(text or "")
    text = text.replace("[Your Name]", name)
    text = re.sub(r"\[[^\]\n]{1,60}\]", "", text)
    text = re.sub(r"(?im)^\s*(?:part\s*2[^\n]*|cover letter:?)\s*$", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _simple_doc(title, body_html, template, header=""):
    font = template.get("font_family") or '"Segoe UI", Calibri, Arial, sans-serif'
    color = template.get("text_color") or "#1C1917"
    accent = template.get("accent_color") or "#0F766E"
    muted = template.get("muted_color") or "#57534E"
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><title>{H.escape(title)}</title>{template.get("_font_link", "")}
<style>
html{{background:#fff}} body{{font-family:{font};font-size:11.5px;line-height:1.65;color:{color};background:#fff;max-width:720px;margin:0 auto;padding:36px 42px}}
p{{margin:0 0 14px}} .lh-name{{font-size:20px;font-weight:700;color:{accent};margin:0 0 2px}}
.lh-meta{{color:{muted};font-size:10.5px;margin:0 0 22px;padding-bottom:12px;border-bottom:1px solid {accent}}}
h1{{font-size:15px;letter-spacing:1.2px;text-transform:uppercase;color:{accent};margin:0 0 14px}}
.q{{font-weight:700;margin:18px 0 6px}}
</style></head><body>{header}{body_html}</body></html>"""


def _paras(text):
    return "".join(
        f"<p>{H.escape(p.strip()).replace(chr(10), '<br>')}</p>" for p in re.split(r"\n\s*\n", text or "") if p.strip()
    )


def letterhead(profile):
    contact = "  ·  ".join(H.escape(str(c)) for c in (profile.get("contact") or []) if str(c).strip())
    return f'<div class="lh-name">{H.escape(profile.get("name") or "")}</div><div class="lh-meta">{contact}</div>'


def render_cover_letter(text, profile, template):
    text = clean_letter(text, profile)
    body = _paras(text) or "<p>No cover letter was generated.</p>"
    return _simple_doc("Cover Letter", body, template, letterhead(profile))


def render_answers(answers, profile, template):
    body = "<h1>Application answers</h1>"
    for i, qa in enumerate(answers or [], 1):
        body += f'<div class="q">{i}. {H.escape(qa.get("q") or "")}</div>' + _paras(qa.get("a") or "")
    if not answers:
        body += "<p>No answers yet.</p>"
    return _simple_doc("Application Answers", body, template, letterhead(profile))


# ------------------------------------------------------------------ plain text

def resume_text(data, profile, role_mode="fixed", show_title=True):
    data = normalize_resume(data)
    lines = [profile.get("name") or ""]
    head = headline_for(profile, data, role_mode) if show_title else ""
    if head:
        lines.append(head)
    lines.append(" | ".join(str(c) for c in profile.get("contact") or [] if str(c).strip()))
    lines += ["", "SUMMARY", data["summary"], "", "EXPERIENCE"]
    jobs = profile.get("jobs") or []
    for i, exp in enumerate(data["experiences"]):
        meta = jobs[i] if i < len(jobs) else {}
        lines.append(" | ".join(x for x in [_role(meta, exp, role_mode), meta.get("company"), meta.get("dates"), meta.get("work_method")] if x))
        lines += [f"- {b}" for b in exp["bullets"]] + [""]
    lines.append("SKILLS")
    for entry in data["skills"]:
        for k, v in entry.items():
            lines.append(f"{k}: {v}")
    lines += ["", "EDUCATION"]
    for e in profile.get("education") or []:
        lines.append(" | ".join(x for x in [e.get("degree"), e.get("school"), e.get("dates")] if x))
    certs = [c for c in profile.get("certifications") or [] if c.get("name")]
    if certs:
        lines += ["", "CERTIFICATIONS"] + [" | ".join(x for x in [c.get("name"), c.get("issuer"), c.get("dates")] if x) for c in certs]
    return "\n".join(lines).strip() + "\n"


def answers_text(answers):
    return "\n\n".join(f"{i}. {qa.get('q')}\n{qa.get('a')}" for i, qa in enumerate(answers or [], 1)).strip() + "\n"
