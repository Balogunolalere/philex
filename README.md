# Philex / Broadway Lounge — Cloudflare (free)

Static site (templates pre-rendered to HTML) + serverless form handlers on
**Cloudflare Workers / Pages**, with email sent over SMTP from serverless code.
No database, no server — free tier, auto-deploys on `git push`.

## How it works

| Part | What it used to be | Now |
| --- | --- | --- |
| Pages (`/`, `/about`, `/bar`, `/contact`, `/gallery`, `/philex-index`) | FastAPI rendering Jinja2 templates | Static HTML in `./dist`, served from Cloudflare's CDN |
| `POST /contact-us` | FastAPI + `smtplib` | `functions/contact-us.js` (Pages) or `src/worker.js` (Workers) → SMTP |
| `POST /reserve-table` | FastAPI + `smtplib` | same as above |

- `scripts/build.mjs` renders the Jinja templates to `dist/` (verified
  byte-identical to Jinja2 output) and copies `static/`. The templates use only
  `{% extends %}` / `{% block %}`, so no Python runtime is needed.
- `functions/_lib/smtp.js` is a minimal SMTP client for Cloudflare's TCP
  socket API. It defaults to Hostinger (`smtp.hostinger.com:465`, implicit TLS)
  and supports port 587 with STARTTLS. Port 25 is blocked by Cloudflare.
- Media was re-encoded so every file is under the 25 MiB per-file limit.

## Deploying

### Option A — Workers (what's live at `*.workers.dev`)

The repo ships a GitHub-ready Workers project:

- `wrangler.jsonc` — serves `./dist` via the `ASSETS` binding, `run_worker_first: true`
- `src/worker.js` — handles the two form POSTs, serves everything else from `ASSETS`
- `package.json` — `npm run build` / `npm run deploy`

Dashboard: **Workers & Pages → Create → Workers → Connect to GitHub** (Workers
Builds). After a push it will run the build and `wrangler deploy` automatically.
If your project was created with custom build/deploy commands, set them to:

- Build command: `node scripts/build.mjs` (or `npm run build`)
- Deploy command: `npm run deploy` (or `wrangler deploy`)

Then add environment variables under **Settings → Variables and Secrets**:

| Variable | Value |
| --- | --- |
| `HOST_EMAIL` | your mailbox, e.g. `info@philexentertainment.com` (sender + default recipient) |
| `HOST_PASSWORD` | the mailbox password |

Optional: `SMTP_HOST` (default `smtp.hostinger.com`), `SMTP_PORT` (default
`465`; use `587` for STARTTLS), `EMAIL_TO` (defaults to `HOST_EMAIL`),
`EMAIL_FROM_NAME` (default `Broadway Lounge`).

### Option B — Pages (the Vercel-style flow)

1. **Workers & Pages → Create → Pages → Connect to Git** → select the repo.
2. Build settings: framework preset **None**, build command
   `node scripts/build.mjs`, output directory `dist`.
3. Add the same environment variables under **Settings → Environment variables**.
4. Every push rebuilds and redeploys. You get a free `*.pages.dev` URL.

*(The `functions/` directory is the Pages variant of the form handlers; if you
use Option A, ignore it.)*

### Custom domain

The domain is registered at Hostinger, but Cloudflare needs your zone on its
platform (free plan also gives DNS + CDN):

1. Hostinger → Domain → set the nameservers to the two Cloudflare nameservers
   shown when adding the domain on Cloudflare (**Add a domain**, Free plan).
2. Wait for the zone to show **Active**.
3. Workers & Pages → your project → **Custom domains** → **Set up a custom
   domain** → enter e.g. `philexentertainment.com` (add `www.` separately).

## Local development

```bash
npm install
npm run dev        # builds dist/, then wrangler dev serves it locally
```

Create `.dev.vars` (gitignored) with your SMTP credentials for local form tests.

## Troubleshooting

**The site shows raw `{% endblock %}` text and/or missing images** — the
deployment is serving the *template files* instead of the built site. Root
cause: the build step isn't (or no longer) producing `dist/` as the site root.
Fix: make sure the build command runs `node scripts/build.mjs` and the output
directory is `dist` (not `templates`), then redeploy. Sanity-check locally:
`node scripts/build.mjs` must print `rendered about -> /about ... done -> ./dist`.

**Form emails don't arrive** — SMTP from serverless IPs can be throttled. Check
the Worker/Pages logs for `contact-us failed` / `reserve-table failed`; usually
it's credentials (`HOST_PASSWORD`) or an unauthorized sender address. The
drop-in upgrade is a free email API (Resend/Brevo) — swap the internal
`sendEmail(env, ...)` call.

## Notes & limits

- Free tier: requests to static assets are free/unlimited; form function calls
  count against the Workers free quota (100,000 requests/day).
- `/reservations` (never rendered by the old app) now 301-redirects to `/bar`.
- The old FastAPI app (`main.py`, `requirements.txt`, `render.yml`) was moved
  out of the repo; it lives in git history.
