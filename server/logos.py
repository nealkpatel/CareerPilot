"""Company logos: find the company's real website, fetch its sharpest icon once, cache it on disk.

Precision first - a wrong logo is worse than initials. A website is used only when we can tie
it to the company:
  1. a logo URL supplied by the job feed (Remote OK, Remotive, Jobicy, Himalayas);
  2. the job link's own domain, but only when the domain matches the company name
     (job boards and recruiting systems such as JobDiva or Y Combinator are never used);
  3. a website mentioned in the job description that matches the company name;
  4. an exact-name match in Clearbit's company directory;
  5. likely domains built from the name or the job-board slug (name.com, name.io, usename.com…),
     accepted only when the site's own homepage title names the company.
The icon is the largest one found among the site's own icons and two favicon services.
Images are proxied through CareerPilot so browsers never call third parties directly.
"""

import hashlib
import logging
import os
import re
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from html import unescape
from urllib.parse import parse_qsl, urljoin, urlparse

import httpx

from . import db
from .config import FILES_DIR, SCRAPE_USER_AGENT

logger = logging.getLogger(__name__)
LOGO_DIR = os.path.join(FILES_DIR, "logos")
os.makedirs(LOGO_DIR, exist_ok=True)
RECHECK_DAYS = 14
CACHE_VERSION = "3"
HTTP_TIMEOUT = httpx.Timeout(6.0, connect=3.0)
GUESS_BUDGET = 15  # seconds spent verifying guessed domains for one company

_TWO_PART_TLDS = ("co.uk", "com.au", "co.in", "co.jp", "com.br", "co.nz", "com.sg", "co.za", "com.mx", "org.uk")
GOOD_TLDS = ("com", "io", "ai", "co", "net", "org", "app", "dev", "tech", "us", "health", "inc", "so", "xyz", "cloud",
             "software", "works", "jobs")
SUFFIX_WORDS = ("inc", "llc", "ltd", "corp", "corporation", "co", "gmbh", "plc", "limited", "group", "holdings",
                "technologies", "technology", "solutions", "consulting", "labs", "ai", "the", "and", "company", "international")
# path / subdomain words that are never a company slug
GENERIC = {"www", "www1", "www2", "jobs", "jobs2", "job", "careers", "career", "apply", "boards", "job-boards", "board",
           "embed", "job_app", "portal", "en", "us", "eu", "na", "uk", "positions", "position", "o", "p", "j", "companies",
           "company", "recruiting", "hire", "app", "api", "opening", "openings", "details", "jobdetails.asp", "search",
           "vacancies", "vacancy", "posting", "postings", "join", "work", "team", "about"}
PARKED = ("for sale", "buy this domain", "domain is for sale", "parked", "this domain", "godaddy", "sedo", "hugedomains",
          "dan.com", "afternic", "coming soon", "under construction")

_locks = {}
_locks_guard = threading.Lock()

db.execute("""CREATE TABLE IF NOT EXISTS company_logos (
    key TEXT PRIMARY KEY, company TEXT, domain TEXT, source TEXT, status TEXT, file TEXT, checked_at TEXT)""")
if db.get_setting("logo_cache_version") != CACHE_VERSION:
    # earlier versions trusted job-board domains and kept tiny favicons: start fresh
    db.execute("DELETE FROM company_logos")
    for _f in os.listdir(LOGO_DIR):
        try:
            os.remove(os.path.join(LOGO_DIR, _f))
        except OSError:
            pass
    db.set_setting("logo_cache_version", CACHE_VERSION)


# ------------------------------------------------------------------ names and domains

def _words(name):
    return [w for w in re.findall(r"[a-z0-9]+", (name or "").lower())]


def company_key(name):
    words = [w for w in _words(name) if w not in SUFFIX_WORDS]
    return "".join(words) or "".join(_words(name))


def _main_token(name):
    words = [w for w in _words(name) if w not in SUFFIX_WORDS and len(w) >= 4]
    return words[0] if words else ""


