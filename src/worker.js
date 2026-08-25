/**
 * Cloudflare Workers entry point.
 *
 * Serves the pre-built static site from `dist/` (ASSETS binding) and handles
 * the two form POSTs, mirroring the old FastAPI handlers. This is the
 * Workers equivalent of the Pages Functions in `functions/`; both paths send
 * email through `functions/_lib/smtp.js`.
 */
import { sendEmail, escapeHtml } from "../functions/_lib/smtp.js";

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

async function handleContact(request, env) {
  try {
    const form = await request.formData();
    const name = String(form.get("name") ?? "").trim();
    const email = String(form.get("email") ?? "").trim();
    const message = String(form.get("message") ?? "").trim();

    if (!name || !email || !message || !email.includes("@")) {
      throw new Error("invalid form submission (name/email/message required)");
    }

    const html =
      `<h2>Form Submission</h2>` +
      `<p><b>Name:</b> ${escapeHtml(name)}</p>` +
      `<p><b>Email:</b> ${escapeHtml(email)}</p>` +
      `<p><b>Message:</b> ${escapeHtml(message)}</p>`;

    await sendEmail(env, {
      to: env.EMAIL_TO || env.HOST_EMAIL,
      subject: `Contact Form: ${name}`,
      html,
    });
  } catch (err) {
    console.error("contact-us failed:", err);
  }

  return Response.redirect(new URL("/contact", request.url).toString(), 302);
}

async function handleReserve(request, env) {
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

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (request.method === "POST" && url.pathname === "/contact-us") {
      return handleContact(request, env);
    }
    if (request.method === "POST" && url.pathname === "/reserve-table") {
      return handleReserve(request, env);
    }
    if (url.pathname === "/reservations") {
      return Response.redirect(new URL("/bar", request.url).toString(), 301); // legacy route that never rendered
    }

    return env.ASSETS.fetch(request);
  },
};
