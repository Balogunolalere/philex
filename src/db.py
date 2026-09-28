"""Store form submissions in Turso, straight from the Worker.

The Worker has no libSQL binding, so this speaks the platform's HTTP pipeline
API (``POST <database-url>/v2/pipeline``) with ``fetch``. One round trip per
submission, no connection to hold onto, no WebSocket - which is the only shape
that suits a request-scoped Worker.

Rows land in ``submissions`` (see ``docs/schema.sql`` for the table and its
indexes). The columns are deliberately one flat row per submission: the panel
and the inbox both read from here, and a form field that means nothing to one
form stays empty rather than becoming its own table.

One ticket per address is enforced **by the insert itself**, not by a check
before it: the guarded statement (``INSERT_ONCE_SQL``) only writes when the
address has not claimed, and SQLite serialises writers, so two claims posted at
the same moment cannot both land. That statement reports how many rows it
touched, and zero means the claim was a repeat - surfaced here as
``DuplicateClaim`` so the caller can stay quiet instead of reporting a storage
failure. A unique index would say the same thing, but it cannot be created over
the duplicates that predate this rule.

Configuration comes from the Worker ``env`` bindings (Cloudflare secrets in
production, ``.dev.vars`` locally):

  TURSO_DATABASE_URL  ``libsql://…`` or ``https://…`` for the database.
  TURSO_AUTH_TOKEN    A database token. Read-write is required for writes;
                      ``turso db tokens create <database>`` mints one.

Like the mailer, this raises on failure and lets the caller decide: a lost row
must never cost the visitor their redirect.
"""

from __future__ import annotations

import json

__all__ = [
    "COLUMNS",
    "DuplicateClaim",
    "INSERT_ONCE_SQL",
    "INSERT_SQL",
    "normalise_email",
    "save_submission",
]

# Column order here and in VALUES must stay in step.
COLUMNS = (
    "kind",
    "name",
    "email",
    "phone",
    "message",
    "party_size",
    "booking_date",
    "booking_time",
    "ip",
    "user_agent",
)

INSERT_SQL = (
    f"insert into submissions ({', '.join(COLUMNS)}) "
    f"values ({', '.join('?' for _ in COLUMNS)})"
)

# The same insert, but it writes only when this address has not already claimed
# a ticket - one statement, so the check and the write cannot be split by
# another claim arriving in between. Addresses are compared the way people type
# them (case-folded, trimmed), so "Ada@Example.com " is "ada@example.com".
# Takes the ten column values plus the address to match against.
INSERT_ONCE_SQL = (
    f"insert into submissions ({', '.join(COLUMNS)}) "
    f"select {', '.join('?' for _ in COLUMNS)} "
    "where not exists ("
    "select 1 from submissions where kind = 'free-ticket' and lower(trim(email)) = ?"
    ")"
)


class DuplicateClaim(RuntimeError):
    """This address already has a ticket: the guarded insert wrote nothing."""


def _as_python(value):
    """Unwrap the runtime's JS values so the rest of the module sees Python.

    Cloudflare's Python runtime hands back either real Python objects or JS
    proxies depending on the call, and a dict-like proxy supports ``to_py``.
    """
    to_py = getattr(value, "to_py", None)
    return to_py() if callable(to_py) else value


def normalise_email(value: object) -> str:
    return str(value or "").strip().casefold()


def endpoint(raw_url: str) -> str:
    """Turn a database URL into its HTTP pipeline endpoint.

    ``libsql://`` is the scheme Turso shows in the dashboard and the one
    ``@libsql/client`` takes; over HTTP it is plain ``https://``.
    """
    url = str(raw_url or "").strip().rstrip("/")
    if not url:
        raise RuntimeError(
            "TURSO_DATABASE_URL must be set. In production: "
            "`wrangler secret put TURSO_DATABASE_URL`."
        )
    for scheme in ("libsql://", "http://"):
        if url.startswith(scheme):
            url = "https://" + url[len(scheme) :]
            break
    if not url.startswith("https://"):
        url = "https://" + url
    return f"{url}/v2/pipeline"


def _text_arg(value: object) -> dict:
    return {"type": "text", "value": "" if value is None else str(value)}


