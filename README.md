# Philex / Broadway Lounge — Cloudflare Python Workers (FastAPI)

The site runs on **Cloudflare Python Workers** with **FastAPI**, deployed with
`uv` + `pywrangler` (Cloudflare's official Python Workers tooling). Pages are
pre-rendered static HTML served from Cloudflare's CDN; FastAPI handles the
form POSTs and emails them over SMTP (Hostinger). Free tier, auto-deploys.

Followed Cloudflare's official guides/examples:
- FastAPI on Python Workers: https://developers.cloudflare.com/workers/languages/python/packages/fastapi/
- Python Workers examples (FastAPI + assets): https://github.com/cloudflare/python-workers-examples

## How it works

| Part | Implementation |
| --- | --- |
| `/`, `/about`, `/bar`, `/contact`, `/gallery`, `/philex-index` | `scripts/build.mjs` renders the Jinja templates to `dist/` (byte-identical to Jinja2); served from the `ASSETS` binding |
| `POST /contact-us`, `POST /reserve-table` | `src/worker.py` (FastAPI) → `src/mailer.py` (SMTP over `cloudflare:sockets`) |
| `/reservations` (legacy) | 301 → `/bar` |
| Other unmatched paths | FastAPI catch-all proxies to `ASSETS` (official pattern) |

- `src/mailer.py` implements SMTP (EHLO, AUTH LOGIN with AUTH PLAIN fallback,
  dot-stuffing) over Cloudflare's TCP socket API — Python's `socket` module is
  not available in Workers; the FFI (`import_from_javascript("cloudflare:sockets")`)
  is used instead. Defaults: `smtp.hostinger.com:465` (implicit TLS) or `587`
  (STARTTLS). Port 25 is blocked by Cloudflare.

## Local development

Prerequisites: [uv](https://docs.astral.sh/uv/getting-started/installation/)
and [Node.js](https://nodejs.org/).

```bash
node scripts/build.mjs   # rebuild ./dist (static assets; needed before dev/deploy)
uv run pywrangler dev
```

This builds the worker and serves the site locally at `http://localhost:8787`.
`dist/` is a build artifact (gitignored), so run `scripts/build.mjs` first — and
again whenever you change `templates/` or `static/`.

For form tests, create `.dev.vars` (gitignored) in the project root:

```bash
HOST_EMAIL=info@yourdomain.com
HOST_PASSWORD=your-mailbox-password
SMTP_HOST=smtp.hostinger.com
SMTP_PORT=465
```

## Deploy

```bash
uv run pywrangler deploy
```

It prompts you to log in to Cloudflare via the browser on first use.

Environment variables (secrets — set with `npx wrangler secret put <NAME>` or
in the dashboard under **Settings → Variables and Secrets**):

| Variable | Required | Notes |
| --- | --- | --- |
| `HOST_EMAIL` | yes | mailbox address (sender + default recipient) |
| `HOST_PASSWORD` | yes | Hostinger mailbox password |
| `SMTP_HOST` | no | default `smtp.hostinger.com` |
| `SMTP_PORT` | no | default `465` (use `587` for STARTTLS) |
| `EMAIL_TO` | no | overrides the recipient (default `HOST_EMAIL`) |
| `EMAIL_FROM_NAME` | no | default `Broadway Lounge` |

Local `.dev.vars` is used by `pywrangler dev` automatically; production uses
the secrets above.

### Git push auto-deploy (optional)

If your Worker is connected to GitHub (Workers Builds), the dashboard deploy
settings should be (Settings → Builds & deployments):

- Build command:
  `curl -LsSf https://astral.sh/uv/install.sh | sh && node scripts/build.mjs`
- Deploy command: `~/.local/bin/uv run pywrangler deploy`

Why: `pywrangler deploy` first runs `sync`, which vendors the Python packages
from `pyproject.toml` (fastapi, python-multipart, …) into `python_modules/`
using uv+Pyodide, then proxies to `wrangler deploy`. Plain `wrangler deploy`
uploads the Worker and assets but does not vendor Python packages, so the
deployment would fail at runtime with missing imports (and `dist/` must exist
because `wrangler.jsonc` points `assets.directory` at it — hence the build
command builds it first).

> **Netlify is not used.** If you see a Netlify build of this repo failing with
> `npm error ... ENOENT ... /opt/buildhome/repo/package.json`, that's a stale
> Netlify site still connected to the GitHub repo (its dashboard build command
> is `npm run build`, but this repo has no `package.json` since the Cloudflare
> migration — Netlify also can't run the Worker, which needs Cloudflare's
> `cloudflare:sockets` API). Delete the site under Netlify → Site configuration
> → Danger zone, or at least disconnect the repo.

> **Troubleshooting: `The directory specified by the "assets.directory" field
> ... does not exist: /opt/buildhome/repo/dist`** — the build command
> (`node scripts/build.mjs`) is not running in your project settings, so
> `dist/` is never created. Set the Build command as above and redeploy.

> **Troubleshooting: `ModuleNotFoundError: fastapi` at runtime** — the Worker
> was deployed without vendored packages (e.g. with plain `npx wrangler
> deploy`). Deploy with `uv run pywrangler deploy` so `python_modules/` gets
> created and uploaded.

## Custom domain

The domain is registered at Hostinger, but Cloudflare needs your zone on its
platform (free plan also gives DNS + CDN):

1. Cloudflare dashboard → **Add a domain** → Free plan → note the two
   nameservers.
2. Hostinger → Domain → set those nameservers; wait until the zone is **Active**.
3. Worker → **Settings → Domains & Routes → Add custom domain** →
   `philexentertainment.com` (add `www.` separately).

## Free-tier caveats

- Python Workers are in **beta** (`python_workers` compatibility flag, enabled
  in `wrangler.jsonc`) and run via Pyodide (WASM).
- Free plan: 100,000 requests/day and **10 ms CPU per invocation**. FastAPI +
  Pyodide can approach that on cold starts; if you ever hit `501 CPU time limit`
  errors, the cheapest fix is removing `"run_worker_first": true` from
  `wrangler.jsonc` (pages then get served straight from the CDN without
  invoking Python) — or moving to the Workers Paid plan.
- SMTP from serverless IPs can occasionally be throttled by mail providers;
  check Worker logs for `contact-us: send failed` / `reserve-table: send
  failed`.

## Layout

```
src/worker.py      FastAPI app + WorkerEntrypoint (ASGI)
src/mailer.py      SMTP over cloudflare:sockets
scripts/build.mjs  renders templates/ -> dist/ and copies static/
templates/         Jinja2 templates (source of truth)
static/            images, fonts, etc.
wrangler.jsonc     Worker config (python_workers, ASSETS binding = ./dist)
```

The old FastAPI/Render files (`main.py`, `requirements.txt`, `render.yml`) and
the JS Pages-Functions variant are in git history.