def registrable_domain(host):
    host = (host or "").lower().split(":")[0].removeprefix("www.")
    parts = [p for p in host.split(".") if p]
    if len(parts) < 2:
        return ""
    n = 3 if ".".join(parts[-2:]) in _TWO_PART_TLDS else 2
    return ".".join(parts[-n:])


def _label(domain):
    """'usenash.com' -> 'nash', 'rightwayhealthcare.com' -> 'rightwayhealthcare'."""
    lab = re.sub(r"[^a-z0-9]", "", (domain or "").split(".")[0].lower())
    for pre in ("get", "use", "join", "try", "go", "hello", "with", "meet", "the"):
        if lab.startswith(pre) and len(lab) - len(pre) >= 3:
            lab2 = lab[len(pre):]
            return lab2
    return re.sub(r"(hq|app|inc|labs)$", "", lab) or lab


def related(domain, company, slugs=()):
    """True when a domain clearly belongs to this company name."""
    if not domain:
        return False
    key, lab = company_key(company), _label(domain)
    raw = re.sub(r"[^a-z0-9]", "", domain.split(".")[0].lower())
    if not key or not lab:
        return False
    if lab == key or raw == key:
        return True
    if len(lab) >= 4 and key.startswith(lab):          # hims.com  <- Hims & Hers
        return True
    if len(key) >= 4 and (lab.startswith(key) or raw.startswith(key)):   # rightwayhealthcare.com <- Rightway
        return True
    tok = _main_token(company)
    if tok and (lab == tok or raw == tok):
        return True
    return any(s and (s == lab or s == raw) for s in (re.sub(r"[^a-z0-9]", "", x) for x in slugs))


def _domain_of(url):
    try:
        return registrable_domain(urlparse(url if "://" in url else "https://" + url).netloc)
    except Exception:
        return ""


def slugs_from_url(url):
    """Company slugs that recruiting systems put in links: ?for=gametimeunited, /hims-and-hers/, aplayers.teamtailor.com."""
    if not url:
        return []
    try:
        p = urlparse(url if "://" in url else "https://" + url)
    except Exception:
        return []
    out = []
    host_parts = p.netloc.lower().split(":")[0].split(".")
    reg = registrable_domain(p.netloc)
    sub = host_parts[: max(0, len(host_parts) - len(reg.split(".")))] if reg else []
    out += [s for s in sub if s not in GENERIC and not s.isdigit()]
    for k, v in parse_qsl(p.query):
        if k.lower() in ("for", "company", "board", "org", "client") and v and not v.isdigit():
            out.append(v.lower())
    for seg in [s for s in p.path.split("/") if s][:3]:
        s = seg.lower()
        if s in GENERIC or s.isdigit() or len(s) < 3 or len(s) > 40 or re.fullmatch(r"[0-9a-f-]{16,}", s) or "." in s:
            continue
        out.append(s)
        break
    seen, res = set(), []
    for s in out:
        s = s.strip("-_")
        if s and s not in seen:
            seen.add(s)
            res.append(s)
    return res[:3]


def domains_in_text(text):
    found = re.findall(r"(?:https?://|www\.|@|\b)([a-z0-9][a-z0-9-]{1,40}\.(?:com|io|ai|co|net|org|app|dev|tech|health|us|works))\b",
                       (text or "").lower())
    seen, out = set(), []
    for d in found:
        d = registrable_domain(d)
        if d and d not in seen:
            seen.add(d)
            out.append(d)
    return out[:40]


# ------------------------------------------------------------------ http helpers

def _client():
    return httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=True,
                        headers={"User-Agent": SCRAPE_USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})


def _homepage(c, domain):
    """Fetch a site's homepage. Returns (html, final_url) or ("", "")."""
    for url in (f"https://{domain}", f"https://www.{domain}"):
        try:
            r = c.get(url)
        except httpx.ConnectError:
            if url.startswith("https://www."):
                break
            continue
        except Exception:
            break
        if r.status_code < 400 and "html" in r.headers.get("content-type", ""):
            return r.text[:300_000], str(r.url)
    return "", ""


