"""Style-driven résumé renderer.

A template is a *style*: a nested dict (typography, layout, colors, contact, experience,
education, certifications, headline, expertise, projects, sections, pdf). Built-in templates are
presets of that style; users can customise any preset and save it. Rendering is single-column,
text-based (ATS-safe); PDF decorations are pure CSS shapes with no text.

Units follow word-processor conventions: font sizes in pt, spacing in twips (20 twips = 1 pt),
rule thickness in eighths of a point.
"""

import copy
import html as H
import re

from .documents import normalize_resume, headline_for, _role, clean_letter

# ------------------------------------------------------------------ fonts
FONTS = {
    # name -> (CSS font stack, self-hosted package in web/assets/fonts or None for installed system fonts)
    "Calibri": ("Calibri, Carlito, 'Segoe UI', Arial, sans-serif", None),
    "Arial": ("Arial, Helvetica, sans-serif", None),
    "Helvetica": ("Helvetica, Arial, sans-serif", None),
    "Georgia": ("Georgia, 'Times New Roman', serif", None),
    "Times New Roman": ("'Times New Roman', Times, serif", None),
    "Garamond": ("Garamond, 'EB Garamond', Georgia, serif", "eb-garamond"),
    "Inter": ("Inter, 'Segoe UI', Arial, sans-serif", "inter"),
    "Roboto": ("Roboto, Arial, sans-serif", "roboto"),
    "Open Sans": ("'Open Sans', Arial, sans-serif", "open-sans"),
    "Lato": ("Lato, Arial, sans-serif", "lato"),
    "Source Sans 3": ("'Source Sans 3', Arial, sans-serif", "source-sans-3"),
    "IBM Plex Sans": ("'IBM Plex Sans', Arial, sans-serif", "ibm-plex-sans"),
    "Nunito Sans": ("'Nunito Sans', Arial, sans-serif", "nunito-sans"),
    "Merriweather": ("Merriweather, Georgia, serif", "merriweather"),
    "Lora": ("Lora, Georgia, serif", "lora"),
    "Source Serif 4": ("'Source Serif 4', Georgia, serif", "source-serif-4"),
    "Libre Baskerville": ("'Libre Baskerville', Georgia, serif", "libre-baskerville"),
    "JetBrains Mono": ("'JetBrains Mono', Consolas, monospace", "jetbrains-mono"),
}

SECTION_KEYS = ["summary", "experience", "skills", "projects", "education", "certifications", "headline", "expertise"]
SECTION_TITLES = {"summary": "Professional Summary", "experience": "Professional Experience", "skills": "Technical Skills",
                  "projects": "Projects", "education": "Education", "certifications": "Certifications",
                  "headline": "Headline", "expertise": "Core Expertise"}


def _el(visible=True, line=1, align="left", bold=False, italic=False, color="#1e293b"):
    return {"visible": visible, "line": line, "align": align, "bold": bold, "italic": italic, "color": color}


DEFAULT_STYLE = {
    "typography": {"body_font": "Calibri", "heading_font": "Calibri", "name_size": 20, "heading_size": 11.5, "body_size": 10.5,
                   "contact_size": 9.5, "subtext_size": 9.5, "letter_spacing": 0, "name_upper": False, "heading_upper": True},
    "layout": {"header_align": "left", "contact_sep": "·", "line_spacing": 135, "section_spacing": 220, "name_contact_gap": 60,
               "page_size": "letter", "margins": {"top": 620, "bottom": 620, "left": 760, "right": 760},
               "heading_rule": "solid", "rule_color": "#1e293b", "rule_thickness": 6, "rule_spacing": 60,
               "skills_layout": "category", "bullet_style": "disc"},
    "colors": {"name": "#111827", "headings": "#1e293b", "body": "#1f2937", "accent": "#2563eb", "contact": "#475569",
               "secondary": "#64748b", "section_bg": "#e8eef5", "section_label": "#1e293b"},
    "contact": {"layout": "single", "show_title": True,
                "items": {k: {"visible": True, "align": "left", "color": "#475569"} for k in ("email", "phone", "location", "linkedin", "links")}},
    "experience": {"layout": "double", "line_gap": 30, "bullet_gap": 40, "separator": " · ",
                   "elements": {"title": _el(True, 1, "left", True), "company": _el(True, 2, "left", False, False),
                                "location": _el(True, 2, "right", False, True, "#64748b"), "dates": _el(True, 1, "right", False, False, "#64748b"),
                                "job_type": _el(False, 2, "right", False, True, "#64748b")}},
    "education": {"layout": "double", "line_gap": 30, "separator": " · ",
                  "elements": {"degree": _el(True, 1, "left", True), "institution": _el(True, 2, "left"),
                               "location": _el(False, 2, "right", False, True, "#64748b"), "dates": _el(True, 1, "right", False, False, "#64748b")}},
    "certifications": {"layout": "single", "line_gap": 30, "separator": " · ",
                       "elements": {"name": _el(True, 1, "left", True), "issuer": _el(True, 1, "left"),
                                    "date": _el(True, 1, "right", False, False, "#64748b")}},
    "headline": {"size": "auto", "top_gap": 0, "bottom_gap": 100, "color": "#334155", "bold": False, "italic": True, "align": "left"},
    "expertise": {"layout": "inline"},
    "projects": {"layout": "double", "line_gap": 30, "bullet_gap": 40, "separator": " · ",
                 "elements": {"name": _el(True, 1, "left", True), "link": _el(True, 2, "left", False, False, "#2563eb"),
                              "tech": _el(True, 2, "right", False, True, "#64748b"), "dates": _el(True, 1, "right", False, False, "#64748b")}},
    "sections": {"align": "left", "order": [{"key": k, "title": SECTION_TITLES[k], "visible": k not in ("headline", "expertise")} for k in SECTION_KEYS]},
    "pdf": {"theme": "none", "header_accent": "none", "watermark": "none", "watermark_strength": 12, "section_style": "none"},
}

