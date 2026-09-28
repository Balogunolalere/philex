"""FastAPI app for the Broadway Lounge site, running on Cloudflare Python Workers.

The pages are pre-rendered to ./dist by scripts/build.mjs and served from the
ASSETS binding (see the "Serve a frontend" section of Cloudflare's FastAPI
guide). This app handles the form POSTs — each one is screened for spam
(src/spam.py) and emailed through the mailapi service (src/mailer.py) — and
proxies everything else to the asset store.

Run locally:   uv run pywrangler dev
Deploy:        uv run pywrangler deploy
"""

from __future__ import annotations

from fastapi import FastAPI, Form, Request, status
from fastapi.responses import RedirectResponse, Response
from workers import WorkerEntrypoint

import spam
from mailer import send_email

app = FastAPI(docs_url=None, redoc_url=None)

MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def escape_html(value: object) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def format_date(raw: str) -> str:
    """'13/09/2023' -> 'September 13, 2023'; returns input if unparseable."""
    parts = raw.strip().split("/")
    if len(parts) != 3:
        return raw
    try:
        day, month, year = int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError:
        return raw
    if month < 1 or month > 12:
        return raw
    return f"{MONTHS[month - 1]} {day}, {year}"


def _env_value(env, name: str) -> str:
    value = getattr(env, name, None)
    return value if value is not None else ""


async def _deliver(env, *, kind: str, subject: str, html: str) -> None:
    """Mail a submission.

    A mailapi failure is logged, never surfaced: the visitor still gets the
    same redirect, and nothing is lost that the Web UI did not already show.
    """
    try:
        await send_email(env, to=_env_value(env, "EMAIL_TO"), subject=subject, html=html)
    except Exception as exc:  # noqa: BLE001 - the visitor still gets their redirect
        print(f"{kind}: send failed: {exc!r}")


@app.post("/contact-us")
async def contact_us(
    request: Request,
    name: str = Form(""),
    email: str = Form(""),
    message: str = Form(""),
    website: str = Form(""),
    elapsed: str = Form(""),
):
    """Handle contact form submissions from /contact."""
    env = request.scope["env"]
    value = {
        "name": name.strip(),
        "email": email.strip(),
        "message": message.strip(),
    }

    reason = await spam.screen(website=website, elapsed=elapsed)
    if reason:
        print(f"contact-us: dropped submission ({reason})")
    elif not all(value.values()) or "@" not in value["email"]:
        print(f"contact-us: invalid submission (name/email/message required): {value!r}")
    else:
        html = (
            "<h2>Form Submission</h2>"
            f"<p><b>Name:</b> {escape_html(value['name'])}</p>"
            f"<p><b>Email:</b> {escape_html(value['email'])}</p>"
            f"<p><b>Message:</b> {escape_html(value['message'])}</p>"
        )
        await _deliver(
            env,
            kind="contact",
            subject=f"Contact Form: {value['name']}",
            html=html,
        )
    return RedirectResponse(url="/contact", status_code=status.HTTP_302_FOUND)


@app.post("/reserve-table")
async def reserve_table(
    request: Request,
    name: str = Form(""),
    email: str = Form(""),
    phone: str = Form(""),
    partysize: str = Form(""),
    date: str = Form(""),
    time: str = Form(""),
    website: str = Form(""),
    elapsed: str = Form(""),
):
    """Handle table reservation form submissions from /bar."""
    env = request.scope["env"]
    value = {
        "name": name.strip(),
        "email": email.strip(),
        "phone": phone.strip(),
        "Party Size": partysize.strip(),
        "Date": format_date(date),
        "Time": time.strip(),
    }

    reason = await spam.screen(website=website, elapsed=elapsed)
    if reason:
        print(f"reserve-table: dropped submission ({reason})")
    elif not all(value.values()) or "@" not in value["email"]:
        print(f"reserve-table: invalid submission (name/email/phone/partysize/date/time required): {value!r}")
    else:
        html = (
            "<h2>Table Reservation Request</h2>"
            f"<p><b>Name:</b> {escape_html(value['name'])}</p>"
            f"<p><b>Email:</b> {escape_html(value['email'])}</p>"
            f"<p><b>Phone:</b> {escape_html(value['phone'])}</p>"
            f"<p><b>Party Size:</b> {escape_html(value['Party Size'])}</p>"
            f"<p><b>Date:</b> {escape_html(value['Date'])}</p>"
            f"<p><b>Time:</b> {escape_html(value['Time'])}</p>"
        )
        await _deliver(
            env,
            kind="reservation",
            subject=f"Table Reservation: {value['name']}",
            html=html,
        )
    return RedirectResponse(url="/bar", status_code=status.HTTP_302_FOUND)


@app.post("/free-ticket")
async def free_ticket(
    request: Request,
    name: str = Form(""),
    email: str = Form(""),
    phone: str = Form(""),
    website: str = Form(""),
    elapsed: str = Form(""),
):
    """Handle free-ticket claims from the /bar giveaway popup.

    Tickets themselves are issued by hand on Tix (Attendees -> Guests -> Guest
    List); this mails the claim to the organisers so they can issue one.
    """
    env = request.scope["env"]
    value = {
        "name": name.strip(),
        "email": email.strip(),
        "phone": phone.strip(),
    }

    reason = await spam.screen(website=website, elapsed=elapsed)
    if reason:
        print(f"free-ticket: dropped submission ({reason})")
    elif not all(value.values()) or "@" not in value["email"]:
        print(f"free-ticket: invalid submission (name/email/phone required): {value!r}")
    else:
        html = (
            "<h2>Free Ticket Claim (The BLAQ Xperience)</h2>"
            f"<p><b>Name:</b> {escape_html(value['name'])}</p>"
            f"<p><b>Email:</b> {escape_html(value['email'])}</p>"
            f"<p><b>Phone:</b> {escape_html(value['phone'])}</p>"
            "<p>Send this guest their ticket: Tix &rarr; Attendees &rarr; Guests &rarr; Guest List.</p>"
        )
        await _deliver(
            env,
            kind="free-ticket",
            subject=f"Free Ticket Claim: {value['name']}",
            html=html,
        )
    return RedirectResponse(url="/bar?claimed=1", status_code=status.HTTP_302_FOUND)


@app.get("/{path:path}")
async def frontend(path: str, request: Request):
    """Catch-all: proxy unmatched requests to the Workers static assets."""
    env = request.scope["env"]
    if path == "reservations":
        return RedirectResponse(url="/bar", status_code=301)
    asset_url = f"https://assets.local/{path}"
    resp = await env.ASSETS.fetch(asset_url)
    body = await resp.bytes()
    headers = dict(resp.headers)
    return Response(content=body, status_code=resp.status, headers=headers)


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        import asgi

        return await asgi.fetch(app, request.js_object, self.env)