def _site_name(html):
    bits = []
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    if m:
        bits.append(m.group(1))
    for prop in ("og:site_name", "og:title", "application-name", "twitter:title"):
        m = re.search(r'<meta[^>]+(?:property|name)=["\']%s["\'][^>]*content=["\']([^"\']*)' % re.escape(prop), html, re.I) or \
            re.search(r'<meta[^>]+content=["\']([^"\']*)["\'][^>]*(?:property|name)=["\']%s["\']' % re.escape(prop), html, re.I)
        if m:
            bits.append(m.group(1))
    return unescape(" | ".join(bits)).strip()


def _homepage_names_company(html, company):
    """The homepage title must name the company: the full name, or every significant word of it."""
    name = _site_name(html).lower()
    if not name or any(p in name for p in PARKED):
        return False
    if company_key(company) and company_key(company) in re.sub(r"[^a-z0-9]", "", name):
        return True
    significant = [w for w in _words(company) if w not in SUFFIX_WORDS and len(w) >= 3]
    title_words = set(_words(name))
    return bool(significant) and all(w in title_words for w in significant)


# ------------------------------------------------------------------ choosing a website

def _clearbit(company, c):
    """Exact-name match in Clearbit's free company autocomplete."""
    want = company_key(company)
    if len(want) < 3:
        return ""
    queries = []
    for q in (company, re.sub(r"\band\b", "&", company, flags=re.I), " ".join(w for w in company.split() if w.lower().strip(",.") not in SUFFIX_WORDS)):
        q = q.strip()
        if q and q.lower() not in [x.lower() for x in queries]:
            queries.append(q)
    for query in queries[:3]:
        try:
            r = c.get("https://autocomplete.clearbit.com/v1/companies/suggest", params={"query": query})
            rows = [x for x in (r.json() if r.status_code == 200 else []) if isinstance(x, dict) and x.get("domain")]
        except Exception:
            continue
        wants = {want, company_key(query)}
        # the name must match AND the domain must look like the company's own (no unrelated sites)
        exact = [x for x in rows if company_key(x.get("name")) in wants and x["domain"].rsplit(".", 1)[-1] in GOOD_TLDS
                 and related(x["domain"], company)]
        if exact:
            exact.sort(key=lambda x: not x["domain"].endswith(".com"))
            return exact[0]["domain"]
    return ""


def _guesses(company, slugs):
    bases = []
    for b in [company_key(company), _main_token(company)] + [re.sub(r"[^a-z0-9]", "", s) for s in slugs]:
        if b and len(b) >= 3 and b not in bases:
            bases.append(b)
    bases = bases[:3]
    out = []
    # interleave name variants so the main word is tried early too: gametimeunited.com, gametime.com, ...
    for pattern in ("{}.com", "{}.co", "{}.io", "{}.ai", "get{}.com", "use{}.com", "{}hq.com", "join{}.com"):
        for b in bases:
            d = pattern.format(b)
            if d not in out:
                out.append(d)
    return out[:12]


def _verified_guesses(company, slugs):
    """Check likely domains in parallel; return [(domain, html, final_url)] in likelihood order."""
    guesses = _guesses(company, slugs)

    def check(d):
        with _client() as cc:
            html, final = _homepage(cc, d)
        return d, html, final

    ex = ThreadPoolExecutor(max_workers=6)
    futures = {ex.submit(check, d): i for i, d in enumerate(guesses)}
    found = {}
    try:
        for fut in as_completed(futures, timeout=GUESS_BUDGET):
            try:
                d, html, final = fut.result()
            except Exception:
                continue
            if html and _homepage_names_company(html, company):
                found[futures[fut]] = (d, html, final)
    except Exception:
        pass  # time budget reached
    finally:
        ex.shutdown(wait=False, cancel_futures=True)   # don't wait for slow, unresponsive domains
    return [found[i] for i in sorted(found)]


