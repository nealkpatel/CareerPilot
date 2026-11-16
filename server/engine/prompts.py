"""Prompt building: user instructions + profile facts + a mandatory output contract."""

import re

DEFAULT_PROMPT_LABEL = "ATS Resume + Cover Letter (default)"

DEFAULT_PROMPT = """You are an expert ATS resume writer and professional career coach.

Study every rule below and follow each one before writing. Your goal is a resume
with an ATS score above 95 (parse rate, quantified impact, repetition control,
spelling and grammar, correct sections, job-description tailoring).

=============================
CANDIDATE
=============================
Name: {name}
Headline: {headline}

Work history (most recent first):
{work_history}

Background notes from the candidate:
{background}

Education:
{education}

=============================
SKILLS RULES
=============================
- Identify every technology, tool, framework and platform mentioned or implied in
  the job description and include its realistic production ecosystem
  (e.g. React -> Redux, React Router, Next.js, React Testing Library).
- Skills must reflect the complete professional background, not only the latest job.
- Group skills into clear categories such as Languages, Frameworks & Libraries,
  APIs & Web Services, Databases, Cloud & DevOps, Testing, Security, Tools.
- Do not add unrelated technologies.

=============================
EXPERIENCE RULES
=============================
- Never change company names or employment dates.
- Show career growth: earlier roles are foundational, senior roles show
  architecture, ownership, optimisation and leadership.
- Distribute required technologies across roles in a historically realistic way.
- Every bullet is a natural, human sentence longer than 15 words.
- About 30% of bullets carry exact numbers (e.g. "cut p95 latency from 480ms to
  120ms"); never use ranges like "20-30%" or vague words like "significantly".
- Most recent role: 8+ bullets. Second: 7+. Third: 5+. Older roles: 4+.
- Do not repeat sentence structures or use "*" characters.

=============================
SUMMARY RULES
=============================
- Under 700 characters, 4-5 sentences, native professional English.
- Mention years of experience, the main industries, the systems built and the
  core stack required by the job description.
- Experience sentences that start with a verb use the past tense.

=============================
COVER LETTER RULES
=============================
- 3-4 paragraphs tailored to {company} and the {job_title} role.
- Professional business-letter format with a salutation and a closing.
- Natural and specific; no exaggerated claims.

=============================
TARGET JOB
=============================
Company: {company}
Position: {job_title}

{job_description}
"""

DEFAULT_QUESTION_PROMPT = """You are helping {name} complete a job application for the {job_title} role at {company}.

Use only the resume and cover letter below as the source of facts. Do not invent
employers, dates or degrees. Answer each application question in order, numbered
to match. Be specific, warm and professional; keep each answer under 180 words
unless the question asks for more. Output only the numbered answers.
"""

OUTPUT_CONTRACT = """
=============================
MANDATORY OUTPUT FORMAT (added by CareerPilot - always follow)
=============================
Reply with exactly two parts and nothing else.

PART 1 - the resume as ONE valid JSON object:
{{
  "summary": "...",
  "skills": [ {{"Category name": "item, item, item"}}, {{"Another category": "item, item"}} ],
  "experiences": [
    {{"role": "job title", "bullets": ["sentence", "sentence", "sentence"]}}
  ]
}}
- Valid JSON only: double quotes, no comments, no trailing commas, no markdown inside.
- Output exactly {count} experience objects in the same order as the saved jobs:
{history}
- Company names, dates and work arrangement come from the saved jobs. Never invent employers.
{role_rule}

PART 2 - a line containing only the words COVER LETTER, then the cover letter as plain text (not JSON).
Do not add explanations before, between or after the two parts.
"""

ROLE_FIXED = "- Copy each saved role title exactly. The summary uses the most recent saved title."
ROLE_MATCH = (
    "- Rewrite role titles so the career progresses toward the target job: experience 1 "
    "matches the target title, the earliest role is entry level in the same career, "
    "titles in between step up. Do not give every job the same title."
)

YES_RE = re.compile(r"^\s*yes\b", re.IGNORECASE)


def _work_history(profile):
    lines = []
    for i, job in enumerate(profile.get("jobs") or [], 1):
        parts = [job.get("dates"), job.get("company"), job.get("role"), job.get("work_method"), job.get("location")]
        lines.append(f"{i}. " + " | ".join(str(p).strip() for p in parts if str(p or "").strip()))
        notes = (job.get("notes") or "").strip()
        if notes:
            lines.extend("   - " + n.strip(" -•") for n in notes.splitlines() if n.strip())
    return "\n".join(lines) or "(no saved jobs)"


