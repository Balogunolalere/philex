"""Unit tests for src/db.py (the Turso write path).

Plain stdlib only — no pytest, no Turso account, no network. The Workers
runtime ``fetch`` is stubbed, so these assert the request this Worker makes
(the part that can silently break against a live database) and how it reacts
when the server refuses one.

Run: python3 test_db.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from db import COLUMNS, INSERT_SQL, endpoint, save_submission  # noqa: E402


class Env:
    """Stands in for the Workers env bindings."""

    def __init__(self, **values):
        for key, value in values.items():
            setattr(self, key, value)


class StubResponse:
    def __init__(self, status=200, body=None):
        self.status = status
        self._body = body if body is not None else {
            "results": [
                {
                    "type": "ok",
                    "response": {
                        "type": "execute",
                        "result": {"rows_written": 1, "last_insert_rowid": 42},
                    },
                }
            ]
        }

    async def json(self):
        return self._body


def recorder(status=200, body=None):
    """A stub _post that captures its call and returns a canned response."""
    calls = []

    async def post(url, token, payload):
        calls.append({"url": url, "token": token, "payload": payload})
        return StubResponse(status, body)

    post.calls = calls
    return post


def good_env(**extra):
    return Env(
        TURSO_DATABASE_URL="libsql://philex-doombuggy.aws-us-east-1.turso.io",
        TURSO_AUTH_TOKEN="tok",
        **extra,
    )


# --- URL handling ------------------------------------------------------------
def test_libsql_scheme_becomes_https():
    assert (
        endpoint("libsql://philex-doombuggy.aws-us-east-1.turso.io")
        == "https://philex-doombuggy.aws-us-east-1.turso.io/v2/pipeline"
    )


def test_other_schemes_and_shapes_are_accepted():
    assert endpoint("https://db.example.turso.io/") == "https://db.example.turso.io/v2/pipeline"
    assert endpoint("http://localhost:8080") == "https://localhost:8080/v2/pipeline"
    assert endpoint("db.example.turso.io") == "https://db.example.turso.io/v2/pipeline"


def test_missing_url_fails_loudly():
    try:
        endpoint("")
    except RuntimeError as exc:
        assert "TURSO_DATABASE_URL" in str(exc), "the error must name the missing variable"
    else:
        raise AssertionError("an empty URL must raise")


# --- the insert --------------------------------------------------------------
def test_insert_covers_every_column():
    for column in COLUMNS:
        assert column in INSERT_SQL, f"{column} is missing from the insert"
    assert INSERT_SQL.count("?") == len(COLUMNS), "one placeholder per column"
    assert INSERT_SQL.startswith("insert into submissions"), INSERT_SQL


def test_sends_one_pipeline_request_with_every_value():
    post = recorder()

    result = asyncio.run(
        save_submission(
            good_env(),
            kind="free-ticket",
            name="Ada Lovelace",
            email="ada@example.com",
            phone="0801",
            ip="203.0.113.7",
            user_agent="test-agent",
            post=post,
        )
    )

    assert len(post.calls) == 1, "one round trip per submission"
    call = post.calls[0]
    assert call["url"] == "https://philex-doombuggy.aws-us-east-1.turso.io/v2/pipeline"
    assert call["token"] == "tok"

    requests = call["payload"]["requests"]
    assert requests[-1] == {"type": "close"}, "the pipeline stream is closed"
    stmt = requests[0]["stmt"]
    assert stmt["sql"] == INSERT_SQL
    # Args are positional, so their order has to match COLUMNS exactly.
    assert stmt["args"] == [
        {"type": "text", "value": "free-ticket"},
        {"type": "text", "value": "Ada Lovelace"},
        {"type": "text", "value": "ada@example.com"},
        {"type": "text", "value": "0801"},
        {"type": "text", "value": ""},  # message
        {"type": "text", "value": ""},  # party_size
        {"type": "text", "value": ""},  # booking_date
        {"type": "text", "value": ""},  # booking_time
        {"type": "text", "value": "203.0.113.7"},
        {"type": "text", "value": "test-agent"},
    ]

    assert result["status"] == "stored"
    assert result["rows_written"] == 1
    assert result["row_id"] == 42


def test_the_payload_is_json_serialisable():
    """The body is json.dumps'ed by _post, so every value must survive it."""
    post = recorder()
    asyncio.run(save_submission(good_env(), kind="contact", name="Ada", post=post))

    json.dumps(post.calls[0]["payload"])


# --- failure handling --------------------------------------------------------
def test_missing_token_fails_loudly():
    try:
        asyncio.run(save_submission(Env(TURSO_DATABASE_URL="libsql://x.turso.io"), kind="contact", post=recorder()))
    except RuntimeError as exc:
        assert "TURSO_AUTH_TOKEN" in str(exc), "the error must name the missing variable"
    else:
        raise AssertionError("a missing token must raise")


def test_an_error_result_raises_with_the_servers_message():
    body = {
        "results": [
            {"type": "error", "error": {"message": "SQLite error: no such table: submissions"}}
        ]
    }
    try:
        asyncio.run(save_submission(good_env(), kind="contact", post=recorder(body=body)))
    except RuntimeError as exc:
        assert "no such table" in str(exc), str(exc)
    else:
        raise AssertionError("a rejected insert must raise")


def test_a_non_2xx_status_raises_with_the_status():
    body = {"error": "Unauthorized: `unauthorized access attempt on database: invalid JWT token`"}
    try:
        asyncio.run(save_submission(good_env(), kind="contact", post=recorder(status=401, body=body)))
    except RuntimeError as exc:
        assert "401" in str(exc), str(exc)
    else:
        raise AssertionError("a 401 must raise, so the caller can log it")


def test_a_2xx_without_a_body_counts_as_stored():
    class Empty(StubResponse):
        async def json(self):
            raise ValueError("no body")

    async def post(url, token, payload):
        return Empty()

    result = asyncio.run(save_submission(good_env(), kind="contact", post=post))
    assert result["status"] == "stored"


# --- the real transport ------------------------------------------------------
def test_post_builds_the_request_with_the_runtime_fetch():
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
        from db import _post

        asyncio.run(_post("https://db.example.turso.io/v2/pipeline", "tok", {"requests": []}))

        assert captured["url"] == "https://db.example.turso.io/v2/pipeline"
        kwargs = captured["kwargs"]
        assert kwargs["method"] == "POST"
        assert kwargs["headers"]["authorization"] == "Bearer tok"
        assert kwargs["headers"]["content-type"] == "application/json"
        assert json.loads(kwargs["body"]) == {"requests": []}
    finally:
        if previous is None:
            del sys.modules["workers"]
        else:
            sys.modules["workers"] = previous


def main():
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"PASS {test.__name__[5:]}")
    print(f"\n{len(tests)} db tests passed")


if __name__ == "__main__":
    main()
