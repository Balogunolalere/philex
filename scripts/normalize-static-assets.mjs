#!/usr/bin/env node
/**
 * One-off cleanup for the mirrored WordPress asset tree under static/.
 *
 * The mirror (originally scraped from https://fidalgo.qodeinteractive.com)
 * saved files with the URL query string baked into the filename, e.g.
 *   main.min.css?ver=6.7.1.css      -> main.min.css
 *   jquery.min.js?ver=3.7.1         -> jquery.min.js
 *   eicons.woff2?5.30.0             -> eicons.woff2
 *
 * HTTP treats "?" as the start of the query string, so such files can never
 * be served. This script renames them to clean names and downloads any
 * referenced files that are genuinely missing, so the templates can be
 * rewritten to load everything from /static/.
 *
 * Usage: node scripts/normalize-static-assets.mjs
 */
import { existsSync, mkdirSync, readFileSync, readdirSync, renameSync, rmSync, statSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const TEMPLATES = path.join(ROOT, "templates");
const STATIC = path.join(ROOT, "static");
const ORIGIN = "https://fidalgo.qodeinteractive.com";

/* ---------------------------------------------------------------- helpers */

function walk(dir, rel = "", out = []) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const relPath = rel ? `${rel}/${entry.name}` : entry.name;
    const abs = path.join(dir, entry.name);
    if (entry.isDirectory()) walk(abs, relPath, out);
    else out.push(relPath);
  }
  return out;
}

function cleanName(name) {
  const q = name.indexOf("?");
  return q === -1 ? null : name.slice(0, q);
}

/* --------------------------------------------------- rename ?ver files */

const all = walk(STATIC);
const weird = all.filter((p) => p.includes("?") && !p.startsWith("wp-json/"));

let renamed = 0, deleted = 0, kept = 0;
for (const rel of weird) {
  const target = cleanName(rel);
  const targetAbs = path.join(STATIC, target);
  if (existsSync(targetAbs)) {
    // Clean twin already exists (e.g. ElegantIcons.eot): the weird copy is dup.
    rmSync(path.join(STATIC, rel));
    deleted++;
    continue;
  }
  mkdirSync(path.dirname(targetAbs), { recursive: true });
  renameSync(path.join(STATIC, rel), targetAbs);
  renamed++;
  console.log(`renamed ${rel} -> ${target}`);
}
console.log(`\nrenamed ${renamed}, deleted ${deleted} duplicates\n`);

/* --------------------------------------------------- missing downloads */

// Collect every fidalgo URL referenced from the templates.
const refs = new Set();
for (const f of readdirSync(TEMPLATES).filter((f) => f.endsWith(".html"))) {
  const src = readFileSync(path.join(TEMPLATES, f), "utf8");
  const re = /(?:href|src|srcset)\s*=\s*["']([^"']+)["']|url\(\s*["']?([^)"']+)["']?\s*\)|(?:href|src)=([^\s"'<>]+)/g;
  let m;
  while ((m = re.exec(src))) {
    const u = (m[1] || m[2] || m[3] || "").trim();
    for (const part of u.split(",")) {
      const cand = part.trim().split(/\s+/)[0];
      if (cand.startsWith(ORIGIN)) refs.add(cand);
    }
  }
}

const toLocal = (url) => `static/${url.split("?")[0].replace(`${ORIGIN}/`, "")}`;

const missing = [...refs]
  .map((u) => ({ url: u, local: toLocal(u) }))
  .filter(({ local }) => !existsSync(path.join(ROOT, local)));

let downloaded = 0, failed = [];
for (const { url, local } of missing) {
  const abs = path.join(ROOT, local);
  mkdirSync(path.dirname(abs), { recursive: true });
  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const buf = Buffer.from(await res.arrayBuffer());
    writeFileSync(abs, buf);
    downloaded++;
    console.log(`downloaded ${local} (${buf.length} bytes)`);
  } catch (err) {
    failed.push(`${local} <- ${url} (${err.message})`);
  }
}
console.log(`\ndownloaded ${downloaded}, failed ${failed.length}`);
if (failed.length) {
  console.log("STILL MISSING:");
  for (const f of failed) console.log(`  ${f}`);
  process.exitCode = 1;
}
