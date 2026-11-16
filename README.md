# CareerPilot

**Helping professionals navigate their careers.**

CareerPilot is a multi-user web app for tailoring résumés, cover letters and application answers to each job, then tracking every application from saved to offer.

It does what the AnchorProposal platform does, with one big difference: **no DeepSeek API key.** The AI runs by automating a signed-in session at `chat.deepseek.com` in a real Chrome window, and **every admin and bidder connects their own DeepSeek account** from inside CareerPilot. Job postings and the job pool are also scraped from public sources.

## Features

| Area | What you get |
| --- | --- |
| **Dashboard** | KPIs (applications, applied, interviews, offers, response rate), 30-day activity chart, pipeline funnel, recent work, onboarding checklist, engine status |
| **Applications** | Table or board view, status filters with counts, search, per-profile filter. Status pipeline: Saved → Ready → Applied → Screening → Interview → Offer / Rejected / Withdrawn, with a timeline |
| **Job-link import** | Paste a link: Greenhouse, Lever and Ashby are read through their public JSON; other pages through schema.org `JobPosting` data, then page text, then a headless-browser render for JavaScript-only pages |
| **Warnings** | Duplicate company / job-link detection, plus configurable keyword rules (clearance, citizenship, onsite/hybrid, travel…) that warn, ask for confirmation, or block |
| **AI generation** | Queued, one at a time, with live progress. Each reply is validated (JSON, experience count, bullet depth) and retried in a new chat up to 3 times |
| **Templates** | 12 single-column, ATS-friendly presets researched from the layouts software engineers use most (Jake's Classic, ATS Classic, Modern Minimal, Harvard Serif, Compact Engineer, Stanford, Accent Line, Developer Mono, Executive Serif, Clean Slate, Classy, Gradient Edge). A detailed style editor with live preview covers Typography, Layout, Colors, Contact, Experience, Education, Certifications, Headline, Expertise, Projects, Sections and PDF Style (12 themes, 14 header accents, 11 watermarks, 13 section styles). Styles are saved per user; HTML templates can still be uploaded |
| **Documents** | Live preview, version history, switch templates without regenerating, download **PDF / Word / Text**, copy text. Word exports follow the style's fonts, colors and section order (decorations are PDF-only) |
| **Editor** | Edit summary, skills, bullets, role titles, cover letter and answers. Saves a new version without calling the AI |
| **Application answers** | Questions are answered from the tailored résumé + cover letter, automatically after generation or on demand |
| **Share links** | Public résumé link (no cover letter or application data), expires after 30 days, revocable, view counter |
| **Job Pool** | Imports from Remote OK, Remotive, Jobicy, Himalayas, We Work Remotely, and any Greenhouse / Lever / Ashby board. Search, posted-within filter, remote filter, save / applied / hide, one-click "Tailor résumé" |
| **Profiles** | Fixed facts: name, contact, work history (with notes for the AI), education, certifications, background notes, default prompt and template |
| **Prompts** | Private prompt library per user with placeholders, a question-answer prompt, and a "preview full prompt" button. The output format rules are always appended for you |
| **Your DeepSeek** | Each admin and bidder connects their own DeepSeek account in **Settings → My DeepSeek account**: a normal Chrome window opens on the computer running CareerPilot, on that user's own browser profile, and CareerPilot shows it to them live in their own browser: they sign in there (clicks, drags and typing are replayed as real mouse and keyboard input on the server, with nothing attached to Chrome, so DeepSeek's captcha behaves normally) and click Done. CareerPilot then checks that the sign-in worked. Your requests run with your account, in your own queue, side by side with everyone else's. Reconnect, switch account or disconnect any time |
| **Master tools** | Platform overview, users and teams, who has connected a DeepSeek account, job sources & import schedule, warning rules, sign-up mode, demo/live mode, audit log |
| **UI** | Light and dark themes, works on phones, keyboard shortcut Ctrl+Enter to save and tailor |

## Roles

| Role | Who | Can do |
| --- | --- | --- |
| **Master** | The platform manager. The first account created becomes the master. | Manages every user (create, approve, change role, move bidders between admins, reset passwords, suspend, delete), sees which users have connected their DeepSeek account (never their account details), demo/live mode, job sources and import schedule, warning rules, sign-up mode, and the audit log. Sees platform-wide stats. **Does not bid.** |
| **Admin** | A team lead. Self sign-ups become admins and stay pending until the master approves them. | Bids with their own DeepSeek account. Owns candidate profiles, prompts and templates. Adds and manages **their own bidders** (create accounts, reset passwords, suspend, delete), assigns profiles to them, and sees their bidders' applications and documents. |
| **Bidder** | A team member, created by their admin (or by the master). | Bids with their own DeepSeek account and the profiles their admin assigned (read-only), using the admin's prompts. Sees only their own applications. |

