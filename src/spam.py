"""Key-free screening for every public form on the site.

Two layers, both judged on the server:

1. **Honeypot** - a field parked off-canvas that people never see and bots fill in.
2. **Fill time** - the page reports how long the visitor took (hidden ``elapsed``
   field, set in ``base.html``); automated posts arrive instantly.

A caught submission is dropped quietly: the visitor still gets the normal
thank-you, so a spammer learns nothing. There is deliberately no per-IP cap:
counting submissions needs shared state, and losing a real enquiry is worse
than accepting one spam entry.
"""

from __future__ import annotations

__all__ = ["looks_automated", "screen", "MIN_FILL_MS"]

MIN_FILL_MS = 3000


def _int_or_none(raw) -> int | None:
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


def looks_automated(*, website: str = "", elapsed: str = "") -> str | None:
    """Honeypot + fill-time screen. Returns a reason string, or None when it passes."""
    if (website or "").strip():
        return "honeypot field was filled in"

    ms = _int_or_none(elapsed)
    # An empty value means the page's script never ran (JS off); nothing to judge.
    if ms is not None and ms < MIN_FILL_MS:
        return f"submitted after {ms}ms, under the {MIN_FILL_MS}ms floor"
    return None


async def screen(*, website: str = "", elapsed: str = "") -> str | None:
    """Full screen for a submission. Returns a reason string, or None when it passes."""
    return looks_automated(website=website, elapsed=elapsed)
