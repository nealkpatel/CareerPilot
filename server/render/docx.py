"""Minimal, dependency-free .docx writer for resumes, cover letters and answers."""

import io
import zipfile
from xml.sax.saxutils import escape

from .documents import normalize_resume, headline_for, _role, clean_letter

_CT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>"""
_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""
_DOC_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""
_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:cs="Calibri"/><w:sz w:val="21"/></w:rPr></w:rPrDefault>
<w:pPrDefault><w:pPr><w:spacing w:after="60" w:line="264" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/>
<w:pPr><w:keepNext/><w:spacing w:before="200" w:after="80"/><w:pBdr><w:bottom w:val="single" w:sz="6" w:space="1" w:color="1F4E79"/></w:pBdr></w:pPr>
<w:rPr><w:b/><w:caps/><w:color w:val="1F4E79"/><w:sz w:val="22"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="ListBullet"><w:name w:val="List Bullet"/><w:basedOn w:val="Normal"/>
<w:pPr><w:ind w:left="360" w:hanging="220"/></w:pPr></w:style>
</w:styles>"""


def _run(text, bold=False, size=None, color=None, italic=False):
    props = ""
    if bold:
        props += "<w:b/>"
    if italic:
        props += "<w:i/>"
    if color:
        props += f'<w:color w:val="{color}"/>'
    if size:
        props += f'<w:sz w:val="{size}"/>'
    rpr = f"<w:rPr>{props}</w:rPr>" if props else ""
    return f'<w:r>{rpr}<w:t xml:space="preserve">{escape(str(text))}</w:t></w:r>'


def _p(runs, style=None, align=None, tab_right=False):
    ppr = ""
    if style:
        ppr += f'<w:pStyle w:val="{style}"/>'
    if tab_right:
        pos = tab_right if isinstance(tab_right, int) and not isinstance(tab_right, bool) and tab_right > 1 else 10080
        ppr += f'<w:tabs><w:tab w:val="right" w:pos="{pos}"/></w:tabs>'
    if align:
        ppr += f'<w:jc w:val="{align}"/>'
    return f"<w:p>{'<w:pPr>' + ppr + '</w:pPr>' if ppr else ''}{''.join(runs)}</w:p>"


def _tab():
    return "<w:r><w:tab/></w:r>"


def _package(body_xml, styles_xml=None, sect=None):
    sect = sect or '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="864" w:right="1080" w:bottom="864" w:left="1080" w:header="0" w:footer="0" w:gutter="0"/></w:sectPr>'
    doc = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
        + body_xml + sect + "</w:body></w:document>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CT)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("word/_rels/document.xml.rels", _DOC_RELS)
        z.writestr("word/styles.xml", styles_xml or _STYLES)
        z.writestr("word/document.xml", doc)
    return buf.getvalue()


def _header(profile, headline=""):
    out = [_p([_run(profile.get("name") or "", bold=True, size=36)], align="center")]
    if headline:
        out.append(_p([_run(headline, size=22, color="44546A")], align="center"))
    contact = "  |  ".join(str(c) for c in profile.get("contact") or [] if str(c).strip())
    if contact:
        out.append(_p([_run(contact, size=19, color="57534E")], align="center"))
    return "".join(out)


# ------------------------------------------------------------------ styled résumé (Word)

def _hx(c):
    return (c or "#000000").lstrip("#").upper()


def _font_name(name):
    # Word uses installed fonts by name; web fonts fall back to the user's default if missing
    return {"Source Sans 3": "Source Sans 3", "Garamond": "Garamond"}.get(name, name)


