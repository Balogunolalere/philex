/**
 * POST /contact-us — handles the contact form on /contact.
 * Mirrors the old FastAPI handler: validates, emails the submission, redirects.
 */
import { sendEmail, escapeHtml } from "./_lib/smtp.js";

export async function onRequestPost({ request, env }) {
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
