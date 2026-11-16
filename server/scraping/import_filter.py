"""Which jobs the Job Pool imports — one platform-wide filter, managed by the master account.

  * US remote only     the job must be remote and open to the US (US / USA / a US state / North America / anywhere)
  * software only      the title must contain one of the include keywords (whole words, /regex/ allowed)
  * blocked keywords   never import a job whose title contains one of these

Jobs that stop matching (e.g. after the master adds a keyword) are removed with "Apply to the current pool".
"""
import re

from .. import db

DEFAULT_INCLUDE = """software, developer, programmer, full stack, fullstack, full-stack, frontend, front end, front-end, backend, back end,
back-end, web engineer, mobile engineer, ios, android, application engineer, platform engineer, cloud engineer, devops, site reliability,
sre, sdet, qa automation, test automation, automation engineer, ai engineer, ml engineer, machine learning engineer, data engineer,
infrastructure engineer, security engineer, react, angular, vue, node, python, java, javascript, typescript, golang, ruby, rails, php,
.net, c#, c++, rust, scala, kotlin, swift, blockchain, solidity, salesforce developer, embedded software"""

DEFAULT_EXCLUDE = """sales, recruiter, recruiting, marketing, account executive, business development, customer success, support specialist,
mechanical, civil, electrical, hardware, manufacturing, technician, intern, internship, director, vp, vice president, head of"""

SETTINGS = {
    "jobfilter_us_remote": "1",
    "jobfilter_software": "1",
    "jobfilter_include": DEFAULT_INCLUDE,
    "jobfilter_exclude": DEFAULT_EXCLUDE,
}

US_STATES = ("alabama|alaska|arizona|arkansas|california|colorado|connecticut|delaware|florida|georgia|hawaii|idaho|illinois|indiana|iowa|"
             "kansas|kentucky|louisiana|maine|maryland|massachusetts|michigan|minnesota|mississippi|missouri|montana|nebraska|nevada|"
             "new hampshire|new jersey|new mexico|new york|north carolina|north dakota|ohio|oklahoma|oregon|pennsylvania|rhode island|"
             "south carolina|south dakota|tennessee|texas|utah|vermont|virginia|washington|west virginia|wisconsin|wyoming|"
             "district of columbia|san francisco|seattle|austin|boston|chicago|denver|atlanta|los angeles|nyc|miami|dallas")
STATE_ABBR = ("AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|"
              "TN|TX|UT|VT|VA|WA|WV|WI|WY|DC")
US_RE = re.compile(r"\b(united states|usa|u\.s\.a?\.?|us|america|americas|north america|" + US_STATES + r")\b", re.I)
US_ABBR_RE = re.compile(r"(?:,|\s-)\s*(" + STATE_ABBR + r")\b")
OPEN_RE = re.compile(r"^\s*(remote|anywhere|worldwide|global|work from home|wfh|distributed)?\s*$|\b(anywhere|worldwide|global)\b", re.I)
NON_US_RE = re.compile(r"\b(canada|uk|u\.k\.|united kingdom|england|scotland|ireland|europe|european|emea|eu|germany|france|spain|portugal|"
                       r"netherlands|belgium|poland|italy|sweden|norway|denmark|finland|switzerland|austria|czech|romania|bulgaria|serbia|"
                       r"croatia|greece|ukraine|turkey|israel|uae|dubai|saudi|egypt|africa|nigeria|kenya|south africa|india|pakistan|"
                       r"bangladesh|sri lanka|philippines|vietnam|indonesia|malaysia|singapore|thailand|china|hong kong|taiwan|japan|"
                       r"korea|australia|new zealand|apac|asia|latam|latin america|south america|brazil|mexico|argentina|colombia|chile|"
                       r"peru|costa rica|uruguay)\b", re.I)


# job titles written for other markets ("Desarrollador", "Engenheiro"…)
FOREIGN_TITLE_RE = re.compile(r"\b(desarrollador|desarrolladora|ingeniero|ingeniera|programador|engenheiro|engenheira|desenvolvedor|"
                              r"desenvolvedora|analista|entwickler|ingenieur|développeur|developpeur|sviluppatore|vaga|remoto|híbrido)\b", re.I)


