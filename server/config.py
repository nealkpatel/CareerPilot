"""CareerPilot configuration. Override any value with an environment variable."""

import os

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_env_file():
    """Read KEY=VALUE lines from a .env file in the project folder (deployment settings; see README → Run it on the
    internet). Real environment variables win over the file."""
    try:
        with open(os.path.join(ROOT_DIR, ".env"), encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    except OSError:
        pass


_load_env_file()
DATA_DIR = os.environ.get("CAREERPILOT_DATA", os.path.join(ROOT_DIR, "data"))
DB_PATH = os.path.join(DATA_DIR, "careerpilot.db")
TEMPLATES_DIR = os.path.join(ROOT_DIR, "data", "templates")  # built-in résumé designs
FILES_DIR = os.path.join(DATA_DIR, "files")
WEB_DIR = os.path.join(ROOT_DIR, "web")

HOST = os.environ.get("CAREERPILOT_HOST", "127.0.0.1")
PORT = int(os.environ.get("CAREERPILOT_PORT", "8000"))

# The address people open when CareerPilot is published through Netlify (README → Run it on the internet),
# e.g. https://careerpilot.netlify.app. Used for share links. Leave empty when people open this server directly.
PUBLIC_URL = os.environ.get("CAREERPILOT_PUBLIC_URL", "").strip().rstrip("/")
# Google Search Console "HTML tag" verification code (the content="…" value); rendered into the front page when set.
GOOGLE_SITE_VERIFICATION = os.environ.get("CAREERPILOT_GOOGLE_SITE_VERIFICATION", "").strip()
# Session cookies are HTTPS-only when the public address is HTTPS (override with CAREERPILOT_SECURE_COOKIES=0/1).
SECURE_COOKIES = os.environ.get("CAREERPILOT_SECURE_COOKIES", "1" if PUBLIC_URL.startswith("https://") else "0") == "1"

# Session cookie lifetime (days).
SESSION_DAYS = int(os.environ.get("CAREERPILOT_SESSION_DAYS", "14"))
# Public resume share links expire after this many days.
SHARE_DAYS = int(os.environ.get("CAREERPILOT_SHARE_DAYS", "30"))

# ------------------------------------------------------------------
# DeepSeek web automation (no API key: the engine drives chat.deepseek.com
# in a real Chromium window with a persistent, logged-in profile).
# ------------------------------------------------------------------
DEEPSEEK_URL = os.environ.get("CAREERPILOT_DEEPSEEK_URL", "https://chat.deepseek.com")
# Each user connects their own DeepSeek account. Their sign-in lives in their own Chrome profile under this folder.
BROWSER_PROFILE_DIR = os.environ.get(
    "CAREERPILOT_BROWSER_PROFILE",
    os.path.join(os.path.expanduser("~"), ".careerpilot_browser_profile"),
)


def user_browser_profile(user_id):
    """Chrome profile holding one user's DeepSeek sign-in."""
    return os.path.join(BROWSER_PROFILE_DIR, "users", f"u{int(user_id)}")


CHROME_EXECUTABLE = os.environ.get(
    "CAREERPILOT_CHROME", r"C:\Program Files\Google\Chrome\Application\chrome.exe"
)
# Seconds to wait for one reply / for a DeepSeek sign-in to be finished.
RESPONSE_TIMEOUT = int(os.environ.get("CAREERPILOT_RESPONSE_TIMEOUT", "240"))
LOGIN_WAIT_TIMEOUT = int(os.environ.get("CAREERPILOT_LOGIN_TIMEOUT", "900"))
# Close the scraping browser after this many idle seconds.
ENGINE_IDLE_SECONDS = int(os.environ.get("CAREERPILOT_ENGINE_IDLE", "600"))

# Outbound HTTP for job scraping.
SCRAPE_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36 CareerPilot/1.0"
)

for _d in (DATA_DIR, TEMPLATES_DIR, FILES_DIR):
    os.makedirs(_d, exist_ok=True)