async def _post(url: str, token: str, payload: dict):
    """POST to the pipeline endpoint using the Workers runtime fetch."""
    from workers import fetch

    return await fetch(
        url,
        method="POST",
        headers={
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
            "user-agent": "philex-worker",
        },
        body=json.dumps(payload),
    )


def _failure_detail(body) -> str:
    """Pull the server's own message out of a failed pipeline response."""
    for result in _as_python(body).get("results") or []:
        result = _as_python(result)
        error = _as_python(result).get("error") or {}
        message = _as_python(error).get("message")
        if message:
            return str(message)
    return json.dumps(_as_python(body))[:300]


def _credentials(env) -> tuple[str, str]:
    url = endpoint(getattr(env, "TURSO_DATABASE_URL", None) or "")
    token = str(getattr(env, "TURSO_AUTH_TOKEN", None) or "").strip()
    if not token:
        raise RuntimeError(
            "TURSO_AUTH_TOKEN must be set. In production: "
            "`wrangler secret put TURSO_AUTH_TOKEN`."
        )
    return url, token


async def _execute(env, sql: str, args: list[dict], post) -> dict:
    """Run one statement. Returns the parsed response body.

    Raises RuntimeError (or DuplicateClaim) when the server refused it, so
    callers can log and carry on serving the visitor.
    """
    url, token = _credentials(env)
    resp = await post(
        url,
        token,
        {
            "requests": [
                {"type": "execute", "stmt": {"sql": sql, "args": args}},
                {"type": "close"},
            ]
        },
    )

    status = getattr(resp, "status", None)
    if status is None or not 200 <= int(status) < 300:
        detail = ""
        try:
            detail = _failure_detail(_as_python(await resp.json()))
        except Exception:  # noqa: BLE001 - the status alone is still worth reporting
            detail = ""
        raise RuntimeError(f"turso returned HTTP {status}{f': {detail}' if detail else ''}")

    try:
        body = _as_python(await resp.json())
    except Exception:  # noqa: BLE001 - a 2xx with no body still means it ran
        return {}

    for result in _as_python(body).get("results") or []:
        result = _as_python(result)
        if _as_python(result).get("type") == "error":
            detail = _failure_detail(body)
            if "unique constraint" in detail.lower():
                raise DuplicateClaim(detail)
            raise RuntimeError(f"turso rejected the statement: {detail}")
    return _as_python(body)


def _first_result(body) -> dict:
    for result in _as_python(body).get("results") or []:
        result = _as_python(result)
        if _as_python(result).get("type") == "ok":
            executed = _as_python(_as_python(result).get("response") or {}).get("result") or {}
            return _as_python(executed) or {}
    return {}


async def save_submission(
    env,
    *,
    kind: str,
    name: str = "",
    email: str = "",
    phone: str = "",
    message: str = "",
    party_size: str = "",
    booking_date: str = "",
    booking_time: str = "",
    ip: str = "",
    user_agent: str = "",
    once_per_email: bool = False,
    post=None,
) -> dict:
    """Insert one submission. Raises RuntimeError when the row was not stored.

    With ``once_per_email`` (the free-ticket claim) the insert writes nothing
    when that address has already claimed, and ``DuplicateClaim`` is raised
    instead - the caller stays quiet and no second ticket goes out.

    ``kind`` is the form's name in ``submissions.kind``: ``contact``,
    ``reservation`` or ``free-ticket``. ``created_at`` is filled by the table
    default, which is UTC on the server.
    """
    if post is None:
        post = _post

    values = {
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

    args = [_text_arg(values[column]) for column in COLUMNS]
    sql = INSERT_SQL
    if once_per_email:
        sql = INSERT_ONCE_SQL
        args.append(_text_arg(normalise_email(email)))

    body = await _execute(env, sql, args, post)

    executed = _first_result(body)
    written = executed.get("affected_row_count")
    if written is None:
        written = executed.get("rows_written")
    # Only a definite zero means "already claimed". If the server said nothing
    # about the count, the guarded insert either wrote the row or found one, and
    # a guest is only lost if we wrongly stay silent - so treat it as stored.
    if once_per_email and written == 0:
        raise DuplicateClaim(f"{normalise_email(email)} has already claimed a ticket")

    return {
        "status": "stored",
        "rows_written": written,
        "row_id": executed.get("last_insert_rowid"),
    }
