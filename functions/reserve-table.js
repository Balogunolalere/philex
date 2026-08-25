/**
 * POST /reserve-table — handles the table reservation form on /bar.
 * Mirrors the old FastAPI handler: formats the date, emails, redirects.
 */
import { sendEmail, escapeHtml } from "./_lib/smtp.js";

const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

/** "13/09/2023" -> "September 13, 2023"; returns input unchanged if unparseable. */
function formatDate(raw) {
  const m = /^(\d{1,2})\/(\d{1,2})\/(\d{4})$/.exec(raw.trim());
  if (!m) return raw;
  const month = Number(m[2]);
  if (month < 1 || month > 12) return raw;
  return `${MONTHS[month - 1]} ${Number(m[1])}, ${m[3]}`;
}

export async function onRequestPost({ request, env }) {
  try {
    const form = await request.formData();
    const partysize = String(form.get("partysize") ?? "").trim();
    const date = formatDate(String(form.get("date") ?? "").trim());
    const time = String(form.get("time") ?? "").trim();

    if (!partysize || !date || !time) {
      throw new Error("invalid form submission (partysize/date/time required)");
    }

    const html =
      `<h2>Table Reservation Request</h2>` +
      `<p><b>Party Size:</b> ${escapeHtml(partysize)}</p>` +
      `<p><b>Date:</b> ${escapeHtml(date)}</p>` +
      `<p><b>Time:</b> ${escapeHtml(time)}</p>`;

    await sendEmail(env, {
      to: env.EMAIL_TO || env.HOST_EMAIL,
      subject: "Table Reservation Request",
      html,
    });
  } catch (err) {
    console.error("reserve-table failed:", err);
  }

  return Response.redirect(new URL("/bar", request.url).toString(), 302);
}
