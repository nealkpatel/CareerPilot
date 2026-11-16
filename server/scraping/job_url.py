"""Scrape a job posting URL into {company, title, location, description, ...}.

Known applicant-tracking systems are read through their public JSON endpoints.
Other pages are parsed for schema.org JobPosting data, then Open Graph + page text.
JavaScript-only pages fall back to a headless browser render.
"""

import json
import re
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

import httpx

from ..config import SCRAPE_USER_AGENT
from .html_text import html_to_text

TRACKING = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "gh_src", "ref", "source",
            "src", "lever-source", "lever-origin", "fbclid", "gclid", "trk", "trackingid", "refid"}


def url_key(url):
    """Normalise a job URL for duplicate detection."""
    url = (url or "").strip()
    if not url:
        return ""
    try:
        p = urlparse(url if "://" in url else "https://" + url)
    except Exception:
        return url.lower()
    query = [(k, v) for k, v in parse_qsl(p.query) if k.lower() not in TRACKING]
    host = p.netloc.lower().removeprefix("www.")
    path = p.path.rstrip("/")
    return urlunparse(("", host, path, "", urlencode(sorted(query)), "")).lstrip("/")


def _client():
    return httpx.Client(timeout=20, follow_redirects=True, headers={
        "User-Agent": SCRAPE_USER_AGENT, "Accept-Language": "en-US,en;q=0.9",
    })


def _result(**kw):
    base = {"company": "", "title": "", "location": "", "work_arrangement": "", "salary": "", "description": "", "url": "", "source": ""}
    base.update({k: (v or "").strip() if isinstance(v, str) else v for k, v in kw.items()})
    base["work_arrangement"] = base["work_arrangement"] or guess_arrangement(base["location"] + "\n" + base["description"][:3000])
    return base


def guess_arrangement(text):
    t = (text or "").lower()
    if re.search(r"\bhybrid\b", t):
        return "Hybrid"
    if re.search(r"\b(fully remote|100% remote|remote[- ]first|remote)\b", t):
        return "Remote"
    if re.search(r"\b(on[- ]?site|in[- ]office)\b", t):
        return "Onsite"
    return ""


def _greenhouse(c, url, p):
    m = re.search(r"/([^/]+)/jobs/(\d+)", p.path)
    board = m.group(1) if m else ""
    jid = m.group(2) if m else ""
    if not jid:
        q = dict(parse_qsl(p.query))
        jid, board = q.get("gh_jid", ""), q.get("for", board)
    if not (board and jid):
        return None
    r = c.get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{jid}")
    if r.status_code != 200:
        return None
    d = r.json()
    company = (d.get("company_name") or board).replace("-", " ").title() if not d.get("company_name") else d["company_name"]
    return _result(company=company, title=d.get("title"), location=(d.get("location") or {}).get("name", ""),
                   description=html_to_text(d.get("content") or ""), url=d.get("absolute_url") or url, source="Greenhouse")


def _lever(c, url, p):
    parts = [x for x in p.path.split("/") if x]
    if len(parts) < 2:
        return None
    r = c.get(f"https://api.lever.co/v0/postings/{parts[0]}/{parts[1]}")
    if r.status_code != 200:
        return None
    d = r.json()
    cats = d.get("categories") or {}
    desc = d.get("descriptionPlain") or html_to_text(d.get("description") or "")
    for block in d.get("lists") or []:
        desc += f"\n\n{block.get('text', '')}\n" + html_to_text(block.get("content") or "")
    desc += "\n\n" + (d.get("additionalPlain") or "")
    sal = d.get("salaryRange") or {}
    salary = f"{sal.get('currency', '')} {sal.get('min', '')}-{sal.get('max', '')}".strip() if sal.get("min") else ""
    return _result(company=parts[0].replace("-", " ").title(), title=d.get("text"), location=cats.get("location", ""),
                   work_arrangement=(d.get("workplaceType") or "").replace("onsite", "Onsite").replace("remote", "Remote").replace("hybrid", "Hybrid"),
                   salary=salary, description=desc, url=d.get("hostedUrl") or url, source="Lever")


