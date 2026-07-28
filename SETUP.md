# Setup & running from mobile

This tool scrapes a logged-in OneHome portal and builds an Excel workbook. You can
run it on your Mac, or drive it from your phone via **Claude Code on the web**
(open `claude.ai/code` in a mobile browser or the Claude app) against this GitHub
repo. The scraper runs headless by default, and `--email-to` sends the finished
workbook to your inbox so there's nothing to download from the session.

## 1. Environment setup (one time)

Requires Python 3.11+.

```bash
python3.11 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/playwright install chromium     # headless browser used for scraping
cp .env.example .env                       # then fill in the values (see below)
```

## 2. Secrets (`.env` — never committed)

`.env` is gitignored. In a cloud/mobile session, set these as the environment's
secrets rather than committing them.

| Variable | Required | Purpose |
|---|---|---|
| `ONEHOME_EMAIL` / `ONEHOME_PASSWORD` | yes | OneHome portal login (fallback if a token URL expires) |
| `SMTP_HOST` / `SMTP_USER` / `SMTP_PASSWORD` | for `--email-to` | outbound email (Gmail: use an App Password) |
| `SMTP_PORT` | no | defaults to `587` (STARTTLS) |
| `EMAIL_FROM` | no | defaults to `SMTP_USER` |

## 3. Get a fresh search URL

The OneHome share link carries a `?token=…` that is session-scoped and expires.
From the OneHome portal open your **"Investment Properties"** saved search and copy
the current URL (the `/properties/map?token=…&searchId=…` link).

## 4. Run

```bash
# All properties in the saved search → workbook, emailed to you
.venv/bin/rentalysis analyze \
  --search-url "https://portal.onehome.com/en-US/properties/map?token=…&searchId=…" \
  --email-to you@example.com \
  -o investment_properties.xlsx

# Quick sample while testing
.venv/bin/rentalysis analyze --search-url "…" --limit 5 --email-to you@example.com
```

Caching (`.cache/`) makes re-runs fast and lets a run resume after partial failures.
Use `--no-cache` to force a fresh scrape.

## Trigger from GitHub (mobile-friendly, no Claude session)

A `workflow_dispatch` Action (`.github/workflows/analyze.yml`) runs the analysis on
GitHub's runners and emails you the workbook (or leaves it as a downloadable
artifact).

1. **Add repo secrets** — Settings → Secrets and variables → Actions → New secret:
   | Secret | Required | |
   |---|---|---|
   | `ONEHOME_EMAIL` / `ONEHOME_PASSWORD` | yes | portal login |
   | `SMTP_HOST` / `SMTP_USER` / `SMTP_PASSWORD` | for email | Gmail: App Password |
   | `SMTP_PORT` / `EMAIL_FROM` | optional | default 587 / `SMTP_USER` |
2. **Run it** — Actions tab → *Analyze Investment Properties* → **Run workflow**,
   paste a fresh saved-search URL (optionally a `limit` and `email_to`). On a phone,
   the GitHub mobile app / mobile web both expose this "Run workflow" button.
3. Get the result by email (if `email_to` set) or download the run's
   **investment_properties** artifact.

> The saved-search token expires — paste a current URL each run.

## From your phone, in practice

1. Open `claude.ai/code` (browser or Claude app) and select this repo.
2. Paste a fresh saved-search URL and ask it to run:
   *"Run `rentalysis analyze --search-url \"<paste>\" --email-to you@example.com`."*
3. The session scrapes headless in the cloud and emails you the `.xlsx`.

> Note: the cloud environment needs the setup from steps 1–2 available (a startup
> script that installs deps + `playwright install chromium`, and the secrets set in
> the environment). The OneHome token must be current each run.
