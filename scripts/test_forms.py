"""Tests for the form endpoints and the spam screen.

Plain stdlib only — no pytest, no Cloudflare runtime, no network. The Workers
runtime and mailer transport are stubbed, so these assert what the site does
with a submission: what gets mailed, and what is dropped.

Run: python3 test_forms.py
"""

from __future__ import annotations

import asyncio
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


async def fake_send_email(env, *, to, subject, html, **_ignored):
    sent.append({"to": to, "subject": subject, "html": html})
    return {"status": "sent"}


mailer_module = types.ModuleType("mailer")
mailer_module.send_email = fake_send_email
sys.modules["mailer"] = mailer_module

import spam  # noqa: E402
import worker  # noqa: E402


class Headers:
    def __init__(self, **values):
        self.values = {k.lower().replace("_", "-"): v for k, v in values.items()}

    def get(self, key, default=None):
        return self.values.get(key.lower(), default)


class Env:
    EMAIL_TO = "info@philexentertainment.com"


class Request:
    def __init__(self, ip="203.0.113.7", agent="test-agent"):
        self.scope = {"env": Env()}
        self.headers = Headers(**{"cf-connecting-ip": ip, "user-agent": agent})


def call(endpoint, **fields):
    """Call an endpoint the way FastAPI does: every field it declares present, as a string."""
    accepted = set(inspect.signature(endpoint).parameters) - {"request"}
    payload = dict.fromkeys(accepted, "")
    payload.update({key: value for key, value in fields.items() if key in accepted})
    sent.clear()
    return asyncio.run(endpoint(request=Request(), **payload))


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


def test_free_ticket_claim_is_mailed():
    response = call(worker.free_ticket, name="Ada", email="ada@example.com", phone="0801", **FORM_FIELDS)
    assert len(sent) == 1 and sent[0]["subject"] == "Free Ticket Claim: Ada"
    assert "0801" in sent[0]["html"]
    assert response.headers["location"] == "/bar?claimed=1"


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


def main():
    failures = 0
    for name, test in sorted(globals().items()):
        if name.startswith("test_") and callable(test):
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
