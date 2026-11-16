"""Download the web fonts CareerPilot uses into web/assets/fonts so nothing depends on Google Fonts.

Fonts come from the open-source Fontsource packages on jsDelivr (SIL Open Font License).
Run once (or again to refresh):  python tools/fetch_fonts.py
"""

import os
import sys

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "web", "assets", "fonts")
CDN = "https://cdn.jsdelivr.net/npm/@fontsource/{pkg}@5/files/{pkg}-{subset}-{weight}-{style}.woff2"
SUBSETS = [("latin", "U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, U+0308, U+0329, U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD"),
           ("latin-ext", "U+0100-02BA, U+02BD-02C5, U+02C7-02CC, U+02CE-02D7, U+02DD-02FF, U+0304, U+0308, U+0329, U+1D00-1DBF, U+1E00-1E9F, U+1EF2-1EFF, U+2020, U+20A0-20AB, U+20AD-20C0, U+2113, U+2C60-2C7F, U+A720-A7FF")]

# family name -> (fontsource package, [(weight, style), ...])
FAMILIES = {
    "Inter": ("inter", [(400, "normal"), (500, "normal"), (600, "normal"), (700, "normal"), (400, "italic")]),
    "Roboto": ("roboto", [(400, "normal"), (500, "normal"), (700, "normal"), (400, "italic")]),
    "Open Sans": ("open-sans", [(400, "normal"), (600, "normal"), (700, "normal"), (400, "italic")]),
    "Lato": ("lato", [(400, "normal"), (700, "normal"), (400, "italic")]),
    "Source Sans 3": ("source-sans-3", [(400, "normal"), (600, "normal"), (700, "normal"), (400, "italic")]),
    "IBM Plex Sans": ("ibm-plex-sans", [(400, "normal"), (500, "normal"), (600, "normal"), (700, "normal"), (400, "italic")]),
    "Nunito Sans": ("nunito-sans", [(400, "normal"), (600, "normal"), (700, "normal"), (400, "italic")]),
    "Merriweather": ("merriweather", [(400, "normal"), (700, "normal"), (400, "italic")]),
    "Lora": ("lora", [(400, "normal"), (600, "normal"), (700, "normal"), (400, "italic")]),
    "Source Serif 4": ("source-serif-4", [(400, "normal"), (600, "normal"), (700, "normal"), (400, "italic")]),
    "Libre Baskerville": ("libre-baskerville", [(400, "normal"), (700, "normal"), (400, "italic")]),
    "JetBrains Mono": ("jetbrains-mono", [(400, "normal"), (600, "normal"), (700, "normal"), (400, "italic")]),
    "EB Garamond": ("eb-garamond", [(400, "normal"), (600, "normal"), (700, "normal"), (400, "italic")]),
    "Plus Jakarta Sans": ("plus-jakarta-sans", [(400, "normal"), (500, "normal"), (600, "normal"), (700, "normal"), (800, "normal")]),  # app UI
}


def main():
    os.makedirs(OUT, exist_ok=True)
    client = httpx.Client(timeout=30, follow_redirects=True)
    failed = []
    for family, (pkg, variants) in FAMILIES.items():
        folder = os.path.join(OUT, pkg)
        os.makedirs(folder, exist_ok=True)
        css = []
        for weight, style in variants:
            for subset, urange in SUBSETS:
                fname = f"{pkg}-{subset}-{weight}-{style}.woff2"
                path = os.path.join(folder, fname)
                if not os.path.isfile(path):
                    r = client.get(CDN.format(pkg=pkg, subset=subset, weight=weight, style=style))
                    if r.status_code != 200 or not r.content.startswith(b"wOF2"):
                        failed.append(fname)
                        continue
                    with open(path, "wb") as fh:
                        fh.write(r.content)
                css.append(f"@font-face{{font-family:'{family}';font-style:{style};font-weight:{weight};font-display:swap;"
                           f"src:url('{pkg}/{fname}') format('woff2');unicode-range:{urange}}}")
        with open(os.path.join(OUT, f"{pkg}.css"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(css) + "\n")
        print(f"{family:20} {len(css)} faces")
    if failed:
        print("Not available:", ", ".join(failed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
