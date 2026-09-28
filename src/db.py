"""Store form submissions in Turso, straight from the Worker.

The Worker has no libSQL binding, so this speaks the platform's HTTP pipeline
API (``POST <database-url>/v2/pipeline``) with ``fetch``. One round trip per
submission, no connection to hold onto, no WebSocket - which is the only shape
that suits a request-scoped Worker.

Rows land in ``submissions`` (see ``docs/schema.sql`` for the table). The
columns are deliberately one flat row per submission: the panel and the inbox
both read from here, and a form field that means nothing to one form stays
empty rather than becoming its own table.

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

__all__ = ["INSERT_SQL", "save_submission"]

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


def _as_python(value):
    """Unwrap the runtime's JS values so the rest of the module sees Python.

    Cloudflare's Python runtime hands back either real Python objects or JS
    proxies depending on the call, and a dict-like proxy supports ``to_py``.
    """
    to_py = getattr(value, "to_py", None)
    return to_py() if callable(to_py) else value


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
    post=None,
) -> dict:
    """Insert one submission. Raises RuntimeError when the row was not stored.

    ``kind`` is the form's name in ``submissions.kind``: ``contact``,
    ``reservation`` or ``free-ticket``. ``created_at`` is filled by the table
    default, which is UTC on the server.
    """
    if post is None:
        post = _post

    url = endpoint(getattr(env, "TURSO_DATABASE_URL", None) or "")
    token = str(getattr(env, "TURSO_AUTH_TOKEN", None) or "").strip()
    if not token:
        raise RuntimeError(
            "TURSO_AUTH_TOKEN must be set. In production: "
            "`wrangler secret put TURSO_AUTH_TOKEN`."
        )

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

    payload = {
        "requests": [
            {
                "type": "execute",
                "stmt": {
                    "sql": INSERT_SQL,
                    "args": [_text_arg(values[column]) for column in COLUMNS],
                },
            },
            {"type": "close"},
        ]
    }

    resp = await post(url, token, payload)

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
    except Exception:  # noqa: BLE001 - a 2xx with no body still means the insert ran
        return {"status": "stored"}

    results = _as_python(body).get("results") or []
    for result in results:
        result = _as_python(result)
        if _as_python(result).get("type") == "error":
            raise RuntimeError(f"turso rejected the insert: {_failure_detail(body)}")
        executed = _as_python(_as_python(result).get("response") or {}).get("result") or {}
        return {
            "status": "stored",
            "rows_written": _as_python(executed).get("rows_written"),
            "row_id": _as_python(executed).get("last_insert_rowid"),
        }
    return {"status": "stored"}
