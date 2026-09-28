# Philex / Broadway Lounge — Cloudflare Python Workers (FastAPI)

The site runs on **Cloudflare Python Workers** with **FastAPI**, deployed with
`uv` + `pywrangler` (Cloudflare's official Python Workers tooling). Pages are
pre-rendered static HTML served from Cloudflare's CDN; FastAPI handles the form
POSTs and emails them through the **mailapi** service. Free tier, auto-deploys.

Followed Cloudflare's official guides/examples:
- FastAPI on Python Workers: https://developers.cloudflare.com/workers/languages/python/packages/fastapi/
- Python Workers examples (FastAPI + assets): https://github.com/cloudflare/python-workers-examples

## How it works

| Part | Implementation |
| --- | --- |
| `/`, `/about`, `/bar`, `/contact`, `/gallery`, `/philex-index` | `scripts/build.mjs` renders the Jinja templates to `dist/` (byte-identical to Jinja2); served from the `ASSETS` binding |

`scripts/build.mjs` also rewrites the templates' asset URLs: everything that
originally pointed at the WordPress demo (`fidalgo.qodeinteractive.com`,
`export.qodethemes.com`) is served from the local `static/` mirror instead
(fixed slow loads and the missing-`custom-frontend-lite.min.css` layout
breakage), and the demo-only snippets (Google Tag Manager, Zendesk chat, qode
toolbar) are stripped. `scripts/normalize-static-assets.mjs` was a one-off
cleanup that renamed the mirror's `file.css?ver=…` artifacts into servable
names; keep it for reference but it should be a no-op now.
| `POST /contact-us`, `POST /reserve-table`, `POST /free-ticket` | `src/worker.py` (FastAPI): screened by `src/spam.py`, stored via `src/db.py`, mailed via `src/mailer.py` with the templates in `src/mail_templates.py` |
| `/reservations` (legacy) | 301 → `/bar` |
| Other unmatched paths | FastAPI catch-all proxies to `ASSETS` (official pattern) |

- `src/mailer.py` POSTs to a **mailapi** deployment over ordinary `fetch`. No
  SMTP happens in this Worker, so it needs no mailbox password — only the
  mailapi URL and an API key, both held as Cloudflare secrets. mailapi owns the
  Hostinger SMTP accounts and does the actual delivery (see the sibling
  `mailapi` project).

- **Why not send SMTP directly from the Worker?** Because it does not work
  reliably here. Workers can create outbound TCP sockets via
  `cloudflare:sockets`, but Cloudflare blocks outbound port `25` outright and
  the `465`/`587` submission path has not worked on this free-plan Worker. The
  Worker's supported outbound mechanism is `fetch`, which is HTTP(S) only — so
  reaching an SMTP server means going through something outside Cloudflare that
  can open the socket. That is exactly what mailapi is: an HTTP endpoint that
  performs the SMTP conversation from a host that permits it. The earlier
  `cloudflare:sockets` implementation in this repo is what proved the problem;
  it is in git history and should not be revived.

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
MAILAPI_URL=https://your-mailapi.vercel.app
MAILAPI_KEY=your-mailapi-api-key
EMAIL_TO=info@philexentertainment.com
TURSO_DATABASE_URL=libsql://philex-doombuggy.aws-us-east-1.turso.io
TURSO_AUTH_TOKEN=your-database-token        # turso db tokens create philex
```

## Deploy

```bash
uv run pywrangler deploy
```

It prompts you to log in to Cloudflare via the browser on first use.

Environment variables. Non-secret values live in `wrangler.jsonc` under `vars`
(visible and version-controlled); secrets are set with
`npx wrangler secret put <NAME>` or in the dashboard under
**Settings → Variables and Secrets** — never in `wrangler.jsonc`:

| Variable | Required | Where | Notes |
| --- | --- | --- | --- |
| `MAILAPI_URL` | yes | `vars` | base URL of your mailapi deployment, no trailing slash |
| `MAILAPI_KEY` | yes | secret | API key from mailapi's `API_KEYS`; Cloudflare owns it |
| `EMAIL_TO` | yes | `vars` | where the contact and reservation notifications are delivered |
| `TURSO_DATABASE_URL` | yes | `vars` | `libsql://…` for the `philex` database; `https://` also works |
| `TURSO_AUTH_TOKEN` | yes | secret | database token (`turso db tokens create philex`), **not** an API token |
| `EMAIL_FROM_NAME` | no | either | display name on the From header; unset uses mailapi's account `fromName` |
| `MAILAPI_ACCOUNT` | no | either | only needed if the key is scoped to more than one account |

Local `.dev.vars` is used by `pywrangler dev` automatically and holds all of
the above, secrets included; production reads the split above.

## Forms and spam

Every form on the site (contact, table reservation, free-ticket claim) goes
through the same path in `src/worker.py`:

1. **Screen** — `src/spam.py` drops automated posts: an off-canvas honeypot
   field, and a minimum fill time (the page stamps an `elapsed` field via
   `base.html`). A caught submission gets the normal thank-you, so a bot
   learns nothing.
2. **Store** — `src/db.py` inserts one row into `submissions` on Turso (the
   `philex` database, `docs/schema.sql`). This is the record: name, email,
   phone, message, booking details, plus the visitor's IP and user agent.
3. **Mail** — `src/mailer.py` → mailapi, with the templates in
   `src/mail_templates.py`:
   - contact and reservation → `EMAIL_TO` (philex), with `Reply-To` set to the
     visitor so hitting reply answers them;
   - free-ticket claim → **the claimant**, carrying the ticket (see below).
     philex gets no notification for these; the row in `submissions` is the
     record.

There is still no per-IP cap: counting submissions per address needs shared
state the Worker does not have on the request path, and losing a real enquiry
is worse than accepting one spam entry. Rows are easy to query when a claim
needs checking:

```bash
turso db shell philex "select created_at, kind, name, email, phone from submissions order by id desc limit 20"
```

Both the store and the mail are best effort and never surface to the visitor:
a failure is logged as `<kind>: store failed` / `<kind>: send failed` and the
visitor still gets the same redirect. The two are independent — if the mail
fails, philex still has the row, and if the row fails, philex still has the
mail.

### Free ticket claims

**One ticket per address.** The claim insert itself refuses a repeat
(`INSERT_ONCE_SQL` in `src/db.py`): it checks and writes in a single statement,
so two claims posted at the same moment cannot both land, and addresses are
compared case-folded and trimmed — `Ada@Example.com ` is the same person as
`ada@example.com`. A repeat looks exactly like a first claim from the outside
(same thank-you, same redirect) and is logged as
`free-ticket: <address> has already claimed, no ticket sent`; no second row and
no second ticket. Rows that were claimed before this rule existed are still in
the table — `docs/schema.sql` explains how to promote the lookup index to a
unique one once those are dealt with.

The claim mail is the guest's ticket, so it is addressed to them, not to
philex. It carries:

- the ticket art as a PNG attachment (`static/images/blaq-xperience-ticket-email.png`,
  a 1200px quantised copy of the 1400px original — the full-size one is 756 kB,
  too heavy for mailapi's 1 MiB request cap);
- a short message with the event details, built by `ticket_delivery()`.

If the asset cannot be read, the mail still goes out with a link to the
public ticket instead — a storage hiccup must not strand a guest at the door
(`free-ticket: ticket attachment unavailable` in the log).

Tickets are still issued by hand on Tix (Attendees → Guests → Guest List);
this only delivers the art and records who asked.

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
> migration — and Netlify cannot run a Cloudflare Python Worker at all). Delete
> the site under Netlify → Site configuration
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
- Mail delivery no longer happens in the Worker, so sending mail costs the
  Worker almost no CPU — it makes one outbound `fetch` and awaits the reply.
  Delivery problems now surface in **mailapi's** logs, not here; check Worker
  logs for `contact-us: send failed` / `reserve-table: send failed` to see
  mailapi's error message.

## Layout

```
src/worker.py         FastAPI app + WorkerEntrypoint (ASGI)
src/mail_templates.py branded HTML mail (notifications + guest ticket)
src/mailer.py         HTTP client for the mailapi service
src/db.py             HTTP client for Turso (form submissions)
src/spam.py           honeypot / fill-time screen shared by every form
docs/schema.sql       the submissions table, to recreate it on a fresh database
scripts/test_forms.py stdlib tests for the endpoints, storage and templates
scripts/test_mailer.py stdlib tests for the mailapi request this Worker builds
scripts/test_db.py    stdlib tests for the Turso request this Worker builds
scripts/build.mjs     renders templates/ -> dist/ and copies static/
templates/            Jinja2 templates (source of truth)
static/               images, fonts, etc.
wrangler.jsonc        Worker config (python_workers, ASSETS binding = ./dist)
```

Run the tests (no pytest, no network, no Cloudflare runtime):

```bash
cd scripts && python3 test_forms.py && python3 test_mailer.py && python3 test_db.py
```

The old FastAPI/Render files (`main.py`, `requirements.txt`, `render.yml`) and
the JS Pages-Functions variant are in git history.
