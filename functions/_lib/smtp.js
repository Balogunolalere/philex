/**
 * Minimal SMTP client for Cloudflare Pages Functions, using the Workers
 * connect() TCP socket API (cloudflare:sockets).
 *
 * Defaults target Hostinger's mail server (smtp.hostinger.com). Port 465 uses
 * implicit TLS, port 587 uses STARTTLS. Cloudflare prohibits port 25.
 *
 * Required environment variables (set in the Pages project settings):
 *   HOST_EMAIL      - mailbox that sends and receives (e.g. info@example.com)
 *   HOST_PASSWORD   - the mailbox password
 * Optional:
 *   SMTP_HOST   (default smtp.hostinger.com)
 *   SMTP_PORT   (default 465)
 *   EMAIL_TO    (default: HOST_EMAIL)
 *   EMAIL_FROM_NAME (default "Broadway Lounge")
 */
import { connect } from "cloudflare:sockets";

const encoder = new TextEncoder();
const decoder = new TextDecoder();

function expect(res, code, what) {
  if (res.code !== code) {
    throw new Error(`SMTP ${what}: expected ${code}, got ${res.code} (${res.line})`);
  }
}

export function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));
}

function base64(value) {
  const bytes = encoder.encode(value);
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin);
}

export async function sendEmail(env, { to, subject, html, fromName }) {
  const host = env.SMTP_HOST || "smtp.hostinger.com";
  const port = Number(env.SMTP_PORT || 465);
  const from = env.HOST_EMAIL;
  const password = env.HOST_PASSWORD;
  const name = fromName || env.EMAIL_FROM_NAME || "Broadway Lounge";

  if (!from || !password) {
    throw new Error("HOST_EMAIL / HOST_PASSWORD environment variables are not set");
  }
  if (!to) {
    throw new Error("EMAIL_TO (or HOST_EMAIL) is required");
  }

  const secureTransport = port === 465 ? "on" : port === 587 ? "starttls" : "off";
  if (port === 25) {
    throw new Error("Cloudflare Workers cannot connect to SMTP port 25; use 465 or 587");
  }

  let socket = connect({ hostname: host, port }, { secureTransport });
  let reader = socket.readable.getReader();
  let writer = socket.writable.getWriter();
  let buffer = "";
  let closed = false;

  async function closeSocket() {
    if (closed) return;
    closed = true;
    try {
      await writer.close();
    } catch {
      /* already closed */
    }
    try {
      socket.close();
    } catch {
      /* already closed */
    }
  }

  /** Read one SMTP response (handles multi-line 250- replies). */
  async function readResponse() {
    for (;;) {
      const nl = buffer.indexOf("\n");
      if (nl >= 0) {
        const line = buffer.slice(0, nl).replace(/\r$/, "");
        buffer = buffer.slice(nl + 1);
        const code = Number(line.slice(0, 3));
        // A response line ends the reply when it is "NNN " (not "NNN-"),
        // except the initial 220 greeting which arrives as a single line.
        if (code === 220 || (line.length >= 4 && line[3] === " ")) {
          return { code, line };
        }
        continue; // continuation line of a multi-line reply
      }
      const { value, done } = await reader.read();
      if (done) {
        throw new Error(`SMTP connection closed by server (${host}:${port})`);
      }
      buffer += decoder.decode(value, { stream: true });
    }
  }

  async function send(line) {
    await writer.write(encoder.encode(line + "\r\n"));
  }

  try {
    // Greeting
    expect(await readResponse(), 220, "greeting");

    await send(`EHLO ${host}`);
    expect(await readResponse(), 250, "EHLO");

    if (secureTransport === "starttls") {
      socket = socket.startTls();
      reader = socket.readable.getReader();
      writer = socket.writable.getWriter();
      buffer = "";
      await send(`EHLO ${host}`);
      expect(await readResponse(), 250, "EHLO after STARTTLS");
    }

    // Authenticate: prefer AUTH LOGIN, fall back to AUTH PLAIN.
    await send("AUTH LOGIN");
    let auth = await readResponse();
    if (auth.code === 334) {
      await send(base64(from));
      auth = await readResponse();
      if (auth.code === 334) {
        await send(base64(password));
        auth = await readResponse();
      }
      expect(auth, 235, "AUTH LOGIN");
    } else {
      await send(`AUTH PLAIN ${base64(`\0${from}\0${password}`)}`);
      auth = await readResponse();
      expect(auth, 235, "AUTH PLAIN");
    }

    await send(`MAIL FROM:<${from}>`);
    expect(await readResponse(), 250, "MAIL FROM");
    await send(`RCPT TO:<${to}>`);
    expect(await readResponse(), 250, "RCPT TO");
    await send("DATA");
    expect(await readResponse(), 354, "DATA");

    const body = [
      `From: ${name} <${from}>`,
      `To: ${to}`,
      `Subject: ${String(subject).replace(/[\r\n]/g, " ")}`,
      "MIME-Version: 1.0",
      "Content-Type: text/html; charset=UTF-8",
      "Content-Transfer-Encoding: 8bit",
      "",
      String(html),
    ].join("\r\n");

    // Dot-stuffing: any line beginning with "." gets an extra ".".
    await send(body.replace(/\r\n\./g, "\r\n.."));
    await send(".");
    expect(await readResponse(), 250, "message body");

    await send("QUIT");
    await readResponse();
  } finally {
    await closeSocket();
  }
}
