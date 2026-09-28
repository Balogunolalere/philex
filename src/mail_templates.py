"""HTML email templates for the philexentertainment.com forms.

One layout, two audiences:

* ``notification`` - the internal mail philex gets for contact and reservation
  submissions. It is a record of what the visitor typed, so it stays close to a
  form: labelled rows, no decoration.
* ``ticket_delivery`` - the mail the guest gets when they claim a free ticket.
  They are expecting a ticket, so this one reads like an invitation and carries
  the event details.

Client support is the whole game with email HTML: tables instead of divs, inline
styles instead of a stylesheet, a 600px column that collapses on phones, and no
web fonts. Anything interactive is a plain link.

Text is escaped here rather than at the call sites, so a visitor cannot inject
markup into a mail (or into the notification philex opens in their inbox).
"""

from __future__ import annotations

__all__ = [
    "SITE_URL",
    "TICKET_FILENAME",
    "TICKET_URL",
    "escape_html",
    "notification",
    "ticket_delivery",
]

SITE_URL = "https://philexentertainment.com"
CONTACT_EMAIL = "info@philexentertainment.com"

# Header mark. Hosted, because an attached logo is blocked or shown as a
# paperclip by most clients. Cropped to the artwork and shrunk for the web from
# the site's own header logo (static/images/WhatsApp_Image_…-removebg-preview(1).png,
# a 1500px square that is mostly empty canvas) - the clean filename matters in a
# URL, and the quantised copy is a quarter of the bytes. If images are off, the
# alt text carries the name.
LOGO_URL = f"{SITE_URL}/static/images/philex-logo.png"

# Public ticket art, used as the fallback link when the attachment cannot be
# loaded. The attachment itself is the smaller email-sized copy in static/.
TICKET_URL = f"{SITE_URL}/static/images/blaq-xperience-ticket-1400.png"
TICKET_FILENAME = "The-BLAQ-Xperience-Ticket.png"
TICKET_SUBJECT = "Your free ticket — The BLAQ Xperience"

# Brand colours, taken from the site (deep green + the gold used across the
# BLAQ Xperience artwork).
INK = "#102b2a"
INK_SOFT = "#3f4442"
GOLD = "#c49871"
PAGE = "#f4f1ea"
CARD = "#ffffff"

EVENT = {
    "name": "The BLAQ Xperience",
    "when": "Sunday 11th October 2026, 2PM",
    "where": "Jogor Centre, Ibadan",
}

FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"


def escape_html(value: object) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _link(url: str, label: str) -> str:
    return f'<a href="{escape_html(url)}" style="color:{INK};text-decoration:underline;">{escape_html(label)}</a>'


def layout(*, title: str, preheader: str, body: str, footer: str) -> str:
    """Wrap body HTML in the branded shell every mail in this project uses."""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<meta name="color-scheme" content="light dark" />
<title>{escape_html(title)}</title>
</head>
<body style="margin:0;padding:0;background:{PAGE};">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:{PAGE};font-size:1px;">{escape_html(preheader)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{PAGE};">
<tr><td align="center" style="padding:28px 12px;">

  <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:600px;background:{CARD};border-radius:14px;overflow:hidden;font-family:{FONT};">
    <tr>
      <td style="background:{INK};padding:24px 32px;">
        <img src="{LOGO_URL}" width="148" alt="Philex Entertainment" style="display:block;width:148px;max-width:148px;height:auto;border:0;outline:none;text-decoration:none;" />
      </td>
    </tr>
    <tr><td style="height:4px;background:{GOLD};line-height:4px;font-size:0;">&nbsp;</td></tr>
    <tr><td style="padding:32px;">
{body}
    </td></tr>
    <tr>
      <td style="padding:20px 32px 26px;border-top:1px solid #ece7de;background:#faf8f4;font-family:{FONT};font-size:12px;line-height:19px;color:#7b7a74;">
        <p style="margin:0 0 8px;">{escape_html(footer)}</p>
        <p style="margin:0;">Philex Entertainment &middot; {_link("mailto:" + CONTACT_EMAIL, CONTACT_EMAIL)} &middot; {_link(SITE_URL, "philexentertainment.com")}</p>
      </td>
    </tr>
  </table>

