# Philex / Broadway Lounge — Cloudflare Pages

Static site (templates pre-rendered to HTML) + serverless form handlers on
**Cloudflare Pages**, with email sent over SMTP from a Pages Function.

## How it works

| Part | What it used to be | Now |
| --- | --- | --- |
| Pages (`/`, `/about`, `/bar`, `/contact`, `/gallery`, `/philex-index`) | FastAPI rendering Jinja2 templates | Static HTML in `./dist`, served from Cloudflare's CDN |
| `POST /contact-us` | FastAPI + `smtplib` | `functions/contact-us.js` → SMTP via Workers `connect()` |
| `POST /reserve-table` | FastAPI + `smtplib` | `functions/reserve-table.js` → SMTP via Workers `connect()` |

- `scripts/build.mjs` renders the Jinja templates to `dist/` and copies `static/`.
- `functions/_lib/smtp.js` is a minimal SMTP client for Cloudflare's TCP socket
  API. It defaults to Hostinger (`smtp.hostinger.com:465`, implicit TLS) and
  supports port 587 with STARTTLS. Port 25 is blocked by Cloudflare.
- Media was re-encoded so every file is under Pages' 25 MiB per-file limit.

## Deploy on Cloudflare Pages (free)

1. Push this repo to GitHub (it already is: `https://github.com/Balogunolalere/philex`).
2. Go to the [Cloudflare dashboard](https://dash.cloudflare.com) →
   **Workers & Pages** → **Create** → **Pages** → **Connect to Git** →
   select the repo.
3. Build settings:
   - Framework preset: **None**
   - Build command: `node scripts/build.mjs`
   - Build output directory: `dist`
4. Click **Save and Deploy** — you'll get `https://<project>.pages.dev`.
5. Add the environment variables under
   **Settings → Environment variables** (and in the Production tab):

   | Variable | Value |
   | --- | --- |
   | `HOST_EMAIL` | your mailbox, e.g. `info@philexentertainment.com` (used as sender and default recipient) |
   | `HOST_PASSWORD` | the mailbox password |

   Optional: `SMTP_HOST` (default `smtp.hostinger.com`), `SMTP_PORT`
   (default `465`; use `587` for STARTTLS), `EMAIL_TO` (defaults to
   `HOST_EMAIL`), `EMAIL_FROM_NAME` (default `Broadway Lounge`).

Every `git push` now rebuilds and redeploys — no server to manage.

### Custom domain

The domain is registered at Hostinger, but Cloudflare Pages needs your zone on
Cloudflare (free plan also gives you DNS + CDN):

1. Hostinger → Domain → set nameservers to the two Cloudflare nameservers
   you're shown when adding the domain on Cloudflare (Registration/DNS, Free plan).
2. Cloudflare dashboard → **Add a domain** → pick the **Free** plan and wait for
   the zone to become active (last step: "change nameservers").
3. Workers & Pages → your project → **Custom domains** → **Set up a custom
   domain** → enter e.g. `philexentertainment.com` (and `www.` as another).

### Local testing

```bash
npx wrangler pages dev dist --port 8788
```

Create `.dev.vars` (gitignored) with your SMTP credentials for local form tests.

## Notes & limits

- Free tier: static assets are free/unlimited; Pages Functions count against
  the Workers free quota (100,000 requests/day). Enough for a restaurant site.
- `/reservations` (never rendered by the old app) now 301-redirects to `/bar`.
- `_headers` adds security headers and long cache for `/static/*`.
- The old FastAPI app (`main.py`, `requirements.txt`, `render.yml`) was removed;
  it lives in git history if you ever need it.