# ------------------------------------------------------------------ decorations (pure shapes, no text)
# Each accent / watermark is HTML positioned against the page; colors use CSS variables
# --acc (accent) and --acc2 (a lighter accent) set on the page.
ACCENTS = {
    "none": ("None", ""),
    "top_rule": ("Top Rule", '<div style="position:absolute;top:18pt;left:36pt;right:36pt;height:1.2pt;background:var(--acc)"></div>'),
    "side_accent": ("Side Accent", '<div style="position:absolute;top:0;bottom:0;left:0;width:7pt;background:var(--acc)"></div>'),
    "corner_triangle": ("Corner Triangle", '<div style="position:absolute;top:0;right:0;border-top:64pt solid var(--acc);border-left:64pt solid transparent"></div>'),
    "two_tone_corner": ("Two Tone Corner", '<div style="position:absolute;top:0;right:0;border-top:80pt solid var(--acc2);border-left:80pt solid transparent"></div>'
                        '<div style="position:absolute;top:0;right:0;border-top:48pt solid var(--acc);border-left:48pt solid transparent"></div>'),
    "double_corner": ("Double Corner", '<div style="position:absolute;top:16pt;right:16pt;width:34pt;height:34pt;border-top:1.6pt solid var(--acc);border-right:1.6pt solid var(--acc)"></div>'
                      '<div style="position:absolute;top:22pt;right:22pt;width:22pt;height:22pt;border-top:1.6pt solid var(--acc);border-right:1.6pt solid var(--acc)"></div>'),
    "corner_frame": ("Corner Frame", '<div style="position:absolute;top:16pt;left:16pt;width:40pt;height:40pt;border-top:1.4pt solid var(--acc);border-left:1.4pt solid var(--acc)"></div>'
                     '<div style="position:absolute;bottom:16pt;right:16pt;width:40pt;height:40pt;border-bottom:1.4pt solid var(--acc);border-right:1.4pt solid var(--acc)"></div>'),
    "diagonal_cut": ("Diagonal Cut", '<div style="position:absolute;top:-30pt;right:-30pt;width:60pt;height:60pt;background:var(--acc);transform:rotate(45deg)"></div>'),
    "top_band": ("Top Band", '<div style="position:absolute;top:0;left:0;right:0;height:10pt;background:var(--acc)"></div>'),
    "gradient_band": ("Gradient Band", '<div style="position:absolute;top:0;left:0;right:0;height:12pt;background:linear-gradient(90deg,var(--acc),var(--acc2))"></div>'),
    "edge_tab": ("Edge Tab", '<div style="position:absolute;top:0;left:36pt;width:70pt;height:6pt;background:var(--acc);border-radius:0 0 3pt 3pt"></div>'),
    "ribbon": ("Ribbon", '<div style="position:absolute;top:0;right:44pt;width:16pt;height:46pt;background:var(--acc);clip-path:polygon(0 0,100% 0,100% 100%,50% 78%,0 100%)"></div>'),
    "classy_lines": ("Classy Lines", '<div style="position:absolute;top:14pt;left:30pt;right:30pt;border-top:0.8pt solid var(--acc);height:2.4pt;border-bottom:0.8pt solid var(--acc)"></div>'
                     '<div style="position:absolute;bottom:14pt;left:30pt;right:30pt;border-top:0.8pt solid var(--acc);height:2.4pt;border-bottom:0.8pt solid var(--acc)"></div>'),
    "ladder_lines": ("Ladder Lines", "".join(f'<div style="position:absolute;top:{8 + i * 4}pt;left:30pt;right:30pt;height:0.6pt;background:var(--acc);opacity:{1 - i * 0.2:.1f}"></div>' for i in range(4))),
}
WATERMARKS = {
    "none": ("None", ""),
    "soft_circle": ("Soft Circle", '<div style="position:absolute;bottom:-60pt;right:-60pt;width:200pt;height:200pt;border-radius:50%;background:var(--acc)"></div>'),
    "concentric_rings": ("Concentric Rings", "".join(f'<div style="position:absolute;top:{-r // 2}pt;right:{-r // 2}pt;width:{r}pt;height:{r}pt;border-radius:50%;border:1.2pt solid var(--acc)"></div>' for r in (80, 130, 180))),
    "corner_arc": ("Corner Arc", '<div style="position:absolute;top:-90pt;right:-90pt;width:180pt;height:180pt;border-radius:50%;border:10pt solid var(--acc)"></div>'),
    "diagonal_lines": ("Diagonal Lines", '<div style="position:absolute;top:0;right:0;width:150pt;height:150pt;background:repeating-linear-gradient(45deg,var(--acc) 0 1pt,transparent 1pt 9pt);'
                       '-webkit-mask-image:linear-gradient(225deg,#000,transparent 70%);mask-image:linear-gradient(225deg,#000,transparent 70%)"></div>'),
    "dot_grid": ("Dot Grid", '<div style="position:absolute;top:24pt;right:24pt;width:110pt;height:110pt;background:radial-gradient(var(--acc) 1.3pt,transparent 1.5pt) 0 0/11pt 11pt"></div>'),
    "line_grid": ("Line Grid", '<div style="position:absolute;top:20pt;right:20pt;width:120pt;height:120pt;background:linear-gradient(var(--acc) 0.6pt,transparent 0.6pt) 0 0/12pt 12pt,'
                  'linear-gradient(90deg,var(--acc) 0.6pt,transparent 0.6pt) 0 0/12pt 12pt"></div>'),
    "dot_scatter": ("Dot Scatter", "".join(f'<div style="position:absolute;top:{t}pt;right:{r}pt;width:{s}pt;height:{s}pt;border-radius:50%;background:var(--acc)"></div>'
                                           for t, r, s in ((30, 40, 8), (60, 90, 5), (95, 30, 10), (40, 130, 4), (120, 70, 6), (150, 120, 4)))),
    "blurred": ("Blurred", '<div style="position:absolute;top:-40pt;right:-30pt;width:180pt;height:180pt;border-radius:50%;background:var(--acc);filter:blur(30pt)"></div>'
                '<div style="position:absolute;bottom:-50pt;left:-40pt;width:160pt;height:160pt;border-radius:50%;background:var(--acc);filter:blur(30pt)"></div>'),
    "half_moon": ("Half Moon", '<div style="position:absolute;bottom:-70pt;left:-70pt;width:180pt;height:180pt;border-radius:50%;background:var(--acc)"></div>'),
    "stars": ("Stars", "".join(f'<div style="position:absolute;top:{t}pt;right:{r}pt;width:{s}pt;height:{s}pt;background:var(--acc);clip-path:polygon(50% 0,61% 35%,98% 35%,68% 57%,79% 91%,50% 70%,21% 91%,32% 57%,2% 35%,39% 35%)"></div>'
                               for t, r, s in ((28, 36, 16), (64, 80, 10), (34, 110, 8)))),
}
# section heading decorations: CSS for .cp-sh.<style>
SECTION_STYLES = {
    "none": ("None", ""),
    "underline": ("Underline", "padding-bottom:2pt;border-bottom:1.2pt solid var(--acc)"),
    "filled_band": ("Filled Band", "background:var(--sbg);color:var(--slab)!important;padding:2.5pt 7pt;border-radius:2pt"),
    "solid_band": ("Solid Band", "background:var(--acc);color:#fff!important;padding:2.5pt 7pt;border-radius:2pt"),
    "pill": ("Pill", "display:inline-block;border:1pt solid var(--acc);border-radius:99pt;padding:1.5pt 10pt"),
    "boxed": ("Boxed", "border:1pt solid var(--acc);padding:2pt 7pt"),
    "side_block": ("Side Block", "border-left:4pt solid var(--acc);padding-left:6pt"),
    "double_rule": ("Double Rule", "padding-bottom:2pt;border-bottom:3pt double var(--acc)"),
    "overline": ("Overline", "padding-top:3pt;border-top:1.6pt solid var(--acc)"),
    "brackets": ("Brackets", ""),
    "dot": ("Dot", ""),
    "square": ("Square", ""),
    "numbered": ("Numbered", ""),
}
_SECTION_PSEUDO = {
    "brackets": ".cp-sh.brackets::before{content:'[ ';color:var(--acc)}.cp-sh.brackets::after{content:' ]';color:var(--acc)}",
    "dot": ".cp-sh.dot::before{content:'';display:inline-block;width:5pt;height:5pt;border-radius:50%;background:var(--acc);margin-right:6pt;vertical-align:middle}",
    "square": ".cp-sh.square::before{content:'';display:inline-block;width:5pt;height:5pt;background:var(--acc);margin-right:6pt;vertical-align:middle}",
    "numbered": ".cp-body{counter-reset:cpsec}.cp-sh.numbered::before{counter-increment:cpsec;content:counter(cpsec);display:inline-grid;place-items:center;"
                "width:12pt;height:12pt;border-radius:50%;background:var(--acc);color:#fff;font-size:7pt;margin-right:6pt;vertical-align:middle}",
}
THEMES = {
    "none": {"label": "None", "header_accent": "none", "watermark": "none", "section_style": "none"},
    "classy": {"label": "Classy", "header_accent": "classy_lines", "watermark": "none", "section_style": "underline", "accent": "#1e293b"},
    "stanford": {"label": "Stanford", "header_accent": "top_band", "watermark": "none", "section_style": "solid_band", "accent": "#1e3a5f"},
    "gradient": {"label": "Gradient", "header_accent": "gradient_band", "watermark": "none", "section_style": "filled_band", "accent": "#2a6f97"},
    "polygon": {"label": "Polygon", "header_accent": "two_tone_corner", "watermark": "none", "section_style": "brackets", "accent": "#334155"},
    "ladder": {"label": "Ladder", "header_accent": "ladder_lines", "watermark": "none", "section_style": "double_rule", "accent": "#1e3a5f"},
    "halo": {"label": "Halo", "header_accent": "none", "watermark": "soft_circle", "section_style": "pill", "accent": "#4a6fa5"},
    "fern": {"label": "Fern", "header_accent": "none", "watermark": "dot_scatter", "section_style": "dot", "accent": "#2f6b4f"},
    "amber": {"label": "Amber", "header_accent": "corner_triangle", "watermark": "none", "section_style": "numbered", "accent": "#b7791f"},
    "bookmark": {"label": "Bookmark", "header_accent": "ribbon", "watermark": "none", "section_style": "side_block", "accent": "#1e3a5f"},
    "grid": {"label": "Blueprint", "header_accent": "top_rule", "watermark": "line_grid", "section_style": "underline", "accent": "#2563eb"},
    "rhubarb": {"label": "Rhubarb", "header_accent": "none", "watermark": "half_moon", "section_style": "pill", "accent": "#7b3f61"},
}