def _education(profile):
    rows = []
    for e in profile.get("education") or []:
        rows.append(" | ".join(str(e.get(k) or "").strip() for k in ("degree", "school", "dates") if e.get(k)))
    return "\n".join(rows) or "(none)"


def fill(template, app, profile, extra=None):
    profile = profile or {}
    contact = profile.get("contact") or []
    values = {
        "job_description": app.get("description") or "",
        "jobDescription": app.get("description") or "",
        "job_title": app.get("title") or "",
        "title": app.get("title") or "",
        "company": app.get("company") or "",
        "company_name": app.get("company") or "",
        "url": app.get("url") or "",
        "job_link": app.get("url") or "",
        "location": app.get("location") or "",
        "name": profile.get("name") or "",
        "headline": profile.get("headline") or "",
        "email": contact[0] if contact else "",
        "phone": contact[1] if len(contact) > 1 else "",
        "work_history": _work_history(profile),
        "background": (profile.get("background") or "").strip() or "(none provided)",
        "education": _education(profile),
        "questions": app.get("questions") or "",
    }
    if extra:
        values.update({k: "" if v is None else str(v) for k, v in extra.items()})
    out = template or ""
    for key, value in values.items():
        out = out.replace("{{" + key + "}}", value)
    # single-brace placeholders, but leave JSON braces alone
    out = re.sub(r"\{([A-Za-z_]+)\}", lambda m: values.get(m.group(1), m.group(0)), out)
    # Prompts written for str.format() escape JSON braces as {{ }}; unescape them.
    return out.replace("{{", "{").replace("}}", "}")


def build_resume_prompt(prompt_text, app, profile, role_mode="fixed"):
    profile = profile or {}
    jobs = profile.get("jobs") or []
    history = "\n".join(
        f"  {i}. {(j.get('role') or 'Role').strip()} - {(j.get('company') or 'Company').strip()} - {(j.get('dates') or '').strip()}"
        for i, j in enumerate(jobs, 1)
    ) or "  (no saved jobs)"
    contract = OUTPUT_CONTRACT.format(
        count=len(jobs) or "the same number of",
        history=history,
        role_rule=ROLE_MATCH if role_mode == "match" else ROLE_FIXED,
    )
    body = fill(prompt_text or DEFAULT_PROMPT, app, profile).rstrip()
    if "{job_description}" not in (prompt_text or DEFAULT_PROMPT) and "{{job_description}}" not in (prompt_text or ""):
        body += "\n\n=============================\nTARGET JOB DESCRIPTION\n=============================\n" + (app.get("description") or "")
    return body + "\n" + contract


def build_answer_prompt(question_text, app, profile, resume_text, cover_letter, questions):
    head = fill(question_text or DEFAULT_QUESTION_PROMPT, app, profile).strip()
    return (
        f"{head}\n\n=== RESUME ===\n{resume_text.strip()}\n\n"
        f"=== COVER LETTER ===\n{(cover_letter or '(none)').strip()}\n\n"
        f"=== JOB DESCRIPTION ===\n{(app.get('description') or '').strip()[:6000]}\n\n"
        f"=== APPLICATION QUESTIONS ===\n{questions.strip()}\n"
    )


def split_questions(text):
    """Split a block of questions into a list (blank lines or numbered lines)."""
    text = (text or "").strip()
    if not text:
        return []
    numbered = re.split(r"\n\s*(?=\d+[.)]\s)", "\n" + text)
    items = [re.sub(r"^\s*\d+[.)]\s*", "", q).strip() for q in numbered if q.strip()]
    if len(items) <= 1:
        items = [q.strip() for q in re.split(r"\n\s*\n|\n(?=\S)", text) if q.strip()]
    return items


def split_answers(text, count):
    """Split a numbered answer block into `count` answers (best effort)."""
    text = (text or "").strip()
    parts = re.split(r"\n\s*(?=\**\d+[.)]\**\s)", "\n" + text)
    parts = [re.sub(r"^\s*\**\d+[.)]\**\s*", "", p).strip() for p in parts if p.strip()]
    if len(parts) == count:
        return parts
    if count == 1:
        return [text]
    return parts if len(parts) > count else parts + [""] * (count - len(parts))