def _styled_styles(s):
    t, c, L = s["typography"], s["colors"], s["layout"]
    body_font, head_font = escape(_font_name(t["body_font"])), escape(_font_name(t["heading_font"]))
    rule = L["heading_rule"]
    border = ""
    if rule != "none":
        val = {"solid": "single", "double": "double", "dotted": "dotted", "dashed": "dashed", "thick": "thick"}[rule]
        sz = max(2, int(L["rule_thickness"] * (2.5 if rule == "thick" else 1)))
        border = f'<w:pBdr><w:bottom w:val="{val}" w:sz="{sz}" w:space="1" w:color="{_hx(L["rule_color"])}"/></w:pBdr>'
    jc = {"left": "left", "center": "center", "right": "right"}[s["sections"]["align"]]
    spacing_line = int(240 * L["line_spacing"] / 100)
    caps = "<w:caps/>" if t["heading_upper"] else ""
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="{body_font}" w:hAnsi="{body_font}" w:cs="{body_font}"/><w:color w:val="{_hx(c['body'])}"/><w:sz w:val="{int(t['body_size'] * 2)}"/></w:rPr></w:rPrDefault>
<w:pPrDefault><w:pPr><w:spacing w:after="0" w:line="{spacing_line}" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/>
<w:pPr><w:keepNext/><w:spacing w:before="{int(L['section_spacing'])}" w:after="{int(L['rule_spacing'] + 40)}"/>{border}<w:jc w:val="{jc}"/></w:pPr>
<w:rPr><w:rFonts w:ascii="{head_font}" w:hAnsi="{head_font}"/><w:b/>{caps}<w:color w:val="{_hx(c['headings'])}"/><w:sz w:val="{int(t['heading_size'] * 2)}"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="ListBullet"><w:name w:val="List Bullet"/><w:basedOn w:val="Normal"/>
<w:pPr><w:ind w:left="300" w:hanging="200"/></w:pPr></w:style>
</w:styles>"""


def _entry_xml(block, values, size_sub, tab_pos=10080):
    """Lines of an entry: left elements, a right tab, right elements."""
    lines = {1: {"left": [], "right": []}, 2: {"left": [], "right": []}}
    for name, el in block["elements"].items():
        val = values.get(name)
        if not el["visible"] or not str(val or "").strip():
            continue
        line = 1 if block["layout"] == "single" else el["line"]
        lines[line][el["align"]].append(_run(val, bold=el["bold"], italic=el["italic"], color=_hx(el["color"]),
                                             size=size_sub if el["align"] == "right" else None))
    out = ""
    sep = _run(block["separator"], color="64748B")
    for n in (1, 2):
        left, right = lines[n]["left"], lines[n]["right"]
        if not left and not right:
            continue
        runs = []
        for i, r in enumerate(left):
            runs += ([sep] if i else []) + [r]
        if right:
            runs.append(_tab())
            for i, r in enumerate(right):
                runs += ([sep] if i else []) + [r]
        out += _p(runs, tab_right=tab_pos)
    return out


def resume_docx(data, profile, role_mode="fixed", show_title=True, style=None):
    if not style:
        return _plain_resume_docx(data, profile, role_mode, show_title)
    from .styled import sanitize_style, classify_contact
    s = sanitize_style(style)
    data = normalize_resume(data)
    t, c, L = s["typography"], s["colors"], s["layout"]
    align = {"left": "left", "center": "center", "right": "right"}[L["header_align"]]
    name = profile.get("name") or ""
    body = _p([_run(name.upper() if t["name_upper"] else name, bold=True, size=int(t["name_size"] * 2), color=_hx(c["name"]))], align=align)
    if s["contact"]["show_title"] and show_title:
        head = headline_for(profile, data, role_mode)
        if head:
            body += _p([_run(head, size=int((t["body_size"] + 1) * 2), color=_hx(c["secondary"]))], align=align)
    items = [(k, v) for k, v in classify_contact(profile.get("contact")) if s["contact"]["items"].get(k, {}).get("visible", True)]
    if items:
        runs = []
        for i, (k, v) in enumerate(items):
            if i:
                runs.append(_run(f"  {L['contact_sep']}  ", color=_hx(c["secondary"]), size=int(t["contact_size"] * 2)))
            runs.append(_run(v, color=_hx(s["contact"]["items"].get(k, {}).get("color", c["contact"])), size=int(t["contact_size"] * 2)))
        body += _p(runs, align=align)
    jobs = profile.get("jobs") or []
    sub = int(t["subtext_size"] * 2)
    page_w = 12240 if L["page_size"] == "letter" else 11906
    tab_pos = int(page_w - L["margins"]["left"] - L["margins"]["right"])
    _entry = _entry_xml

    def _entry_xml_m(block, values, size):
        return _entry(block, values, size, tab_pos)

    def bullets(items):
        mark = {"disc": "•", "circle": "◦", "square": "▪", "dash": "–", "arrow": "›", "check": "✓", "none": ""}[L["bullet_style"]]
        return "".join(_p([_run(f"{mark}  {b}" if mark else b)], style="ListBullet") for b in items if str(b).strip())

    def sec(key):
        if key == "summary":
            return _p([_run(data["summary"])]) if data["summary"] else ""
        if key == "experience":
            out = ""
            for i, exp in enumerate(data["experiences"]):
                meta = jobs[i] if i < len(jobs) else {}
                vals = {"title": _role(meta, exp, role_mode), "company": meta.get("company"), "location": meta.get("location"),
                        "dates": meta.get("dates"), "job_type": meta.get("work_method")}
                out += _entry_xml_m(s["experience"], vals, sub) + bullets(exp["bullets"])
            return out
        if key == "skills":
            rows = [(k, v) for e in data["skills"] for k, v in e.items()]
            if L["skills_layout"] == "inline":
                return _p([_run(", ".join(v for _k, v in rows))]) if rows else ""
            return "".join(_p([_run(k + ": ", bold=True, color=_hx(c["headings"])), _run(v)]) for k, v in rows)
        if key == "education":
            return "".join(_entry_xml_m(s["education"], {"degree": e.get("degree"), "institution": e.get("school"),
                                                       "location": e.get("location"), "dates": e.get("dates")}, sub)
                           for e in profile.get("education") or [])
        if key == "certifications":
            return "".join(_entry_xml_m(s["certifications"], {"name": e.get("name"), "issuer": e.get("issuer"), "date": e.get("dates")}, sub)
                           for e in profile.get("certifications") or [] if (e.get("name") or "").strip())
        if key == "headline":
            text = data.get("headline") or profile.get("headline") or ""
            hl = s["headline"]
            return _p([_run(text, bold=hl["bold"], italic=hl["italic"], color=_hx(hl["color"]))], align=hl["align"]) if text else ""
        if key == "expertise":
            items = data.get("expertise") or [k for e in data["skills"] for k in e.keys()]
            return _p([_run("  ·  ".join(str(x) for x in items))]) if items else ""
        if key == "projects":
            out = ""
            for pj in data.get("projects") or []:
                if isinstance(pj, dict) and (pj.get("name") or "").strip():
                    vals = {"name": pj.get("name"), "link": pj.get("link") or pj.get("url"), "tech": pj.get("tech"), "dates": pj.get("dates")}
                    out += _entry_xml_m(s["projects"], vals, sub)
                    if pj.get("description"):
                        out += _p([_run(pj["description"])])
                    out += bullets(pj.get("bullets") or [])
            return out
        return ""

    for item in s["sections"]["order"]:
        if item["visible"]:
            content = sec(item["key"])
            if content:
                body += _p([_run(item["title"])], style="Heading1") + content
    m = L["margins"]
    w, h = (12240, 15840) if L["page_size"] == "letter" else (11906, 16838)
    sect = (f'<w:sectPr><w:pgSz w:w="{w}" w:h="{h}"/><w:pgMar w:top="{int(m["top"])}" w:right="{int(m["right"])}" '
            f'w:bottom="{int(m["bottom"])}" w:left="{int(m["left"])}" w:header="0" w:footer="0" w:gutter="0"/></w:sectPr>')
    return _package(body, _styled_styles(s), sect)


def _plain_resume_docx(data, profile, role_mode="fixed", show_title=True):
    data = normalize_resume(data)
    body = _header(profile, headline_for(profile, data, role_mode) if show_title else "")
    body += _p([_run("Summary")], style="Heading1") + _p([_run(data["summary"])])
    body += _p([_run("Experience")], style="Heading1")
    jobs = profile.get("jobs") or []
    for i, exp in enumerate(data["experiences"]):
        meta = jobs[i] if i < len(jobs) else {}
        lead = [_run(_role(meta, exp, role_mode), bold=True)]
        if meta.get("company"):
            lead.append(_run("  |  " + meta["company"]))
        tail = " · ".join(x for x in [meta.get("dates"), meta.get("work_method")] if x)
        body += _p(lead + ([_tab(), _run(tail, italic=True, color="57534E")] if tail else []), tab_right=True)
        for b in exp["bullets"]:
            body += _p([_run("•  " + b)], style="ListBullet")
    body += _p([_run("Technical Skills")], style="Heading1")
    for entry in data["skills"]:
        for k, v in entry.items():
            body += _p([_run(k + ": ", bold=True), _run(v)])
    body += _p([_run("Education")], style="Heading1")
    for e in profile.get("education") or []:
        body += _p([_run(e.get("degree") or "", bold=True), _run("  |  " + (e.get("school") or "")), _tab(), _run(e.get("dates") or "", italic=True)], tab_right=True)
    certs = [c for c in profile.get("certifications") or [] if c.get("name")]
    if certs:
        body += _p([_run("Certifications")], style="Heading1")
        for c in certs:
            body += _p([_run(c.get("name") or "", bold=True), _run("  |  " + (c.get("issuer") or "")), _tab(), _run(c.get("dates") or "", italic=True)], tab_right=True)
    return _package(body)


def letter_docx(text, profile):
    body = _header(profile)
    for para in clean_letter(text, profile).split("\n\n"):
        if para.strip():
            body += _p([_run(line) if i == 0 else "<w:r><w:br/></w:r>" + _run(line) for i, line in enumerate(para.strip().split("\n"))])
    return _package(body)


def answers_docx(answers, profile):
    body = _header(profile) + _p([_run("Application answers")], style="Heading1")
    for i, qa in enumerate(answers or [], 1):
        body += _p([_run(f"{i}. {qa.get('q') or ''}", bold=True)])
        for para in (qa.get("a") or "").split("\n\n"):
            if para.strip():
                body += _p([_run(para.strip())])
    return _package(body)