ENUMS = {
    "align": ["left", "center", "right"],
    "page_size": ["letter", "a4"],
    "heading_rule": ["none", "solid", "double", "dotted", "dashed", "thick"],
    "skills_layout": ["category", "inline", "grid", "bullets"],
    "bullet_style": ["disc", "circle", "square", "dash", "arrow", "check", "none"],
    "contact_layout": ["single", "double", "stacked"],
    "entry_layout": ["single", "double"],
    "expertise_layout": ["inline", "bullets", "grid", "tags"],
}

# ------------------------------------------------------------------ presets (researched single-column SWE layouts)
def _p(**over):
    return over


PRESETS = {
    "jakes_classic": _p(
        label="Jake's Classic", tags=["Classic", "ATS", "Serif"],
        description="The most-used developer résumé layout: centered header, small-caps headings over a rule, title and dates on one line, company and location italic below.",
        source="Inspired by Jake's Resume (Overleaf, MIT)",
        style={"typography": {"body_font": "Source Serif 4", "heading_font": "Source Serif 4", "name_size": 24, "heading_size": 12, "body_size": 10.5, "letter_spacing": 0.3},
               "layout": {"header_align": "center", "contact_sep": "|", "heading_rule": "solid", "rule_thickness": 5, "line_spacing": 125},
               "colors": {"name": "#000000", "headings": "#000000", "body": "#111111", "accent": "#000000", "contact": "#222222", "secondary": "#333333"},
               "experience": {"elements": {"company": {"italic": True}, "location": {"visible": True, "italic": True}, "dates": {"color": "#333333"}}},
               "education": {"elements": {"institution": {"italic": True}, "location": {"visible": True}}},
               "sections": {"order_keys": ["summary", "experience", "projects", "skills", "education", "certifications", "headline", "expertise"]}}),
    "ats_classic": _p(
        label="ATS Classic", tags=["ATS", "Sans"],
        description="Calibri, left-aligned header, uppercase headings with a thin rule — the conservative format most screening systems parse perfectly.",
        source="Common recruiter / ATS guidance",
        style={}),
    "modern_minimal": _p(
        label="Modern Minimal", tags=["Modern", "Sans"],
        description="Inter with generous whitespace, accent-colored headings and no rules. Clean and current.",
        source="Standard Resume, Teal-style minimal",
        style={"typography": {"body_font": "Inter", "heading_font": "Inter", "name_size": 24, "heading_size": 11, "body_size": 10, "heading_upper": True, "letter_spacing": 0.6},
               "layout": {"heading_rule": "none", "section_spacing": 260, "line_spacing": 145},
               "colors": {"headings": "#2563eb", "accent": "#2563eb"},
               "experience": {"layout": "single"}}),
    "harvard": _p(
        label="Harvard Serif", tags=["Classic", "Serif"],
        description="Centered name, bold uppercase section headings with a rule, Times type — the career-office standard.",
        source="Harvard Office of Career Services guide",
        style={"typography": {"body_font": "Times New Roman", "heading_font": "Times New Roman", "name_size": 20, "name_upper": True, "heading_size": 11.5, "body_size": 11},
               "layout": {"header_align": "center", "heading_rule": "solid", "rule_thickness": 6},
               "colors": {"name": "#000000", "headings": "#000000", "body": "#000000", "contact": "#111111", "secondary": "#222222", "accent": "#000000"},
               "experience": {"elements": {"company": {"bold": True}, "title": {"line": 2, "bold": False, "italic": True}, "location": {"line": 1}, "dates": {"line": 2}}}}),
    "compact_engineer": _p(
        label="Compact Engineer", tags=["Compact", "Sans"],
        description="Dense one-page layout for experienced engineers: smaller type, tight spacing, single-line entries and inline skills.",
        source="Tech Interview Handbook-style one-pager",
        style={"typography": {"body_font": "Source Sans 3", "heading_font": "Source Sans 3", "name_size": 19, "heading_size": 10.5, "body_size": 9.75, "contact_size": 9, "subtext_size": 9},
               "layout": {"section_spacing": 160, "line_spacing": 125, "margins": {"top": 500, "bottom": 500, "left": 620, "right": 620}, "rule_spacing": 40},
               "experience": {"layout": "single", "bullet_gap": 20, "line_gap": 10}, "education": {"layout": "single"}}),
    "stanford": _p(
        label="Stanford", tags=["Modern", "Accent"],
        description="Navy band across the top and solid navy section labels. Confident but still single-column and ATS-safe.",
        source="Professional theme",
        style={"typography": {"body_font": "Lato", "heading_font": "Lato", "name_size": 24, "heading_size": 10.5},
               "layout": {"heading_rule": "none", "margins": {"top": 760}},
               "colors": {"name": "#1e3a5f", "headings": "#1e3a5f", "accent": "#1e3a5f"},
               "pdf": {"theme": "stanford", "header_accent": "top_band", "section_style": "solid_band"}}),
    "accent_line": _p(
        label="Accent Line", tags=["Modern", "Accent"],
        description="Light name with an accent side-block on each heading, inspired by the Awesome-CV family, in one column.",
        source="Awesome-CV inspired",
        style={"typography": {"body_font": "Open Sans", "heading_font": "Open Sans", "name_size": 26, "heading_size": 11.5, "body_size": 10},
               "layout": {"heading_rule": "none"},
               "colors": {"accent": "#dc3522", "headings": "#1f2937", "name": "#1f2937"},
               "pdf": {"section_style": "side_block"}}),
    "developer_mono": _p(
        label="Developer Mono", tags=["Tech", "Mono"],
        description="IBM Plex body with JetBrains Mono headings in brackets — a subtle engineering feel that stays readable.",
        source="Developer-focused",
        style={"typography": {"body_font": "IBM Plex Sans", "heading_font": "JetBrains Mono", "name_size": 22, "heading_size": 11, "body_size": 10, "heading_upper": False},
               "layout": {"heading_rule": "dotted", "rule_color": "#94a3b8"},
               "colors": {"accent": "#0f766e", "headings": "#0f766e"},
               "pdf": {"section_style": "brackets"}}),
    "executive_serif": _p(
        label="Executive Serif", tags=["Senior", "Serif"],
        description="Merriweather with roomy spacing and double rules, suited to staff and principal engineers.",
        source="Executive résumé convention",
        style={"typography": {"body_font": "Merriweather", "heading_font": "Merriweather", "name_size": 23, "heading_size": 11, "body_size": 9.75},
               "layout": {"header_align": "center", "heading_rule": "double", "rule_thickness": 8, "section_spacing": 280, "line_spacing": 150, "rule_color": "#7c2d12"},
               "colors": {"accent": "#7c2d12", "headings": "#7c2d12"}}),
    "clean_slate": _p(
        label="Clean Slate", tags=["Modern", "Soft"],
        description="Nunito Sans with soft tinted section labels — friendly and easy to scan.",
        source="Professional theme",
        style={"typography": {"body_font": "Nunito Sans", "heading_font": "Nunito Sans", "name_size": 24, "heading_size": 10.5},
               "layout": {"heading_rule": "none"},
               "colors": {"section_bg": "#e8eef5", "section_label": "#1e293b", "name": "#1e3a5f"},
               "pdf": {"section_style": "filled_band"}}),
    "classy": _p(
        label="Classy", tags=["Classic", "Accent"],
        description="Fine double lines at the top and bottom of every page with underlined headings.",
        source="Professional theme",
        style={"typography": {"body_font": "Lora", "heading_font": "Lora", "name_size": 24, "heading_size": 11.5},
               "layout": {"header_align": "center", "heading_rule": "none", "margins": {"top": 820, "bottom": 820}},
               "pdf": {"theme": "classy", "header_accent": "classy_lines", "section_style": "underline"}}),
    "gradient_edge": _p(
        label="Gradient Edge", tags=["Modern", "Accent"],
        description="A gradient strip across the top with tinted section bands.",
        source="Professional theme",
        style={"typography": {"body_font": "Roboto", "heading_font": "Roboto", "name_size": 24, "heading_size": 10.5},
               "layout": {"heading_rule": "none", "margins": {"top": 780}},
               "colors": {"accent": "#2a6f97", "name": "#1b4965", "section_bg": "#dbeafe", "section_label": "#1b4965"},
               "pdf": {"theme": "gradient", "header_accent": "gradient_band", "section_style": "filled_band"}}),
}
DEFAULT_TEMPLATE_ID = "ats_classic"
# old built-in HTML template ids -> closest new preset (keeps existing documents and defaults working)
LEGACY_ALIASES = {
    "classic": "ats_classic", "classic_jobscan_ats_1": "ats_classic", "modern_minimal": "modern_minimal", "harvard": "harvard",
    "academic_serif": "harvard", "elegant": "executive_serif", "executive_bold": "executive_serif", "compact_technical": "compact_engineer",
    "tech_startup": "modern_minimal", "creative_clean": "clean_slate", "corporate_band": "stanford", "sidebar_prime": "accent_line",
    "modern_left": "accent_line", "stockholm": "modern_minimal", "dublin": "clean_slate", "entry_level": "ats_classic",
    "skills_forward": "compact_engineer", "sales_marketing": "clean_slate", "healthcare_professional": "ats_classic",
    "legal_professional": "harvard", "federal_government": "ats_classic",
}


