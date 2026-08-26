"""Minimal SMTP client for Cloudflare Python Workers.

Cloudflare Workers cannot open raw sockets with Python's ``socket`` module, so
this uses the Workers runtime's TCP socket API (``cloudflare:sockets``) via the
Pyodide FFI (see https://developers.cloudflare.com/workers/languages/python/ffi/).

Defaults target Hostinger's mail server (smtp.hostinger.com). Port 465 uses
implicit TLS, port 587 uses STARTTLS. Port 25 is prohibited by Cloudflare.
"""

from __future__ import annotations

import base64
import re

__all__ = ["send_email"]


def _b64(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def _connect_socket(host: str, port: int, secure_transport: str):
    """Open a TCP socket using the cloudflare:sockets runtime API."""
    from workers.utils import import_from_javascript

    sockets = import_from_javascript("cloudflare:sockets")
    return sockets.connect(
        {"hostname": host, "port": port},
        {"secureTransport": secure_transport},
    )


def _as_bytes(value) -> bytes:
    """Normalize a JS Uint8Array (exposed as a Pyodide buffer view) to bytes."""
    return value.tobytes() if hasattr(value, "tobytes") else bytes(value)


class _SmtpSession:
    """Tiny SMTP dialogue helper over one socket connection."""

    def __init__(self, socket):
        self.socket = socket
        self.reader = socket.readable.getReader()
        self.writer = socket.writable.getWriter()
        self._buffer = bytearray()

    async def _fill(self) -> None:
        result = await self.reader.read()
        if result.done:
            raise ConnectionError("SMTP server closed the connection")
        self._buffer.extend(_as_bytes(result.value))

    async def read_response(self) -> tuple[int, str]:
        """Read one SMTP reply; multi-line replies (NNN-...) are consumed."""
        while True:
            nl = self._buffer.find(b"\n")
            if nl >= 0:
                raw = bytes(self._buffer[:nl])
                del self._buffer[: nl + 1]
                line = raw.decode("utf-8", "replace").rstrip("\r")
                code = int(line[:3]) if line[:3].isdigit() else 0
                if code == 220 or (len(line) >= 4 and line[3] == " "):
                    return code, line
                continue
            await self._fill()

    async def send(self, line: str) -> None:
        # The sockets API writer requires a JS Uint8Array; a raw Python bytes
        # object is rejected ("non-ArrayBuffer/ArrayBufferView type").
        from js import Uint8Array

        data = (line + "\r\n").encode("utf-8")
        await self.writer.write(Uint8Array.new(data))

    async def close(self) -> None:
        try:
            await self.writer.close()
        except Exception:
            pass
        try:
            self.socket.close()
        except Exception:
            pass


def _expect(code: int, line: str, expected: int, what: str) -> None:
    if code != expected:
        raise RuntimeError(f"SMTP {what}: expected {expected}, got {code} ({line})")


def _single_value(value) -> str:
    return value if value is not None else ""


async def send_email(
    env,
    to: str,
    subject: str,
    html: str,
    from_name: str | None = None,
    *,
    connect=None,
    host: str | None = None,
    port: int | None = None,
) -> None:
    """Send a plain-HTML email through the configured SMTP server.

    Environment variables used (from the Worker ``env`` binding):
      HOST_EMAIL, HOST_PASSWORD (required)
      SMTP_HOST (default smtp.hostinger.com), SMTP_PORT (default 465)
      EMAIL_TO (defaults to HOST_EMAIL), EMAIL_FROM_NAME
    """
    if connect is None:
        connect = _connect_socket
    host = host or _single_value(getattr(env, "SMTP_HOST", None)) or "smtp.hostinger.com"
    port = int(port or _single_value(getattr(env, "SMTP_PORT", None)) or 465)
    sender = _single_value(getattr(env, "HOST_EMAIL", None))
    password = _single_value(getattr(env, "HOST_PASSWORD", None))
    name = from_name or _single_value(getattr(env, "EMAIL_FROM_NAME", None)) or "Broadway Lounge"

    if not sender or not password:
        raise RuntimeError("HOST_EMAIL / HOST_PASSWORD environment variables are not set")
    if port == 25:
        raise RuntimeError("Cloudflare Workers cannot connect to SMTP port 25; use 465 or 587")

    secure_transport = "on" if port == 465 else ("starttls" if port == 587 else "off")

    socket = connect(host, port, secure_transport)
    session = _SmtpSession(socket)
    try:
        code, line = await session.read_response()
        _expect(code, line, 220, "greeting")

        await session.send(f"EHLO {host}")
        code, line = await session.read_response()
        _expect(code, line, 250, "EHLO")

        if secure_transport == "starttls":
            socket = socket.startTls()
            session = _SmtpSession(socket)
            await session.send(f"EHLO {host}")
            code, line = await session.read_response()
            _expect(code, line, 250, "EHLO after STARTTLS")

        # Authenticate: prefer AUTH LOGIN, fall back to AUTH PLAIN.
        await session.send("AUTH LOGIN")
        code, line = await session.read_response()
        if code == 334:
            await session.send(_b64(sender))
            code, line = await session.read_response()
            if code == 334:
                await session.send(_b64(password))
                code, line = await session.read_response()
            _expect(code, line, 235, "AUTH LOGIN")
        else:
            auth_plain = f"\x00{sender}\x00{password}"
            await session.send(f"AUTH PLAIN {_b64(auth_plain)}")
            code, line = await session.read_response()
            _expect(code, line, 235, "AUTH PLAIN")

        await session.send(f"MAIL FROM:<{sender}>")
        code, line = await session.read_response()
        _expect(code, line, 250, "MAIL FROM")
        await session.send(f"RCPT TO:<{to}>")
        code, line = await session.read_response()
        _expect(code, line, 250, "RCPT TO")
        await session.send("DATA")
        code, line = await session.read_response()
        _expect(code, line, 354, "DATA")

        safe_subject = str(subject).replace("\r", " ").replace("\n", " ")
        body = "\r\n".join(
            [
                f"From: {name} <{sender}>",
                f"To: {to}",
                f"Subject: {safe_subject}",
                "MIME-Version: 1.0",
                "Content-Type: text/html; charset=UTF-8",
                "Content-Transfer-Encoding: 8bit",
                "",
                str(html),
            ]
        )
        # Dot-stuffing: lines starting with "." get an extra dot.
        await session.send(re.sub(r"\r\n\.", "\r\n..", body))
        await session.send(".")
        code, line = await session.read_response()
        _expect(code, line, 250, "message body")

        await session.send("QUIT")
        await session.read_response()
    finally:
        await session.close()