def website_candidates(company, url="", description="", c=None):
    """Yield (domain, how, homepage_html, homepage_url) from most to least trustworthy."""
    slugs = slugs_from_url(url)
    seen = set()

    def fresh(d):
        if d and d not in seen:
            seen.add(d)
            return True
        return False

    # 1. the job link itself, when its domain is the company's
    d = _domain_of(url)
    if d and related(d, company, slugs):
        if d.endswith(".jobs"):          # pearson.jobs -> pearson.com
            d = d[:-5] + ".com"
        if fresh(d):
            yield d, "job link", "", ""
    # 2. matching websites named in the job description
    for d in domains_in_text(description):
        if related(d, company, slugs) and fresh(d):
            yield d, "description", "", ""
    # 3. Clearbit directory
    d = _clearbit(company, c)
    if fresh(d):
        yield d, "directory", "", ""
    # 4. likely domains, confirmed by the homepage. Short one-word names ("Nash", "Truss")
    #    are shared by many unrelated companies, so they are never guessed.
    if _distinctive(company):
        for d, html, final in _verified_guesses(company, slugs):
            final_dom = registrable_domain(urlparse(final).netloc) or d
            d = final_dom if related(final_dom, company, slugs) else d
            if fresh(d):
                yield d, "verified guess", html, final


def _distinctive(company):
    """Multi-word names and longer single words are specific enough to guess a domain for."""
    words = [w for w in _words(company) if w not in SUFFIX_WORDS]
    return len(words) >= 2 or (words and len(words[0]) >= 6)


# ------------------------------------------------------------------ choosing the sharpest icon

def image_size(data):
    """Pixel size of PNG / ICO / JPEG / GIF / WEBP bytes, or 0 when unknown."""
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            w, h = struct.unpack(">II", data[16:24])
            return min(w, h)
        if data[:4] == b"\x00\x00\x01\x00":
            count = struct.unpack("<H", data[4:6])[0]
            best = 0
            for i in range(min(count, 32)):
                w, h = data[6 + 16 * i], data[7 + 16 * i]
                best = max(best, min(w or 256, h or 256))
            return best
        if data[:4] == b"GIF8":
            w, h = struct.unpack("<HH", data[6:10])
            return min(w, h)
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            if data[12:16] == b"VP8X":
                w = 1 + int.from_bytes(data[24:27], "little")
                h = 1 + int.from_bytes(data[27:30], "little")
                return min(w, h)
            return 64
        if data[:2] == b"\xff\xd8":
            i = 2
            while i < len(data) - 9:
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xC0, 0xC1, 0xC2):
                    h, w = struct.unpack(">HH", data[i + 5:i + 9])
                    return min(w, h)
                i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    except Exception:
        return 0
    return 0


def _is_image(r):
    ctype = r.headers.get("content-type", "").lower()
    # never cache SVG: it could carry script and is served from our own origin
    if r.status_code != 200 or "svg" in ctype or len(r.content) <= 100:
        return False
    head = r.content.lstrip()[:5].lower()
    if head.startswith((b"<svg", b"<?xml", b"<!doc", b"<html")):
        return False
    return ctype.startswith("image/") or image_size(r.content) > 0


def _icon_links(html, base):
    out = []
    for tag in re.findall(r"<link\b[^>]*>", html or "", re.I):
        rel = (re.search(r'rel=["\']([^"\']+)', tag, re.I) or [None, ""])[1].lower()
        href = (re.search(r'href=["\']([^"\']+)', tag, re.I) or [None, ""])[1]
        if not href or "icon" not in rel or href.lower().endswith(".svg") or href.startswith("data:"):
            continue
        sizes = (re.search(r'sizes=["\']([^"\']+)', tag, re.I) or [None, ""])[1]
        n = max([int(x) for x in re.findall(r"(\d+)x\d+", sizes)] or [180 if "apple" in rel else 32])
        out.append((n, urljoin(base, unescape(href))))
    out.sort(key=lambda x: -x[0])
    return [u for _n, u in out[:3]]