# ------------------------------------------------------------------ style helpers

def deep_merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def preset_style(preset_id):
    pid = LEGACY_ALIASES.get(preset_id, preset_id)
    preset = PRESETS.get(pid) or PRESETS[DEFAULT_TEMPLATE_ID]
    over = copy.deepcopy(preset.get("style") or {})
    order_keys = (over.get("sections") or {}).pop("order_keys", None)
    style = deep_merge(DEFAULT_STYLE, over)
    if order_keys:
        by = {s["key"]: s for s in style["sections"]["order"]}
        style["sections"]["order"] = [by[k] for k in order_keys if k in by]
    # contact item colors follow the contact color unless set explicitly
    for item in style["contact"]["items"].values():
        if item.get("color") == DEFAULT_STYLE["contact"]["items"]["email"]["color"]:
            item["color"] = style["colors"]["contact"]
    return style


def sanitize_style(style):
    """Merge a user-supplied style onto defaults and clamp every value to a safe range/type."""
    s = deep_merge(DEFAULT_STYLE, style if isinstance(style, dict) else {})

    def num(v, lo, hi, d):
        try:
            return max(lo, min(hi, float(v)))
        except (TypeError, ValueError):
            return d

    def color(v, d):
        v = str(v or "").strip()
        if re.fullmatch(r"[0-9a-fA-F]{6}", v):
            v = "#" + v
        return v.lower() if re.fullmatch(r"#[0-9a-fA-F]{6}", v) else d

    def choice(v, options, d):
        return v if v in options else d

    t, L, c = s["typography"], s["layout"], s["colors"]
    for k in ("body_font", "heading_font"):
        t[k] = choice(t[k], FONTS, "Calibri")
    for k, lo, hi in (("name_size", 12, 40), ("heading_size", 8, 20), ("body_size", 7, 14), ("contact_size", 7, 14), ("subtext_size", 7, 14)):
        t[k] = num(t[k], lo, hi, DEFAULT_STYLE["typography"][k])
    t["letter_spacing"] = num(t["letter_spacing"], -1, 5, 0)
    t["name_upper"], t["heading_upper"] = bool(t["name_upper"]), bool(t["heading_upper"])
    L["header_align"] = choice(L["header_align"], ENUMS["align"], "left")
    L["contact_sep"] = str(L.get("contact_sep") or "·")[:4]
    L["line_spacing"] = num(L["line_spacing"], 90, 220, 135)
    for k, hi in (("section_spacing", 800), ("name_contact_gap", 600), ("rule_spacing", 400)):
        L[k] = num(L[k], 0, hi, DEFAULT_STYLE["layout"][k])
    L["page_size"] = choice(L["page_size"], ENUMS["page_size"], "letter")
    for k in ("top", "bottom", "left", "right"):
        L["margins"][k] = num(L["margins"].get(k), 200, 2000, 720)
    L["heading_rule"] = choice(L["heading_rule"], ENUMS["heading_rule"], "solid")
    L["rule_color"] = color(L["rule_color"], "#1e293b")
    L["rule_thickness"] = num(L["rule_thickness"], 1, 32, 6)
    L["skills_layout"] = choice(L["skills_layout"], ENUMS["skills_layout"], "category")
    L["bullet_style"] = choice(L["bullet_style"], ENUMS["bullet_style"], "disc")
    for k in DEFAULT_STYLE["colors"]:
        c[k] = color(c.get(k), DEFAULT_STYLE["colors"][k])
    s["contact"]["layout"] = choice(s["contact"]["layout"], ENUMS["contact_layout"], "single")
    s["contact"]["show_title"] = bool(s["contact"]["show_title"])
    for k, item in s["contact"]["items"].items():
        item["visible"] = bool(item.get("visible", True))
        item["align"] = choice(item.get("align"), ["left", "right"], "left")
        item["color"] = color(item.get("color"), c["contact"])
    for sec in ("experience", "education", "certifications", "projects"):
        block = s[sec]
        block["layout"] = choice(block.get("layout"), ENUMS["entry_layout"], "double")
        block["line_gap"] = num(block.get("line_gap"), 0, 400, 30)
        if "bullet_gap" in block:
            block["bullet_gap"] = num(block.get("bullet_gap"), 0, 400, 40)
        block["separator"] = str(block.get("separator") or " · ")[:6]
        for name, el in block["elements"].items():
            d = DEFAULT_STYLE[sec]["elements"].get(name, _el())
            el["visible"], el["bold"], el["italic"] = bool(el.get("visible", d["visible"])), bool(el.get("bold")), bool(el.get("italic"))
            el["line"] = 1 if str(el.get("line")) == "1" else 2
            el["align"] = choice(el.get("align"), ["left", "right"], d["align"])
            el["color"] = color(el.get("color"), d["color"])
    hl = s["headline"]
    hl["size"] = "auto" if str(hl.get("size", "auto")).lower() in ("", "auto") else num(hl["size"], 8, 24, 12)
    hl["top_gap"], hl["bottom_gap"] = num(hl["top_gap"], 0, 600, 0), num(hl["bottom_gap"], 0, 600, 100)
    hl["color"], hl["bold"], hl["italic"] = color(hl["color"], "#334155"), bool(hl["bold"]), bool(hl["italic"])
    hl["align"] = choice(hl["align"], ENUMS["align"], "left")
    s["expertise"]["layout"] = choice(s["expertise"]["layout"], ENUMS["expertise_layout"], "inline")
    s["sections"]["align"] = choice(s["sections"]["align"], ENUMS["align"], "left")
    seen, order = set(), []
    for item in s["sections"]["order"] if isinstance(s["sections"]["order"], list) else []:
        if isinstance(item, dict) and item.get("key") in SECTION_TITLES and item["key"] not in seen:
            seen.add(item["key"])
            order.append({"key": item["key"], "title": str(item.get("title") or SECTION_TITLES[item["key"]])[:60], "visible": bool(item.get("visible", True))})
    order += [{"key": k, "title": SECTION_TITLES[k], "visible": False} for k in SECTION_KEYS if k not in seen]
    s["sections"]["order"] = order
    p = s["pdf"]
    p["theme"] = choice(p.get("theme"), THEMES, "none")
    p["header_accent"] = choice(p.get("header_accent"), ACCENTS, "none")
    p["watermark"] = choice(p.get("watermark"), WATERMARKS, "none")
    p["section_style"] = choice(p.get("section_style"), SECTION_STYLES, "none")
    p["watermark_strength"] = num(p.get("watermark_strength"), 2, 40, 12)
    return s