def _ashby(c, url, p):
    parts = [x for x in p.path.split("/") if x]
    if len(parts) < 2:
        return None
    r = c.get(f"https://api.ashbyhq.com/posting-api/job-board/{parts[0]}?includeCompensation=true")
    if r.status_code != 200:
        return None
    for job in r.json().get("jobs") or []:
        if job.get("id") == parts[1] or parts[1] in (job.get("jobUrl") or ""):
            comp = (job.get("compensation") or {}).get("compensationTierSummary") or ""
            return _result(company=parts[0].replace("-", " ").title(), title=job.get("title"), location=job.get("location"),
                           work_arrangement="Remote" if job.get("isRemote") else (job.get("workplaceType") or ""),
                           salary=comp, description=job.get("descriptionPlain") or html_to_text(job.get("descriptionHtml") or ""),
                           url=job.get("jobUrl") or url, source="Ashby")
    return None


def _walk_ld(node):
    if isinstance(node, list):
        for n in node:
            yield from _walk_ld(n)
    elif isinstance(node, dict):
        t = node.get("@type")
        if t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t):
            yield node
        for key in ("@graph", "mainEntity", "itemListElement"):
            if key in node:
                yield from _walk_ld(node[key])


def _meta(html, name):
    m = re.search(r'<meta[^>]+(?:property|name)=["\']%s["\'][^>]*content=["\']([^"\']*)' % re.escape(name), html, re.I) or \
        re.search(r'<meta[^>]+content=["\']([^"\']*)["\'][^>]*(?:property|name)=["\']%s["\']' % re.escape(name), html, re.I)
    return html_to_text(m.group(1)) if m else ""


def parse_html(html, url):
    for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.S | re.I):
        try:
            data = json.loads(block.strip())
        except Exception:
            continue
        for job in _walk_ld(data):
            org = job.get("hiringOrganization") or {}
            org = org[0] if isinstance(org, list) and org else org
            loc = job.get("jobLocation") or {}
            loc = loc[0] if isinstance(loc, list) and loc else loc
            addr = (loc or {}).get("address") or {}
            if isinstance(addr, str):
                location = addr
            else:
                location = ", ".join(x for x in [addr.get("addressLocality"), addr.get("addressRegion"), addr.get("addressCountry") if isinstance(addr.get("addressCountry"), str) else ""] if x)
            arrangement = "Remote" if str(job.get("jobLocationType", "")).upper() == "TELECOMMUTE" else ""
            sal = job.get("baseSalary") or {}
            salary = ""
            if isinstance(sal, dict) and isinstance(sal.get("value"), dict):
                v = sal["value"]
                salary = f"{sal.get('currency', '')} {v.get('minValue', '')}-{v.get('maxValue', '')} {v.get('unitText', '')}".strip(" -")
            return _result(company=org.get("name") if isinstance(org, dict) else "", title=job.get("title"),
                           location=location, work_arrangement=arrangement, salary=salary,
                           description=html_to_text(job.get("description") or ""), url=url, source="Job posting data")
    title = _meta(html, "og:title") or html_to_text((re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I) or [None, ""])[1])
    site = _meta(html, "og:site_name")
    company, role = site, title
    m = re.match(r"(.+?)\s+(?:at|@|-|–|\|)\s+(.+)", title or "")
    if m:
        role, company = m.group(1), company or m.group(2)
    main = re.search(r"<(main|article)[^>]*>(.*?)</\1>", html, re.S | re.I)
    body = html_to_text(main.group(2) if main else html, skip_chrome=True)
    return _result(company=company, title=role, description=body[:20000], url=url, source="Page text")


def scrape_job(url):
    url = (url or "").strip()
    if not url:
        raise ValueError("Paste a job link first.")
    if "://" not in url:
        url = "https://" + url
    p = urlparse(url)
    host = p.netloc.lower()
    with _client() as c:
        try:
            if "greenhouse.io" in host or "gh_jid=" in p.query:
                r = _greenhouse(c, url, p)
                if r:
                    return r
            if "lever.co" in host:
                r = _lever(c, url, p)
                if r:
                    return r
            if "ashbyhq.com" in host:
                r = _ashby(c, url, p)
                if r:
                    return r
        except Exception:
            pass
        html = ""
        try:
            resp = c.get(url)
            if resp.status_code < 400:
                html = resp.text
        except httpx.HTTPError:
            html = ""
    result = parse_html(html, url) if html else None
    if not result or len(result["description"]) < 300:
        try:
            from ..render.pdf import fetch_rendered_html
            rendered = fetch_rendered_html(url)
            alt = parse_html(rendered, url)
            if alt and len(alt["description"]) > len((result or {}).get("description") or ""):
                alt["source"] += " (rendered)"
                result = alt
        except Exception:
            pass
    if not result or not (result["description"] or result["title"]):
        raise ValueError("Could not read that page. Some job sites block automated access — paste the description instead.")
    result["url"] = url
    return result
