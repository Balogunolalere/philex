"""Send email through the mailapi HTTP service.

This Worker does not speak SMTP itself. It POSTs to a ``mailapi`` deployment
(see the sibling ``mailapi`` project), which owns the Hostinger SMTP accounts
and does the actual delivery. That keeps the mailbox password out of this
project entirely: all this Worker needs is the mailapi URL and one API key.

Configuration comes from the Worker ``env`` bindings (Cloudflare secrets in
production, ``.dev.vars`` locally):

  MAILAPI_URL   Base URL of the mailapi deployment, no trailing slash.
  MAILAPI_KEY   API key for this project. Held by Cloudflare as a secret.
  MAILAPI_ACCOUNT  Optional: which mailapi SMTP account to send from. Needed
                 only when the key is scoped to more than one account.
  EMAIL_FROM_NAME  Optional: override the display name on the From header.
                 When unset, mailapi's own account ``fromName`` is used.

``EMAIL_TO`` (the recipient) is read by the caller in ``worker.py``.
"""

from __future__ import annotations

import json

__all__ = ["send_email"]


def _single_value(value) -> str:
    return value if value is not None else ""


async def _post(base_url: str, api_key: str, payload: dict):
    """POST JSON to mailapi using the Workers runtime fetch.

    Imported lazily like the rest of the runtime FFI so the module stays
    importable outside a Worker (tests, linting). No client-side timeout is set:
    the runtime bounds outbound fetches, and mailapi bounds its own SMTP work.
    """
    from workers import fetch

    return await fetch(
        f"{base_url.rstrip('/')}/api/send",
        method="POST",
        headers={
            "authorization": f"Bearer {api_key}",
            "content-type": "application/json",
            "user-agent": "philex-worker",
        },
        body=json.dumps(payload),
    )


async def _describe_failure(resp) -> str:
    """Pull mailapi's JSON error out of a failed response.

    Reading the body can raise when the response was not JSON (or when the
    platform already rejected it), and an error message must never be the
    reason a form submission fails, so this is best effort.
    """
    status = getattr(resp, "status", "?")
    try:
        body = await resp.json()
    except Exception:  # noqa: BLE001 - fall back to raw text
        try:
            text = await resp.text()
        except Exception:  # noqa: BLE001
            return f"mailapi returned HTTP {status}"
        return f"mailapi returned HTTP {status}: {text[:300]}"

    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        err = body["error"]
        return f"mailapi returned HTTP {status}: {err.get('code', 'error')}: {err.get('message', '')}"

    return f"mailapi returned HTTP {status}: {json.dumps(body)[:300]}"


async def send_email(
    env,
    to: str,
    subject: str,
    html: str,
    from_name: str | None = None,
    *,
    post=None,
    text: str | None = None,
) -> dict:
    """Send an HTML email via mailapi.

    Returns mailapi's JSON response (message id, accepted/rejected recipients)
    so callers can log or act on it. Raises RuntimeError when mailapi rejects
    the request, so the existing ``try/except`` in ``worker.py`` still works.

    ``to`` may be a single address or a comma-separated list.
    """
    if post is None:
        post = _post

    base_url = _single_value(getattr(env, "MAILAPI_URL", None)).strip()
    api_key = _single_value(getattr(env, "MAILAPI_KEY", None)).strip()

    if not base_url or not api_key:
        raise RuntimeError(
            "MAILAPI_URL and MAILAPI_KEY must be set. In production: "
            "`wrangler secret put MAILAPI_URL` and `wrangler secret put MAILAPI_KEY`."
        )
    if not to:
        raise RuntimeError("No recipient: set EMAIL_TO in the Worker environment")

    recipients = [addr.strip() for addr in str(to).split(",") if addr.strip()]

    payload: dict = {
        "to": recipients,
        "subject": str(subject).replace("\r", " ").replace("\n", " "),
        "html": str(html),
    }

    # Optional keys are omitted rather than sent empty, so mailapi's own
    # defaults (account fromName, auto-generated plain-text part) apply.
    if text:
        payload["text"] = str(text)
    account = _single_value(getattr(env, "MAILAPI_ACCOUNT", None)).strip()
    if account:
        payload["account"] = account
    name = (from_name or _single_value(getattr(env, "EMAIL_FROM_NAME", None))).strip()
    if name:
        payload["fromName"] = name

    resp = await post(base_url, api_key, payload)

    status = getattr(resp, "status", None)
    if status is None or not 200 <= int(status) < 300:
        raise RuntimeError(await _describe_failure(resp))

    try:
        return await resp.json()
    except Exception:  # noqa: BLE001 - a 2xx with no body still counts as sent
        return {"status": "sent"}
