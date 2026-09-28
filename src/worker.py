"""FastAPI app for the Broadway Lounge site, running on Cloudflare Python Workers.

The pages are pre-rendered to ./dist by scripts/build.mjs and served from the
ASSETS binding (see the "Serve a frontend" section of Cloudflare's FastAPI
guide). This app handles the form POSTs. Every submission is:

1. screened for spam (src/spam.py),
2. written to the submissions table on Turso (src/db.py),
3. mailed through the mailapi service (src/mailer.py), using the templates in
   src/mail_templates.py.

Contact and reservation submissions are mailed to EMAIL_TO (philex). A free
ticket claim is mailed to the *claimant* instead, with the ticket attached -
the organisers read that claim from the submissions table, not from an inbox.

Everything else is proxied to the asset store.

Run locally:   uv run pywrangler dev
Deploy:        uv run pywrangler deploy
"""

from __future__ import annotations

from fastapi import FastAPI, Form, Request, status
from fastapi.responses import RedirectResponse, Response
from workers import WorkerEntrypoint

import spam
from db import DuplicateClaim, save_submission
from mail_templates import TICKET_FILENAME, TICKET_SUBJECT, notification, ticket_delivery
from mailer import attachment, send_email

app = FastAPI(docs_url=None, redoc_url=None)

MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]

# Email-sized copy of the ticket art (the 1400px original is 756 kB, too heavy
# for an attachment). Built from static/images/blaq-xperience-ticket-1400.png.
TICKET_ASSET = "static/images/blaq-xperience-ticket-email.png"


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


def _header(request: Request, name: str) -> str:
    value = request.headers.get(name, "")
    return (value or "").strip()


def _client_info(request: Request) -> tuple[str, str]:
    """The visitor's address and browser, for the stored row.

    ``cf-connecting-ip`` is set by Cloudflare on every request; the forwarded
    header is the fallback for local runs and tests.
    """
    ip = _header(request, "cf-connecting-ip")
    if not ip:
        ip = _header(request, "x-forwarded-for").split(",")[0].strip()
    return ip, _header(request, "user-agent")


async def _record(env, *, kind: str, request: Request, once_per_email: bool = False, **fields) -> str:
    """Store a submission. Returns ``stored``, ``duplicate`` or ``failed``.

    Best effort, like the mail: losing a row is bad, but losing the visitor's
    redirect is worse, so a failure is logged and swallowed. ``duplicate`` is
    the one answer the caller acts on - a free-ticket claim that this address
    has already made, which must not be answered with a second ticket.
    """
    ip, user_agent = _client_info(request)
    try:
        await save_submission(
            env,
            kind=kind,
            ip=ip,
            user_agent=user_agent,
            once_per_email=once_per_email,
            **fields,
        )
        print(f"{kind}: stored")
        return "stored"
    except DuplicateClaim:
        return "duplicate"
    except Exception as exc:  # noqa: BLE001 - the visitor still gets their redirect
        print(f"{kind}: store failed: {exc!r}")
        return "failed"


async def _deliver(
    env,
    *,
    kind: str,
    to: str,
    subject: str,
    html: str,
    attachments: list[dict] | None = None,
    reply_to: str | None = None,
) -> None:
    """Mail a submission.

    A mailapi failure is logged, never surfaced: the visitor still gets the
    same redirect, and the submission is in the submissions table either way.
    """
    try:
        await send_email(
            env,
            to=to,
            subject=subject,
            html=html,
            attachments=attachments,
            reply_to=reply_to,
        )
    except Exception as exc:  # noqa: BLE001 - the visitor still gets their redirect
        print(f"{kind}: send failed: {exc!r}")


async def _asset_bytes(env, path: str) -> bytes:
    """Read a file out of the ASSETS binding as bytes.

    Same addressing the catch-all proxy uses. Used to attach the ticket, which
    is served from the same static assets as the site itself.
    """
    resp = await env.ASSETS.fetch(f"https://assets.local/{path.lstrip('/')}")
    status_code = getattr(resp, "status", 200)
    if status_code != 200:
        raise RuntimeError(f"asset {path} returned HTTP {status_code}")
    raw = await resp.bytes()
    # The runtime may hand back a JS typed array; unwrap it if so.
    to_py = getattr(raw, "to_py", None)
    if callable(to_py):
        raw = to_py()
    return bytes(raw)


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
        await _record(env, kind="contact", request=request, **value)
        await _deliver(
            env,
            kind="contact",
            to=_env_value(env, "EMAIL_TO"),
            subject=f"Contact Form: {value['name']}",
            html=notification(
                heading="New contact message",
                preheader=f"{value['name']} · {value['email']}",
                rows=[
                    ("Name", value["name"]),
                    ("Email", value["email"]),
                    ("Message", value["message"]),
                ],
                note=f"Replying to this email answers {value['name']} directly.",
            ),
            reply_to=value["email"],
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
        "party_size": partysize.strip(),
        "booking_date": format_date(date),
        "booking_time": time.strip(),
    }

    reason = await spam.screen(website=website, elapsed=elapsed)
    if reason:
        print(f"reserve-table: dropped submission ({reason})")
    elif not all(value.values()) or "@" not in value["email"]:
        print(f"reserve-table: invalid submission (name/email/phone/partysize/date/time required): {value!r}")
    else:
        await _record(env, kind="reservation", request=request, **value)
        await _deliver(
            env,
            kind="reservation",
            to=_env_value(env, "EMAIL_TO"),
            subject=f"Table Reservation: {value['name']}",
            html=notification(
                heading="New table reservation",
                preheader=(
                    f"{value['name']} · {value['booking_date']} at "
                    f"{value['booking_time']} · party of {value['party_size']}"
                ),
                rows=[
                    ("Name", value["name"]),
                    ("Email", value["email"]),
                    ("Phone", value["phone"]),
                    ("Party Size", value["party_size"]),
                    ("Date", value["booking_date"]),
                    ("Time", value["booking_time"]),
                ],
                note=f"Confirm with the guest by replying to this email or calling {value['phone']}.",
            ),
            reply_to=value["email"],
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

    One ticket per address: the insert refuses a repeat claim, and a repeat is
    answered with the ordinary thank-you and nothing else - no second ticket, no
    second row. The organisers read claims from the submissions table; philex
    does not get a copy. If the ticket art cannot be read, the claim is still
    mailed with a link to it - a storage hiccup must not strand a guest at the
    door.
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
        # Stored before it is mailed: with no notification going to philex, this
        # row is what the organisers work from. `once_per_email` makes the
        # insert itself refuse a second claim from the same address.
        outcome = await _record(env, kind="free-ticket", request=request, once_per_email=True, **value)
        if outcome == "duplicate":
            print(f"free-ticket: {value['email']} has already claimed, no ticket sent")
        else:
            attachments: list[dict] = []
            try:
                ticket = await _asset_bytes(env, TICKET_ASSET)
                attachments.append(attachment(TICKET_FILENAME, ticket, "image/png"))
            except Exception as exc:  # noqa: BLE001 - fall back to linking the ticket
                print(f"free-ticket: ticket attachment unavailable: {exc!r}")

            await _deliver(
                env,
                kind="free-ticket",
                to=value["email"],
                subject=TICKET_SUBJECT,
                html=ticket_delivery(name=value["name"], attached=bool(attachments)),
                attachments=attachments,
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