def settings():
    return {k: db.get_setting(k, v) for k, v in SETTINGS.items()}


def save(values):
    for k in SETTINGS:
        if k in values:
            v = str(values[k] if values[k] is not None else "")
            db.set_setting(k, v[:8000] if k in ("jobfilter_include", "jobfilter_exclude") else ("1" if v in ("1", "true", "True") else "0"))
    return settings()


def us_remote(job):
    """(ok, reason): remote and open to people in the US."""
    loc = (job.get("location") or "").strip()
    title = job.get("title") or ""
    remote = bool(job.get("remote")) or re.search(r"\bremote\b|work from home|anywhere", f"{loc} {title}", re.I)
    if not remote:
        return False, "not remote"
    if US_RE.search(loc) or US_ABBR_RE.search(loc):
        return True, "US remote"
    if NON_US_RE.search(loc):
        return False, f"remote outside the US ({loc[:40]})"
    if (NON_US_RE.search(title) or FOREIGN_TITLE_RE.search(title)) and not US_RE.search(title):
        return False, "remote outside the US (title)"
    if OPEN_RE.search(loc):
        return True, "remote anywhere"
    return False, f"location not in the US ({loc[:40]})"


# ------------------------------------------------------------------ keyword matching (whole words, like the extension)
def _norm_kw(text):
    t = (text or "").lower()
    t = re.sub(r"[‐-―−]", "-", t)
    t = re.sub(r"[-_/|\\·•,;:()\[\]{}\"'!?*&]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def parse_keywords(text):
    out = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        if re.match(r"^/.+/[a-z]*$", line, re.I):
            out.append(line)
        else:
            out += [p.strip() for p in line.split(",") if p.strip()]
    return out


def _compile(kw):
    m = re.match(r"^/(.+)/([a-z]*)$", kw, re.I)
    if m:
        try:
            rx = re.compile(m.group(1), re.I)
            return lambda norm, orig: bool(rx.search(orig))
        except re.error:
            return None
    n = _norm_kw(kw)
    if not n:
        return None
    rx = re.compile(r"(?:^|[^a-z0-9+#])" + re.escape(n) + r"(?=$|[^a-z0-9+#])", re.I)
    return lambda norm, orig: bool(rx.search(norm))


def title_matches(title, include="", exclude=""):
    """(ok, reason) — exclude wins; empty include means every title."""
    orig = (title or "").strip()
    if not orig:
        return False, "no title"
    norm = _norm_kw(orig)
    for kw in parse_keywords(exclude):
        f = _compile(kw)
        if f and f(norm, orig):
            return False, f'excluded by "{kw}"'
    inc = parse_keywords(include)
    if not inc:
        return True, "all titles"
    for kw in inc:
        f = _compile(kw)
        if f and f(norm, orig):
            return True, f'matched "{kw}"'
    return False, "no keyword matched"


def check(job, cfg=None):
    """(ok, reason) for a job dict with title / location / remote."""
    cfg = cfg or settings()
    title = job.get("title") or ""
    for kw in parse_keywords(cfg["jobfilter_exclude"]):
        f = _compile(kw)
        if f and f(_norm_kw(title), title):
            return False, f'blocked keyword "{kw}"'
    if cfg["jobfilter_software"] == "1":
        ok, why = title_matches(title, cfg["jobfilter_include"], "")
        if not ok:
            return False, "not a software job"
    if cfg["jobfilter_us_remote"] == "1":
        ok, why = us_remote(job)
        if not ok:
            return False, why
    return True, "matches"


def apply_to_pool():
    """Remove pool jobs that don't match the current filter. Returns {reason: count}."""
    cfg = settings()
    removed = {}
    for j in db.query("SELECT id, title, location, remote FROM jobs"):
        ok, why = check(j, cfg)
        if not ok:
            db.execute("DELETE FROM jobs WHERE id = ?", (j["id"],))
            key = re.sub(r'".*"', "keyword", why) if why.startswith("blocked") else re.sub(r"\(.*\)", "", why).strip()
            removed[key] = removed.get(key, 0) + 1
    return removed