def options():
    return {
        "fonts": list(FONTS), "enums": ENUMS, "section_titles": SECTION_TITLES,
        "accents": [{"id": k, "label": v[0], "html": v[1]} for k, v in ACCENTS.items()],
        "watermarks": [{"id": k, "label": v[0], "html": v[1]} for k, v in WATERMARKS.items()],
        "section_styles": [{"id": k, "label": v[0]} for k, v in SECTION_STYLES.items()],
        "section_css": section_css(),
        "themes": [{"id": k, **v} for k, v in THEMES.items()],
    }


def section_css():
    css = "".join(f".cp-sh.{k}{{{v[1]}}}" for k, v in SECTION_STYLES.items() if v[1])
    return css + "".join(_SECTION_PSEUDO.values())


# ------------------------------------------------------------------ rendering

def _pt(twips):
    return f"{float(twips) / 20:.2f}pt"


def _font(name):
    return FONTS.get(name, FONTS["Calibri"])[0]


def _font_link(style):
    """Self-hosted fonts (web/assets/fonts) - no third-party requests, so previews never wait on Google."""
    pkgs = sorted({FONTS[f][1] for f in (style["typography"]["body_font"], style["typography"]["heading_font"]) if FONTS.get(f) and FONTS[f][1]})
    return "".join(f'<link rel="stylesheet" href="/assets/fonts/{p}.css">' for p in pkgs)


