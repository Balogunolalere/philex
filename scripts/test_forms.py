"""Tests for the form endpoints, the storage write and the spam screen.

Plain stdlib only — no pytest, no Cloudflare runtime, no network. The Workers
runtime, the mailer transport and the Turso client are stubbed, so these assert
what the site does with a submission: what gets stored, what gets mailed to
whom, and what is dropped.

Run: python3 test_forms.py
"""

from __future__ import annotations

import asyncio
import base64
import inspect
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# --- stub the Workers runtime bits that only exist on Cloudflare -------------
workers_module = types.ModuleType("workers")


class WorkerEntrypoint:  # noqa: D101 - stand-in for the runtime base class
    pass


workers_module.WorkerEntrypoint = WorkerEntrypoint
sys.modules["workers"] = workers_module

sent: list[dict] = []
stored: list[dict] = []


async def fake_send_email(env, *, to, subject, html, attachments=None, reply_to=None, **_ignored):
    sent.append(
        {
            "to": to,
            "subject": subject,
            "html": html,
            "attachments": attachments,
            "reply_to": reply_to,
        }
    )
    return {"status": "sent"}


async def fake_save_submission(
    env,
    *,
    kind,
    name="",
    email="",
    phone="",
    message="",
    party_size="",
    booking_date="",
    booking_time="",
    ip="",
    user_agent="",
    **_ignored,
):
    """Mirrors the real client's signature so a test row has every column."""
    stored.append(
        {
            "kind": kind,
            "name": name,
            "email": email,
            "phone": phone,
            "message": message,
            "party_size": party_size,
            "booking_date": booking_date,
            "booking_time": booking_time,
            "ip": ip,
            "user_agent": user_agent,
        }
    )
    return {"status": "stored"}


# The transports are stubbed by patching the real modules, so everything else in
# them (attachment encoding, template rendering) runs as it does in production.
import db as db_module  # noqa: E402
import mailer as mailer_module  # noqa: E402

mailer_module.send_email = fake_send_email
db_module.save_submission = fake_save_submission

import spam  # noqa: E402
import worker  # noqa: E402

# The real ticket art, so the attachment assertion is about the file the site
# actually ships.
TICKET_BYTES = (Path(__file__).resolve().parent.parent / worker.TICKET_ASSET).read_bytes()


class Headers:
    def __init__(self, **values):
        self.values = {k.lower().replace("_", "-"): v for k, v in values.items()}

    def get(self, key, default=None):
        return self.values.get(key.lower(), default)


class AssetResponse:
    def __init__(self, body: bytes, status: int = 200):
        self.status = status
        self._body = body

    async def bytes(self):
        return self._body


class Assets:
    """Stands in for the ASSETS binding, which serves ./dist in production."""

    def __init__(self, body: bytes = TICKET_BYTES, status: int = 200):
        self.body = body
        self.status = status
        self.urls: list[str] = []

    async def fetch(self, url):
        self.urls.append(url)
        if self.status != 200:
            return AssetResponse(b"", self.status)
        return AssetResponse(self.body)


class Env:
    EMAIL_TO = "info@philexentertainment.com"

    def __init__(self, assets=None, **values):
        self.ASSETS = assets if assets is not None else Assets()
        for key, value in values.items():
            setattr(self, key, value)


class Request:
    def __init__(self, ip="203.0.113.7", agent="test-agent", env=None):
        self.scope = {"env": env if env is not None else Env()}
        self.headers = Headers(**{"cf-connecting-ip": ip, "user-agent": agent})


def call(endpoint, env=None, ip="203.0.113.7", agent="test-agent", **fields):
    """Call an endpoint the way FastAPI does: every field it declares present, as a string.

    The ``sent``/``stored`` recorders keep accumulating across calls within a
    test; the runner empties them before each one.
    """
    accepted = set(inspect.signature(endpoint).parameters) - {"request"}
    payload = dict.fromkeys(accepted, "")
    payload.update({key: value for key, value in fields.items() if key in accepted})
    return asyncio.run(endpoint(request=Request(ip=ip, agent=agent, env=env), **payload))


FORM_FIELDS = {"website": "", "elapsed": "8000"}


# --- the spam screen ---------------------------------------------------------
def test_honeypot_is_caught():
    assert spam.looks_automated(website="http://spam.example", elapsed="9000")


def test_instant_submission_is_caught():
    assert spam.looks_automated(website="", elapsed="120")


def test_missing_elapsed_is_not_judged():
    assert spam.looks_automated(website="", elapsed="") is None
    assert spam.looks_automated(website="", elapsed="not-a-number") is None


def test_genuine_submission_passes():
    assert spam.looks_automated(website="", elapsed="8000") is None