**Your own DeepSeek account.** Every admin and bidder connects their own DeepSeek account; nobody shares one. Each user's sign-in is kept in their own Chrome profile on the server (`~/.careerpilot_browser_profile/users/u<id>`), and only that user's requests use it. Each user has their own queue, so requests from different people run at the same time. When an admin generates for a bidder's application, the admin's own account is used. If someone's DeepSeek sign-in expires, only their requests wait, and a banner asks them to reconnect; the work resumes automatically afterwards.

## Requirements

- Windows 10/11 (the engine's hidden-window handling is Windows specific)
- Python 3.11+ (tested with 3.14)
- Google Chrome or Microsoft Edge installed (or Playwright's Chromium)

## Install and run

```bat
install_windows.bat
start_careerpilot.bat
```

Or manually:

```bash
python -m pip install -r requirements.txt
python -m playwright install chromium
python run.py
```

Open **http://127.0.0.1:8000**. The first account you create becomes the **master**.

## First run

**As the master**
1. **Add admins** (Users → Add user), or let people sign up and approve them (Users → Pending, or the Platform overview).
2. Check who has connected DeepSeek under **Settings → DeepSeek accounts**.

**As an admin or bidder**
1. **Connect your DeepSeek account** (Settings → My DeepSeek account → Connect DeepSeek, or the banner at the top). Your DeepSeek sign-in window opens on the computer running CareerPilot and is shown to you live, inside CareerPilot: click into it, sign in with your email or phone (solve any check DeepSeek shows; paste a password with Ctrl+V or the text box under the view), wait for the chat page, then click **Done**. This works from any computer; the server's screen must be unlocked for your clicks and typing to arrive. You only do this once.

**As an admin**
1. **Create a profile** (Profiles → New profile). The work history and notes for each role are the facts the AI must keep.
2. **Review your prompt** (Prompts). A solid default is created for you.
3. **Add bidders** (My bidders → Add bidder) and assign them profiles.
4. **Add an application**: paste a job link, check the fields, click **Save & tailor documents**.

Want to look around before connecting DeepSeek? The master can switch **Settings → Platform → AI engine mode** to **Demo**, which produces placeholder documents instantly.

## How generation works

```
Web request ──► your queue ──► your engine thread ──► Chrome on your profile (chat.deepseek.com, hidden window)
                                │
                                ├─ prompt = your instructions + profile facts + job + output contract
                                ├─ parse reply → résumé JSON + cover letter
                                ├─ validate → retry in a new chat (max 3)
                                └─ save version → render template → PDF / DOCX on download
```

- The profile controls name, contact details, employers, dates and education. DeepSeek writes the summary, skills, bullets, cover letter and answers.
- Each user has their own engine and DeepSeek session. One person's requests run one at a time; different people's run side by side.
- The browser closes after 10 idle minutes and reopens on the next request.

## Configuration

Environment variables (all optional):

| Variable | Default | Purpose |
| --- | --- | --- |
| `CAREERPILOT_HOST` / `CAREERPILOT_PORT` | `127.0.0.1` / `8000` | Where the server listens. Use `0.0.0.0` to let other computers on your network connect |
| `CAREERPILOT_DATA` | `./data` | Database and PDF cache folder |
| `CAREERPILOT_BROWSER_PROFILE` | `~/.careerpilot_browser_profile` | Folder holding each user's saved DeepSeek sign-in (`users/u<id>`) |
| `CAREERPILOT_LOGIN_TIMEOUT` | `900` | Seconds a DeepSeek sign-in window may stay open before it's closed |
| `CAREERPILOT_CHROME` | Chrome's default path | Browser to use if Playwright's Chromium is missing |
| `CAREERPILOT_RESPONSE_TIMEOUT` | `240` | Seconds to wait for one reply |
| `CAREERPILOT_ENGINE_IDLE` | `600` | Close the browser after this many idle seconds |
| `CAREERPILOT_PUBLIC_URL` | empty | The address people use when the site is published through Netlify (below). Used for share links; turns on HTTPS-only cookies |
| `CAREERPILOT_SECURE_COOKIES` | `1` if the public URL is HTTPS | Mark session cookies HTTPS-only |
| `CAREERPILOT_GOOGLE_SITE_VERIFICATION` | empty | Google Search Console "HTML tag" code; rendered into the front page (see Search engines below) |

Instead of environment variables you can put `KEY=VALUE` lines in a `.env` file next to `run.py`.

## Run it on the internet (Netlify + one Windows machine)

CareerPilot has two parts, and only one of them can live on Netlify:

| Part | Runs on | Why |
| --- | --- | --- |
| **The web app** (`web/`): the pages people open | **Netlify**, deployed from GitHub | Static files on a CDN: always up, HTTPS, a public address |
| **The server** (`server/`): accounts, database, job pool, every user's DeepSeek engine and sign-in window | **One Windows computer that stays on**: a Windows VPS, or your own PC | It needs an always-running process, a database on disk, Chrome, and a screen for DeepSeek sign-ins. Netlify (like most clouds) has none of these |

People only ever use the Netlify address. The Netlify site proxies `/api/*` and `/r/*` (résumé share links) to the server, so for the browser it is one site: same cookies, same links, nothing to set up on the user's side. Users never open the server directly.

### 1. The server computer

1. Install CareerPilot on it as described above (`install_windows.bat`). Each user's DeepSeek sign-in runs in a Chrome window **on this computer**, which CareerPilot shows to the user live in their own browser and drives with real mouse and keyboard input. So the computer needs a desktop session that stays **logged in and unlocked**: on a VPS, leave Remote Desktop by running `deploy\disconnect-rdp.cmd` as administrator (it hands the session to the console without locking it) rather than closing the Remote Desktop window, locking, or signing out. Turn off the screen saver's "On resume, display logon screen" and automatic lock in Windows settings. While a user is signing in, their Chrome window stays on top of the other windows on this computer so that their clicks land in it; it closes when they click Done.
2. Give it an HTTPS address, one of:
   - **It has a public IP (typical VPS):** point a DNS name such as `api.example.com` at it, install [Caddy](https://caddyserver.com/download), edit the domain in `deploy/Caddyfile`, and run `caddy run --config deploy\Caddyfile`. Caddy gets the certificate itself and forwards to CareerPilot on `127.0.0.1:8000`. Open ports 80 and 443 in Windows Firewall and at your provider.
   - **It has no public IP (a home PC):** use a [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/get-started/create-local-tunnel/) with a domain on Cloudflare: `cloudflared tunnel login`, `cloudflared tunnel create careerpilot`, `cloudflared tunnel route dns careerpilot api.example.com`, then a config that sends `api.example.com` to `http://127.0.0.1:8000` and `cloudflared service install`. No ports to open.
3. Create a `.env` file next to `run.py` with the address people will use (your Netlify site, or your own domain once it's added in Netlify):
   ```
   CAREERPILOT_PUBLIC_URL=https://your-site.netlify.app
   ```
4. Make it start at every logon: run `powershell -ExecutionPolicy Bypass -File deploy\install-autostart.ps1` once. It registers a scheduled task that runs `python run.py` when you log on and restarts it if it stops. Keep `CAREERPILOT_HOST` at `127.0.0.1`, so only Caddy or cloudflared can reach it.

### 2. Netlify

1. Push this folder to a GitHub repository. `.gitignore` already leaves out the database, uploaded files, `.env` and browser profiles.
2. In Netlify: **Add new site → Import an existing project → GitHub**, pick the repository. `netlify.toml` sets the publish folder (`web`) and the build command.
3. **Site configuration → Environment variables**: add `CAREERPILOT_BACKEND_URL` = the server's HTTPS address from step 1 (for example `https://api.example.com`), then deploy. The build writes `web/_redirects` with the proxy rules from that variable. Without the variable the site still deploys (front page, guides, FAQ), but the app answers "not connected to a CareerPilot server yet" until you add it and redeploy, so you can publish first and connect the server later.
4. Open the Netlify address and create the first account, which becomes the master. From then on every push to GitHub redeploys the site; server-side changes are picked up by restarting CareerPilot on the server computer (pull, then `Restart-ScheduledTask CareerPilot`).

### Good to know

- When the server computer is off or unreachable, the site still loads but shows "The CareerPilot server is offline right now".
- When the server computer's screen is locked, DeepSeek sign-ins show a warning: the user can watch their window but nothing they do arrives until someone unlocks it (or hands the session to the console, see above). Everything else keeps working.
- DeepSeek's captcha (hCaptcha) tends to be stricter with data-centre IP addresses, so sign-ins on a VPS may take a few tries; a home connection has it easier. Once signed in, users stay signed in for a long time.
- After a reboot (for example Windows Update), CareerPilot starts only once someone logs on. Turn on automatic logon for that Windows user (Sysinternals Autologon, or `netplwiz`) so it comes back on its own.
- Each connected user gets their own Chrome on the server computer when they generate, roughly 0.5–1 GB of RAM each while active. Plan for that when choosing a VPS.
- Run exactly one server process (no `--workers`): queues and engines live in memory.
- Netlify's proxy gives up on requests that take longer than about 30 seconds. CareerPilot's long jobs (generation, job imports, sign-in checks) already run in the background and are polled, so normal use is unaffected.
- On the server computer itself you can still open `http://localhost:8000` directly.

### 3. Search engines (SEO)

The front page is the only public page (everything else is behind sign-in, and résumé share links are deliberately kept out of search results), so that is what is tuned for Google:

- **Built in**: a descriptive title and meta description, canonical URL, Open Graph and Twitter cards with a 1200×630 preview image (`web/assets/img/social-card.png`), structured data (`WebSite` + `WebApplication` JSON-LD), a static copy of the landing content in `index.html` that crawlers and link previews read before the app starts, `robots.txt`, `sitemap.xml`, a web app manifest with icons, real 404s for unknown addresses, and `noindex` on `/api/*` and `/r/*`. Public pages with their own titles, descriptions and structured data give search engines something to rank for the phrases people actually type: `/features/`, `/how-it-works/`, `/for-teams/`, `/faq/` (FAQ rich-result markup) and the guides under `/guides/` (tailoring a résumé to a job description, matching cover letters, application questions, tracking applications). They are plain HTML files in `web/`; edit them like any page, keep the `__PUBLIC_URL__` placeholders, and add new ones to `web/sitemap.xml`. The site address in all of these comes from `CAREERPILOT_PUBLIC_URL`, or from Netlify's own site URL when that variable is not set; deploy previews and branch deploys are marked `noindex` automatically.
- **Custom domain**: ranking for the name is far easier on a domain that contains it (for example `careerpilot.app` or `getcareerpilot.com`). Add it in Netlify (**Domain management**), make it the primary domain, and set `CAREERPILOT_PUBLIC_URL` to it so canonical links, the sitemap and share links all use it.
- **Google Search Console**: open [search.google.com/search-console](https://search.google.com/search-console), add a *URL prefix* property for the site address, choose the **HTML tag** method and copy the `content="…"` value into the Netlify environment variable `CAREERPILOT_GOOGLE_SITE_VERIFICATION` (or into `.env` on the server when it serves the site directly), redeploy, then click **Verify**. Afterwards submit `https://<your site>/sitemap.xml` under **Sitemaps** and use **URL inspection → Request indexing** for the front page once. Bing Webmaster Tools can import the verified property from Search Console.
- **What helps most after that**: links to the site from places you control (GitHub repository description and website field, social profiles, a launch post), the exact brand name "CareerPilot" used consistently in those links, and keeping the page fast (it already ships local fonts and no third-party scripts). Searches for the generic phrases the page targets ("AI résumé tailoring", "tailor résumé to job description", "AI cover letter generator", "job application tracker") are competitive; the brand name is where a new site ranks first.

## Data and backups

Everything is stored in `data/careerpilot.db` (SQLite). Back it up by copying that file while the server is stopped.

## Project layout

```
run.py                     start the server
server/
  app.py                   REST API + static web app
  auth.py, db.py           sessions, password hashing, SQLite schema
  warnings.py              duplicate + keyword warning rules
  engine/                  DeepSeek web automation, per-user engines and queues, sign-in window, prompt builder
  render/                  template rendering, PDF (headless Chromium), DOCX writer
  scraping/                job-link scraper, job pool feeds
web/                       single-page app (no build step)
data/templates/            built-in résumé templates
netlify.toml, netlify/     Netlify site: publishes web/ and proxies /api to the server (see "Run it on the internet")
deploy/                    Caddyfile (HTTPS for the server), autostart task installer, disconnect-rdp.cmd
web/robots.txt, sitemap.xml, manifest.webmanifest, 404.html, assets/img/   search-engine files, icons and the social preview card
web/features/, how-it-works/, for-teams/, faq/, guides/   public pages (plain HTML, styled by assets/css/site.css)
server/engine/winremote.py live view of + real input into the DeepSeek sign-in window (Windows: PrintWindow, SendInput)
```

API docs: http://127.0.0.1:8000/api/docs

## Notes

CareerPilot automates the DeepSeek website instead of using an official API. That can break if DeepSeek changes its interface, and it can be affected by sign-in challenges, rate limits or captchas. Check DeepSeek's terms of use for your situation. Job scraping uses public endpoints and pages; some sites block automated access, in which case paste the description instead.