def _lighten(hex_color, amount=0.55):
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    r, g, b = (int(x + (255 - x) * amount) for x in (r, g, b))
    return f"#{r:02x}{g:02x}{b:02x}"


def classify_contact(items):
    out = []
    for raw in items or []:
        v = str(raw or "").strip()
        if not v:
            continue
        low = v.lower()
        if "@" in v and " " not in v:
            kind = "email"
        elif "linkedin.com" in low:
            kind = "linkedin"
        elif re.search(r"(https?://|www\.|\.(com|io|dev|me|ai|org|net|app)(/|$))", low):
            kind = "links"
        elif len(re.sub(r"\D", "", v)) >= 7 and re.fullmatch(r"[\d\s()+.\-x]+", v):
            kind = "phone"
        else:
            kind = "location"
        out.append((kind, v))
    return out


def _esc(v):
    return H.escape(str(v or ""))


def _entry(block, values, sep):
    """Render one entry's meta lines from element settings."""
    els = block["elements"]
    lines = {1: {"left": [], "right": []}, 2: {"left": [], "right": []}}
    single = block["layout"] == "single"
    for name, el in els.items():
        val = values.get(name)
        if not el["visible"] or not str(val or "").strip():
            continue
        style = f"color:{el['color']};font-weight:{700 if el['bold'] else 400};font-style:{'italic' if el['italic'] else 'normal'}"
        line = 1 if single else el["line"]
        lines[line][el["align"]].append(f'<span style="{style}">{_esc(val)}</span>')
    out = []
    for n in (1, 2):
        left, right = lines[n]["left"], lines[n]["right"]
        if not left and not right:
            continue
        joiner = f'<span class="cp-sep">{_esc(sep)}</span>'
        gap = f"margin-top:{_pt(block['line_gap'])}" if n == 2 and out else ""
        out.append(f'<div class="cp-eline" style="{gap}"><span class="cp-l">{joiner.join(left)}</span><span class="cp-r">{joiner.join(right)}</span></div>')
    return "".join(out)


def _bullets(items, block):
    items = [b for b in items if str(b).strip()]
    if not items:
        return ""
    gap = _pt(block.get("bullet_gap", 40))
    return '<ul class="cp-ul">' + "".join(f'<li style="margin-top:{gap}">{_esc(b)}</li>' for b in items) + "</ul>"