def test_screen_agrees_with_the_stateless_layers():
    """`screen` is what the endpoints call; it must return the same verdicts."""
    assert asyncio.run(spam.screen(website="http://spam.example", elapsed="9000"))
    assert asyncio.run(spam.screen(website="", elapsed="100"))
    assert asyncio.run(spam.screen(website="", elapsed="9000")) is None


# --- storage -----------------------------------------------------------------
def test_every_form_is_stored_with_its_own_kind():
    call(worker.contact_us, name="Ada", email="ada@example.com", message="Hello", **FORM_FIELDS)
    call(
        worker.reserve_table,
        name="Ada",
        email="ada@example.com",
        phone="08012345678",
        partysize="4",
        date="11/10/2026",
        time="19:00",
        **FORM_FIELDS,
    )
    call(worker.free_ticket, name="Ada", email="ada@example.com", phone="0801", **FORM_FIELDS)

    # One row per submission, in the order the forms were posted.
    assert [row["kind"] for row in stored] == ["contact", "reservation", "free-ticket"]
    assert stored[0]["message"] == "Hello"
    assert stored[1]["party_size"] == "4"
    assert stored[1]["booking_date"] == "October 11, 2026", "the stored date is the rendered one"
    assert stored[2]["phone"] == "0801"


def test_stored_rows_carry_the_client_info():
    call(worker.contact_us, name="Ada", email="ada@example.com", message="Hi", ip="198.51.100.9", agent="curl/8", **FORM_FIELDS)

    assert stored[0]["ip"] == "198.51.100.9"
    assert stored[0]["user_agent"] == "curl/8"
    # Columns that this form does not use stay empty rather than missing.
    assert stored[0]["phone"] == "" and stored[0]["party_size"] == ""


def test_spam_is_never_stored():
    call(worker.contact_us, name="Ada", email="ada@example.com", message="hi", website="http://spam.example")

    assert stored == [], "a honeypot hit must not reach the database"


def test_invalid_submission_is_never_stored():
    call(worker.contact_us, name="", email="", message="", **FORM_FIELDS)

    assert stored == [] and sent == []


def test_a_storage_failure_still_mails_and_redirects():
    """The inbox is not the only record any more, but a row must not gate the rest."""

    async def broken_save(env, **_kw):
        raise RuntimeError("turso is down")

    worker.save_submission = broken_save
    try:
        response = call(worker.contact_us, name="Ada", email="ada@example.com", message="Hi", **FORM_FIELDS)
    finally:
        worker.save_submission = fake_save_submission

    assert len(sent) == 1, "the notification must still go out"
    assert response.status_code == 302


# --- the three endpoints -----------------------------------------------------
def test_contact_is_mailed():
    response = call(worker.contact_us, name="Ada", email="ada@example.com", message="Hello", **FORM_FIELDS)
    assert len(sent) == 1 and sent[0]["subject"] == "Contact Form: Ada"
    assert "Hello" in sent[0]["html"]
    assert response.headers["location"] == "/contact"


def test_reservation_keeps_the_booking_details():
    response = call(
        worker.reserve_table,
        name="Ada",
        email="ada@example.com",
        phone="08012345678",
        partysize="4",
        date="11/10/2026",
        time="19:00",
        **FORM_FIELDS,
    )
    assert len(sent) == 1, "a complete reservation must be mailed"
    html = sent[0]["html"]
    assert "08012345678" in html
    assert "October 11, 2026" in html, "the date must be rendered, not echoed raw"
    assert response.headers["location"] == "/bar"


def test_reservation_without_contact_details_is_rejected():
    call(worker.reserve_table, name="Ada", email="", phone="", partysize="2", date="11/10/2026", time="19:00", **FORM_FIELDS)
    assert sent == [], "the form now requires name/email/phone"


def test_internal_mail_goes_to_philex_and_replies_to_the_visitor():
    call(worker.contact_us, name="Ada", email="ada@example.com", message="Hello", **FORM_FIELDS)

    assert sent[0]["to"] == "info@philexentertainment.com"
    assert sent[0]["reply_to"] == "ada@example.com", "hitting reply must answer the visitor"
    assert sent[0]["attachments"] is None