def best_icon(c, domain, html="", base=""):
    """Collect candidate icons and return (bytes, source) of the sharpest one."""
    if not html:
        html, base = _homepage(c, domain)
    candidates = [("site", u) for u in _icon_links(html, base or f"https://{domain}/")]
    candidates += [("site", f"https://{domain}/apple-touch-icon.png"),
                   ("google", f"https://www.google.com/s2/favicons?domain={domain}&sz=256"),
                   ("duckduckgo", f"https://icons.duckduckgo.com/ip3/{domain}.ico")]
    best, best_src, best_size, seen = None, "", 0, set()
    for src, u in candidates:
        if u in seen:
            continue
        seen.add(u)
        try:
            r = c.get(u)
        except Exception:
            continue
        if not _is_image(r):
            continue
        size = image_size(r.content) or 48
        if size > best_size:
            best, best_src, best_size = r.content, src, size
        if best_size >= 120:
            break
    return best, best_src, best_size


# ------------------------------------------------------------------ cache entry point

def _lock_for(key):
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def get_logo(company, url="", feed_logo="", description=""):
    """Return the path of a cached logo image, or None when there is no logo."""
    key = company_key(company)
    if not key:
        return None
    with _lock_for(key):
        row = db.query_one("SELECT * FROM company_logos WHERE key = ?", (key,))
        if row and row["status"] == "ok" and row["file"] and os.path.isfile(os.path.join(LOGO_DIR, row["file"])):
            return os.path.join(LOGO_DIR, row["file"])
        fresh = row and row["checked_at"] > (datetime.now(timezone.utc) - timedelta(days=RECHECK_DAYS)).isoformat(timespec="seconds")
        new_info = feed_logo or (url and related(_domain_of(url), company, slugs_from_url(url)))
        if row and fresh and row["status"] == "none" and not new_info:
            return None
        data, source, domain, how = None, "", "", ""
        with _client() as c:
            if feed_logo:
                try:
                    r = c.get(feed_logo)
                    if _is_image(r):
                        data, source, how = r.content, "feed", "job feed"
                except Exception:
                    pass
            if data is None or image_size(data) < 48:
                # try websites from most to least trustworthy until one has a usable icon
                for i, (cand, how2, html, base) in enumerate(website_candidates(company, url, description, c)):
                    if i >= 4:
                        break
                    icon, src, size = best_icon(c, cand, html, base)
                    if icon and (data is None or size > image_size(data)):
                        data, source, how, domain = icon, src, how2, cand
                        break
                    domain = domain or cand
        fname = None
        if data:
            fname = hashlib.sha1(key.encode()).hexdigest()[:16] + ".img"
            with open(os.path.join(LOGO_DIR, fname), "wb") as fh:
                fh.write(data)
        db.execute("""INSERT INTO company_logos (key, company, domain, source, status, file, checked_at) VALUES (?, ?, ?, ?, ?, ?, ?)
                      ON CONFLICT(key) DO UPDATE SET company = excluded.company, domain = excluded.domain, source = excluded.source,
                      status = excluded.status, file = excluded.file, checked_at = excluded.checked_at""",
                   (key, company, domain, f"{how} / {source}" if data else how, "ok" if data else "none", fname, db.now_iso()))
        return os.path.join(LOGO_DIR, fname) if fname else None


def sniff_type(path):
    with open(path, "rb") as fh:
        head = fh.read(16)
    if head.startswith(b"\x89PNG"):
        return "image/png"
    if head[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if head[:4] == b"GIF8":
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head[:4] == b"\x00\x00\x01\x00":
        return "image/x-icon"
    return "application/octet-stream"