def render_resume_styled(data, profile, style, role_mode="fixed", show_title=True):
    s = sanitize_style(style)
    data = normalize_resume(data)
    t, L, c, P = s["typography"], s["layout"], s["colors"], s["pdf"]
    acc = c["accent"]
    acc2 = _lighten(acc, 0.55)
    wm_opacity = P["watermark_strength"] / 100

    # ---------------- header
    name = _esc(profile.get("name") or "")
    title_line = ""
    if s["contact"]["show_title"] and show_title:
        head = headline_for(profile, data, role_mode)
        if head:
            title_line = f'<div class="cp-title">{_esc(head)}</div>'
    contact_items = [(k, v) for k, v in classify_contact(profile.get("contact")) if s["contact"]["items"].get(k, {}).get("visible", True)]

    def citem(k, v):
        return f'<span style="color:{s["contact"]["items"].get(k, {}).get("color", c["contact"])}">{_esc(v)}</span>'

    sep = f'<span class="cp-csep">{_esc(L["contact_sep"])}</span>'
    if s["contact"]["layout"] == "stacked":
        contact_html = "".join(f"<div>{citem(k, v)}</div>" for k, v in contact_items)
    elif s["contact"]["layout"] == "double":
        left = [citem(k, v) for k, v in contact_items if s["contact"]["items"].get(k, {}).get("align") != "right"]
        right = [citem(k, v) for k, v in contact_items if s["contact"]["items"].get(k, {}).get("align") == "right"]
        if not right and len(left) > 1:
            half = (len(left) + 1) // 2
            left, right = left[:half], left[half:]
        contact_html = f'<div class="cp-eline"><span class="cp-l">{sep.join(left)}</span><span class="cp-r">{sep.join(right)}</span></div>'
    else:
        contact_html = sep.join(citem(k, v) for k, v in contact_items)
    header = (f'<header class="cp-header"><div class="cp-name">{name}</div>{title_line}'
              f'<div class="cp-contact" style="margin-top:{_pt(L["name_contact_gap"])}">{contact_html}</div></header>')

    # ---------------- sections
    jobs = profile.get("jobs") or []

    def sec_summary():
        return f'<p class="cp-p">{_esc(data["summary"])}</p>' if data["summary"] else ""

    def sec_experience():
        out = []
        for i, exp in enumerate(data["experiences"]):
            meta = jobs[i] if i < len(jobs) else {}
            vals = {"title": _role(meta, exp, role_mode), "company": meta.get("company"), "location": meta.get("location"),
                    "dates": meta.get("dates"), "job_type": meta.get("work_method")}
            out.append(f'<div class="cp-entry">{_entry(s["experience"], vals, s["experience"]["separator"])}{_bullets(exp["bullets"], s["experience"])}</div>')
        return "".join(out)

    def sec_skills():
        rows = [(k, v) for e in data["skills"] for k, v in e.items()]
        if not rows:
            return ""
        lay = L["skills_layout"]
        if lay == "inline":
            return f'<p class="cp-p">{_esc(", ".join(v for _k, v in rows))}</p>'
        if lay == "grid":
            return '<div class="cp-grid2">' + "".join(f'<div><b class="cp-skk">{_esc(k)}</b><div>{_esc(v)}</div></div>' for k, v in rows) + "</div>"
        if lay == "bullets":
            return '<ul class="cp-ul">' + "".join(f'<li><b class="cp-skk">{_esc(k)}:</b> {_esc(v)}</li>' for k, v in rows) + "</ul>"
        return "".join(f'<div class="cp-skrow"><b class="cp-skk">{_esc(k)}:</b> {_esc(v)}</div>' for k, v in rows)

    def sec_education():
        out = []
        for e in profile.get("education") or []:
            vals = {"degree": e.get("degree"), "institution": e.get("school"), "location": e.get("location"), "dates": e.get("dates")}
            out.append(f'<div class="cp-entry">{_entry(s["education"], vals, s["education"]["separator"])}</div>')
        return "".join(out)

    def sec_certifications():
        out = []
        for e in profile.get("certifications") or []:
            if not (e.get("name") or "").strip():
                continue
            vals = {"name": e.get("name"), "issuer": e.get("issuer"), "date": e.get("dates")}
            out.append(f'<div class="cp-entry">{_entry(s["certifications"], vals, s["certifications"]["separator"])}</div>')
        return "".join(out)

    def sec_headline():
        text = data.get("headline") or profile.get("headline") or ""
        if not text:
            return ""
        hl = s["headline"]
        size = f"{hl['size']}pt" if hl["size"] != "auto" else f"{t['body_size'] + 1.5}pt"
        return (f'<p class="cp-p" style="font-size:{size};color:{hl["color"]};font-weight:{700 if hl["bold"] else 400};'
                f'font-style:{"italic" if hl["italic"] else "normal"};text-align:{hl["align"]};margin:{_pt(hl["top_gap"])} 0 {_pt(hl["bottom_gap"])}">{_esc(text)}</p>')

    def sec_expertise():
        items = data.get("expertise") or [k for e in data["skills"] for k in e.keys()]
        items = [str(x).strip() for x in items if str(x).strip()]
        if not items:
            return ""
        lay = s["expertise"]["layout"]
        if lay == "bullets":
            return '<ul class="cp-ul">' + "".join(f"<li>{_esc(x)}</li>" for x in items) + "</ul>"
        if lay == "grid":
            return '<div class="cp-grid3">' + "".join(f"<div>• {_esc(x)}</div>" for x in items) + "</div>"
        if lay == "tags":
            return '<div class="cp-tags">' + "".join(f'<span class="cp-tag">{_esc(x)}</span>' for x in items) + "</div>"
        return f'<p class="cp-p">{f" <span class=cp-sep>·</span> ".join(_esc(x) for x in items)}</p>'

    def sec_projects():
        out = []
        for pj in data.get("projects") or []:
            if not isinstance(pj, dict) or not (pj.get("name") or "").strip():
                continue
            vals = {"name": pj.get("name"), "link": pj.get("link") or pj.get("url"), "tech": pj.get("tech") or pj.get("technologies"), "dates": pj.get("dates")}
            desc = f'<p class="cp-p" style="margin-top:2pt">{_esc(pj.get("description"))}</p>' if pj.get("description") else ""
            out.append(f'<div class="cp-entry">{_entry(s["projects"], vals, s["projects"]["separator"])}{desc}{_bullets(pj.get("bullets") or [], s["projects"])}</div>')
        return "".join(out)

    builders = {"summary": sec_summary, "experience": sec_experience, "skills": sec_skills, "education": sec_education,
                "certifications": sec_certifications, "headline": sec_headline, "expertise": sec_expertise, "projects": sec_projects}
    sections = []
    for item in s["sections"]["order"]:
        if not item["visible"]:
            continue
        body = builders[item["key"]]()
        if not body:
            continue
        sections.append(f'<section class="cp-sec cp-{item["key"]}"><h2 class="cp-sh {P["section_style"]}">{_esc(item["title"])}</h2>{body}</section>')

    # ---------------- css
    rule_px = L["rule_thickness"] / 8
    rule = {"none": "none", "solid": f"{rule_px}pt solid", "double": f"{max(rule_px * 2.5, 2.5)}pt double",
            "dotted": f"{max(rule_px, 1)}pt dotted", "dashed": f"{max(rule_px, 1)}pt dashed", "thick": f"{rule_px * 2.5}pt solid"}[L["heading_rule"]]
    bullet = {"disc": "disc", "circle": "circle", "square": "square", "none": "none"}.get(L["bullet_style"])
    bullet_marker = {"dash": "'–  '", "arrow": "'›  '", "check": "'✓  '"}.get(L["bullet_style"])
    page_w, page_h = ("8.5in", "11in") if L["page_size"] == "letter" else ("210mm", "297mm")
    m = L["margins"]
    align_sec = s["sections"]["align"]
    css = f"""
@page {{ size: {page_w} {page_h}; margin: 0 }}
* {{ box-sizing: border-box }}
html, body {{ margin: 0; padding: 0; background: #fff }}
body {{ font-family: {_font(t['body_font'])}; font-size: {t['body_size']}pt; color: {c['body']}; line-height: {L['line_spacing'] / 100:.2f};
       -webkit-print-color-adjust: exact; print-color-adjust: exact }}
.cp-page {{ position: relative; width: {page_w}; min-height: {page_h}; margin: 0 auto; background: #fff; overflow: hidden;
           --acc: {acc}; --acc2: {acc2}; --sbg: {c['section_bg']}; --slab: {c['section_label']} }}
.cp-deco {{ position: absolute; inset: 0; pointer-events: none; z-index: 0 }}
.cp-wm {{ opacity: {wm_opacity:.2f} }}
table.cp-frame {{ width: 100%; border-collapse: collapse; position: relative; z-index: 1 }}
table.cp-frame td {{ padding: 0 {_pt(m['right'])} 0 {_pt(m['left'])} }}
.cp-header {{ text-align: {L['header_align']} }}
.cp-name {{ font-family: {_font(t['heading_font'])}; font-size: {t['name_size']}pt; font-weight: 700; color: {c['name']}; line-height: 1.1;
           text-transform: {'uppercase' if t['name_upper'] else 'none'}; letter-spacing: {t['letter_spacing'] + (0.6 if t['name_upper'] else 0)}pt }}
.cp-title {{ font-size: {t['body_size'] + 1}pt; color: {c['secondary']}; margin-top: 2pt }}
.cp-contact {{ font-size: {t['contact_size']}pt; color: {c['contact']} }}
.cp-csep {{ margin: 0 5pt; color: {c['secondary']} }}
.cp-sec {{ margin-top: {_pt(L['section_spacing'])}; text-align: left }}
.cp-sh {{ font-family: {_font(t['heading_font'])}; font-size: {t['heading_size']}pt; font-weight: 700; color: {c['headings']}; margin: 0 0 {_pt(L['rule_spacing'] + 40)};
         text-transform: {'uppercase' if t['heading_upper'] else 'none'}; letter-spacing: {t['letter_spacing'] + (0.8 if t['heading_upper'] else 0)}pt;
         text-align: {align_sec}; line-height: 1.25; break-after: avoid; page-break-after: avoid }}
.cp-sh.none, .cp-sh.underline, .cp-sh.double_rule, .cp-sh.overline, .cp-sh.dot, .cp-sh.square, .cp-sh.numbered, .cp-sh.brackets, .cp-sh.side_block {{
         {'' if rule == 'none' else f'padding-bottom: {_pt(L["rule_spacing"])}; border-bottom: {rule} {L["rule_color"]};'} }}
.cp-sh.pill, .cp-sh.boxed {{ {'display:block;' if align_sec == 'left' else ''} width: fit-content; {'margin-left:auto;margin-right:auto;' if align_sec == 'center' else 'margin-left:auto;' if align_sec == 'right' else ''} }}
{section_css()}
.cp-p {{ margin: 0 }}
.cp-entry {{ margin-top: {_pt(L['section_spacing'] * 0.45)}; break-inside: avoid-page }}
.cp-entry:first-of-type {{ margin-top: 0 }}
.cp-eline {{ display: flex; justify-content: space-between; gap: 12pt; align-items: baseline }}
.cp-l {{ min-width: 0 }}
.cp-r {{ text-align: right; white-space: nowrap; font-size: {t['subtext_size']}pt }}
.cp-sep {{ color: {c['secondary']} }}
.cp-ul {{ margin: 2pt 0 0; padding-left: {'0' if bullet_marker or bullet == 'none' else '14pt'}; list-style: {bullet or 'none'} }}
{f".cp-ul li {{ list-style: none; padding-left: 12pt; text-indent: -12pt }} .cp-ul li::before {{ content: {bullet_marker}; color: {acc} }}" if bullet_marker else ''}
.cp-ul li::marker {{ color: {c['secondary']} }}
.cp-skrow {{ margin-top: 1.5pt }}
.cp-skk {{ color: {c['headings'] if P['section_style'] not in ('solid_band',) else c['body']} }}
.cp-grid2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 3pt 16pt }}
.cp-grid3 {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 2pt 12pt }}
.cp-tags {{ display: flex; flex-wrap: wrap; gap: 4pt }}
.cp-tag {{ border: 0.8pt solid {acc}; border-radius: 99pt; padding: 0 7pt; font-size: {t['subtext_size']}pt }}
@media screen {{ body {{ background: #fff }} }}
@media print {{ .cp-deco {{ position: fixed }} .cp-page {{ overflow: visible; min-height: 0 }} }}
"""
    deco = (f'<div class="cp-deco" aria-hidden="true">{ACCENTS[P["header_accent"]][1]}</div>' if P["header_accent"] != "none" else "") + \
           (f'<div class="cp-deco cp-wm" aria-hidden="true">{WATERMARKS[P["watermark"]][1]}</div>' if P["watermark"] != "none" else "")
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><title>{name} — Résumé</title>
<meta name="viewport" content="width=device-width, initial-scale=1">{_font_link(s)}<style>{css}</style></head>
<body><div class="cp-page">{deco}<table class="cp-frame" role="presentation">
<thead><tr><td><div style="height:{_pt(m['top'])}"></div></td></tr></thead>
<tfoot><tr><td><div style="height:{_pt(m['bottom'])}"></div></td></tr></tfoot>
<tbody><tr><td class="cp-body">{header}{''.join(sections)}</td></tr></tbody></table></div></body></html>"""


def legacy_template_dict(style):
    """Fonts/colors for cover letters and answers, which use the simple letter layout."""
    s = sanitize_style(style)
    return {"font_family": _font(s["typography"]["body_font"]), "text_color": s["colors"]["body"],
            "muted_color": s["colors"]["secondary"], "accent_color": s["colors"]["accent"], "_font_link": _font_link(s)}