def test_free_ticket_claim_goes_to_the_guest_with_the_ticket_attached():
    response = call(worker.free_ticket, name="Ada Lovelace", email="ada@example.com", phone="0801", **FORM_FIELDS)

    assert len(sent) == 1
    mail = sent[0]
    assert mail["to"] == "ada@example.com", "the claimant gets the ticket, not philex"
    assert "info@philexentertainment.com" not in mail["to"]
    assert mail["subject"] == "Your free ticket — The BLAQ Xperience"
    assert "Ada," in mail["html"], "the guest is greeted by name"
    assert "The BLAQ Xperience" in mail["html"] and "Jogor Centre" in mail["html"]

    assert mail["attachments"] and len(mail["attachments"]) == 1
    ticket = mail["attachments"][0]
    assert ticket["filename"] == "The-BLAQ-Xperience-Ticket.png"
    assert ticket["contentType"] == "image/png"
    assert base64.b64decode(ticket["content"]) == TICKET_BYTES, "the attachment is the shipped ticket file"

    assert response.headers["location"] == "/bar?claimed=1"


def test_free_ticket_claim_is_stored_for_the_organisers():
    call(worker.free_ticket, name="Ada", email="ada@example.com", phone="0801", **FORM_FIELDS)

    assert len(stored) == 1 and stored[0]["kind"] == "free-ticket"
    assert stored[0]["name"] == "Ada" and stored[0]["email"] == "ada@example.com"


def test_a_missing_ticket_asset_still_mails_a_link():
    """A guest must not be stranded because the asset store hiccuped."""
    env = Env(assets=Assets(status=500))

    call(worker.free_ticket, env=env, name="Ada", email="ada@example.com", phone="0801", **FORM_FIELDS)

    assert len(sent) == 1, "the claim is still delivered"
    mail = sent[0]
    assert not mail["attachments"], "there is no file to attach"
    assert "no ticket" in mail["html"].lower() or "link below" in mail["html"]
    assert worker.TICKET_ASSET.split("/")[-1] not in mail["html"]
    assert "blaq-xperience-ticket-1400.png" in mail["html"], "the mail links the public ticket instead"
    assert stored, "the claim is still recorded"


def test_ticket_asset_is_read_from_the_static_assets():
    env = Env()
    call(worker.free_ticket, env=env, name="Ada", email="ada@example.com", phone="0801", **FORM_FIELDS)

    assert env.ASSETS.urls == [f"https://assets.local/{worker.TICKET_ASSET}"]


def test_spam_is_never_mailed():
    for endpoint, fields in (
        (worker.contact_us, {"name": "Ada", "email": "ada@example.com", "message": "hi"}),
        (worker.free_ticket, {"name": "Ada", "email": "ada@example.com", "phone": "0801"}),
    ):
        call(endpoint, **{**fields, "website": "http://spam.example"})
        assert sent == [], f"honeypot must drop the {endpoint.__name__} submission"
        call(endpoint, **{**fields, "elapsed": "50"})
        assert sent == [], f"instant posts must drop the {endpoint.__name__} submission"


def test_a_mail_failure_still_redirects():
    """The visitor's redirect must not depend on mailapi being up."""

    async def broken_send(env, **_kw):
        raise RuntimeError("mailapi is down")

    worker.send_email = broken_send
    try:
        response = call(worker.free_ticket, name="Ada", email="ada@example.com", phone="0801", **FORM_FIELDS)
    finally:
        worker.send_email = fake_send_email
    assert sent == [] and response.status_code == 302
    assert response.headers["location"] == "/bar?claimed=1"
    assert stored, "the claim is still recorded when the mail fails"


# --- the mail templates ------------------------------------------------------
def test_visitor_input_is_escaped_in_every_mail():
    call(
        worker.contact_us,
        name='<script>alert("x")</script>',
        email="ada@example.com",
        message="<b>bold</b> & <i>italic</i>",
        **FORM_FIELDS,
    )
    html = sent[0]["html"]
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "<b>bold</b>" not in html and "&lt;b&gt;bold&lt;/b&gt;" in html
    assert "&amp; &lt;i&gt;" in html

    call(worker.free_ticket, name="<Ada>", email="ada@example.com", phone="0801", **FORM_FIELDS)
    assert "<Ada>" not in sent[0]["html"]


def test_message_newlines_survive_as_breaks():
    call(worker.contact_us, name="Ada", email="ada@example.com", message="first\nsecond", **FORM_FIELDS)

    assert "first<br />second" in sent[0]["html"]


def test_mails_carry_the_brand_and_a_footer():
    call(worker.contact_us, name="Ada", email="ada@example.com", message="Hi", **FORM_FIELDS)
    html = sent[0]["html"]

    assert "Philex Entertainment" in html
    assert "philex-logo.png" in html
    assert "info@philexentertainment.com" in html
    assert "#c49871" in html, "the brand gold carries into the mail"
    assert "<table" in html, "email clients need table layout"


def main():
    failures = 0
    for name, test in sorted(globals().items()):
        if name.startswith("test_") and callable(test):
            sent.clear()
            stored.clear()
            try:
                test()
                print(f"ok   {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
    print("all passed" if not failures else f"{failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
