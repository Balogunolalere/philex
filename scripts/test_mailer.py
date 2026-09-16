"""Unit tests for src/mailer.py.

Plain stdlib only — no pytest, no Cloudflare runtime. The mailapi transport is
stubbed, so these assert the *request* this Worker makes (which is the part that
can silently break) rather than re-testing the server.

Run: python3 test_mailer.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mailer import send_email  # noqa: E402


class Env:
    """Stands in for the Workers env bindings."""

    def __init__(self, **values):
        for key, value in values.items():
            setattr(self, key, value)


class StubResponse:
    def __init__(self, status=201, body=None):
        self.status = status
        self._body = body if body is not None else {"id": "<x@mailapi>", "status": "sent"}

    async def json(self):
        return self._body

    async def text(self):
        return json.dumps(self._body)


def recorder(status=201, body=None):
    """A stub _post that captures its call and returns a canned response."""
    calls = []

    async def post(base_url, api_key, payload):
        calls.append({"base_url": base_url, "api_key": api_key, "payload": payload})
        return StubResponse(status, body)

    post.calls = calls
    return post


def test_sends_expected_request():
    post = recorder()
    env = Env(MAILAPI_URL="https://mailapi.example.app", MAILAPI_KEY="k123")

    result = asyncio.run(
        send_email(env, to="info@philexentertainment.com", subject="Contact Form: Ada", html="<p>hi</p>", post=post)
    )

    assert len(post.calls) == 1, "exactly one request should be made"
    call = post.calls[0]
    assert call["base_url"] == "https://mailapi.example.app"
    assert call["api_key"] == "k123", "the key must be sent as the bearer credential"
    assert call["payload"]["to"] == ["info@philexentertainment.com"]
    assert call["payload"]["subject"] == "Contact Form: Ada"
    assert call["payload"]["html"] == "<p>hi</p>"
    assert result["status"] == "sent", "mailapi's response should be returned to the caller"
    print("PASS sends_expected_request")


def test_post_builds_the_request_correctly():
    """Exercises the real _post, with only the runtime `fetch` stubbed."""
    import types

    captured = {}

    async def fake_fetch(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return StubResponse()

    fake_workers = types.ModuleType("workers")
    fake_workers.fetch = fake_fetch
    previous = sys.modules.get("workers")
    sys.modules["workers"] = fake_workers

    try:
        from mailer import _post

        # A trailing slash must not produce a double slash in the path.
        asyncio.run(_post("https://mailapi.example.app/", "k", {"to": ["a@b.com"]}))

        assert captured["url"] == "https://mailapi.example.app/api/send", captured["url"]
        kwargs = captured["kwargs"]
        assert kwargs["method"] == "POST"
        assert kwargs["headers"]["authorization"] == "Bearer k"
        assert kwargs["headers"]["content-type"] == "application/json"
        # workers.fetch takes `body`, not `json=`, so the payload must be
        # serialised by us into a valid JSON string.
        assert json.loads(kwargs["body"]) == {"to": ["a@b.com"]}
    finally:
        if previous is None:
            del sys.modules["workers"]
        else:
            sys.modules["workers"] = previous

    print("PASS post_builds_the_request_correctly")


def test_optional_keys_are_omitted_when_unset():
    post = recorder()
    env = Env(MAILAPI_URL="https://m.example.app", MAILAPI_KEY="k")

    asyncio.run(send_email(env, to="a@b.com", subject="s", html="<p>x</p>", post=post))

    payload = post.calls[0]["payload"]
    # Omitting these lets mailapi apply its own account defaults and generate
    # the plain-text alternative itself.
    assert "fromName" not in payload, "fromName should be omitted when unset"
    assert "account" not in payload, "account should be omitted when unset"
    assert "text" not in payload, "text should be omitted so mailapi derives it from html"
    print("PASS optional_keys_are_omitted_when_unset")


def test_optional_keys_are_included_when_set():
    post = recorder()
    env = Env(
        MAILAPI_URL="https://m.example.app",
        MAILAPI_KEY="k",
        MAILAPI_ACCOUNT="philex",
        EMAIL_FROM_NAME="Philex Entertainment",
    )

    asyncio.run(send_email(env, to="a@b.com", subject="s", html="<p>x</p>", post=post))

    payload = post.calls[0]["payload"]
    assert payload["account"] == "philex"
    assert payload["fromName"] == "Philex Entertainment"
    print("PASS optional_keys_are_included_when_set")


def test_from_name_argument_beats_environment():
    post = recorder()
    env = Env(MAILAPI_URL="https://m.example.app", MAILAPI_KEY="k", EMAIL_FROM_NAME="From Env")

    asyncio.run(send_email(env, to="a@b.com", subject="s", html="<p>x</p>", from_name="Explicit", post=post))

    assert post.calls[0]["payload"]["fromName"] == "Explicit"
    print("PASS from_name_argument_beats_environment")


def test_comma_separated_recipients_are_split_and_trimmed():
    post = recorder()
    env = Env(MAILAPI_URL="https://m.example.app", MAILAPI_KEY="k")

    asyncio.run(send_email(env, to=" a@b.com , c@d.com ", subject="s", html="<p>x</p>", post=post))

    assert post.calls[0]["payload"]["to"] == ["a@b.com", "c@d.com"]
    print("PASS comma_separated_recipients_are_split_and_trimmed")


def test_subject_line_breaks_are_neutralised():
    post = recorder()
    env = Env(MAILAPI_URL="https://m.example.app", MAILAPI_KEY="k")

    asyncio.run(send_email(env, to="a@b.com", subject="ok\r\nBcc: victim@x.com", html="<p>x</p>", post=post))

    subject = post.calls[0]["payload"]["subject"]
    assert "\r" not in subject and "\n" not in subject, "header injection must be neutralised"
    assert subject == "ok  Bcc: victim@x.com"
    print("PASS subject_line_breaks_are_neutralised")


def test_missing_configuration_fails_loudly():
    env = Env(MAILAPI_URL="", MAILAPI_KEY="")

    try:
        asyncio.run(send_email(env, to="a@b.com", subject="s", html="<p>x</p>", post=recorder()))
    except RuntimeError as exc:
        assert "MAILAPI_URL" in str(exc), "the error must name the missing variable"
    else:
        raise AssertionError("missing configuration must raise")

    # A URL without a key is just as broken.
    try:
        asyncio.run(
            send_email(Env(MAILAPI_URL="https://m.example.app"), to="a@b.com", subject="s", html="<p>x</p>", post=recorder())
        )
    except RuntimeError:
        pass
    else:
        raise AssertionError("a missing key must raise")
    print("PASS missing_configuration_fails_loudly")


def test_empty_recipient_fails_loudly():
    env = Env(MAILAPI_URL="https://m.example.app", MAILAPI_KEY="k")

    try:
        asyncio.run(send_email(env, to="", subject="s", html="<p>x</p>", post=recorder()))
    except RuntimeError as exc:
        assert "recipient" in str(exc).lower()
    else:
        raise AssertionError("an empty recipient must raise")
    print("PASS empty_recipient_fails_loudly")


def test_mailapi_error_surfaces_the_code_and_message():
    post = recorder(status=403, body={"error": {"code": "from_not_allowed", "message": "Account cannot send from that address."}})
    env = Env(MAILAPI_URL="https://m.example.app", MAILAPI_KEY="k")

    try:
        asyncio.run(send_email(env, to="a@b.com", subject="s", html="<p>x</p>", post=post))
    except RuntimeError as exc:
        message = str(exc)
        assert "403" in message, message
        assert "from_not_allowed" in message, "the caller should see mailapi's error code"
        assert "cannot send" in message, "and its human-readable message"
    else:
        raise AssertionError("a 4xx from mailapi must raise")
    print("PASS mailapi_error_surfaces_the_code_and_message")


def test_non_json_error_response_still_reports_status():
    post = recorder(status=502, body="<html>Bad Gateway</html>")
    env = Env(MAILAPI_URL="https://m.example.app", MAILAPI_KEY="k")

    # The stub's json() returns a str, so the dict branch is skipped and the
    # generic status path is exercised.
    try:
        asyncio.run(send_email(env, to="a@b.com", subject="s", html="<p>x</p>", post=post))
    except RuntimeError as exc:
        assert "502" in str(exc), str(exc)
    else:
        raise AssertionError("a 5xx from mailapi must raise")
    print("PASS non_json_error_response_still_reports_status")


def main():
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
    print(f"\n{len(tests)} mailer tests passed")


if __name__ == "__main__":
    main()