</td></tr>
</table>
</body>
</html>
"""


def _heading(text: str) -> str:
    return (
        f'<h1 style="margin:0 0 18px;font-family:{FONT};font-size:22px;line-height:30px;'
        f'font-weight:700;color:{INK};">{escape_html(text)}</h1>'
    )


def _paragraph(text: str) -> str:
    return (
        f'<p style="margin:0 0 16px;font-family:{FONT};font-size:15px;line-height:24px;'
        f'color:{INK_SOFT};">{escape_html(text)}</p>'
    )


def _details(rows: list[tuple[str, str]]) -> str:
    """Labelled rows - the shape of a form, which is what a notification is.

    Values are escaped, then newlines become breaks so a multi-line message
    reads the way the visitor typed it.
    """
    cells = []
    for index, (label, value) in enumerate(rows):
        pad = "0" if index == 0 else "12px"
        rendered = escape_html(value).replace("\n", "<br />")
        cells.append(
            f"""        <tr>
          <td style="padding:{pad} 12px {pad} 0;vertical-align:top;font-family:{FONT};font-size:11px;line-height:20px;letter-spacing:.08em;text-transform:uppercase;color:#7b7a74;white-space:nowrap;">{escape_html(label)}</td>
          <td style="padding:{pad} 0;vertical-align:top;font-family:{FONT};font-size:15px;line-height:20px;color:{INK};word-break:break-word;">{rendered}</td>
        </tr>"""
        )
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        'style="width:100%;border-collapse:collapse;">\n' + "\n".join(cells) + "\n      </table>"
    )


def _callout(title: str, lines: list[str]) -> str:
    rendered = "".join(
        f'<p style="margin:0;font-family:{FONT};font-size:14px;line-height:22px;color:{INK_SOFT};">{escape_html(line)}</p>'
        for line in lines
    )
    return f"""      <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="width:100%;background:#f7f3ec;border-left:3px solid {GOLD};border-radius:0 10px 10px 0;margin:0 0 20px;">
        <tr><td style="padding:18px 20px;">
          <p style="margin:0 0 6px;font-family:{FONT};font-size:15px;line-height:22px;font-weight:700;color:{INK};">{escape_html(title)}</p>
{rendered}
        </td></tr>
      </table>"""


def _button(url: str, label: str) -> str:
    return (
        f'<p style="margin:0 0 20px;"><a href="{escape_html(url)}" '
        f'style="display:inline-block;padding:13px 22px;background:{GOLD};border-radius:8px;'
        f'font-family:{FONT};font-size:15px;font-weight:700;color:{INK};text-decoration:none;">'
        f"{escape_html(label)}</a></p>"
    )


def notification(*, heading: str, preheader: str, rows: list[tuple[str, str]], note: str = "") -> str:
    """Internal mail: a contact or reservation submission, as a record for philex."""
    body_parts = [_heading(heading), _details(rows)]
    if note:
        body_parts.append(
            f'<p style="margin:18px 0 0;font-family:{FONT};font-size:13px;line-height:21px;color:#7b7a74;">{escape_html(note)}</p>'
        )
    return layout(
        title=heading,
        preheader=preheader,
        body="\n      ".join(body_parts),
        footer="Sent automatically by the philexentertainment.com forms.",
    )


def ticket_delivery(*, name: str = "", attached: bool = True) -> str:
    """Guest mail: the ticket itself, plus what they need to know at the door."""
    greeting = f"Hi {name.split()[0]}," if name.strip() else "Hi,"
    if attached:
        delivery = (
            "Your ticket is attached to this email. Keep the email or save the attachment — "
            "show it at the door and you are in."
        )
    else:
        delivery = "Your ticket is waiting for you at the link below. Show it at the door and you are in."

    parts = [
        _heading("Your free ticket is here"),
        _paragraph(greeting),
        _paragraph(
            "A friend of the house bought a handful of tickets to The BLAQ Xperience, "
            "and one of them has your name on it."
        ),
        _paragraph(delivery),
        _callout(EVENT["name"], [EVENT["when"], EVENT["where"]]),
    ]
    if not attached:
        parts.append(_button(TICKET_URL, "Open your ticket"))
    parts.append(
        _paragraph("One ticket per person, while they last. See you at Jogor Centre.")
    )
    parts.append(_paragraph("— Philex Entertainment"))
    return layout(
        title="Your free ticket - The BLAQ Xperience",
        preheader=f"Your free ticket to {EVENT['name']} is ready.",
        body="\n      ".join(parts),
        footer=(
            "You are receiving this because you claimed a free ticket to "
            f"{EVENT['name']} on philexentertainment.com. If this was not you, ignore this email."
        ),
    )
