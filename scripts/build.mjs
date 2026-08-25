#!/usr/bin/env node
/**
 * Cloudflare Pages build script.
 *
 * The site is a set of Jinja2 templates that were previously rendered by a
 * FastAPI server. They use only `{% extends %}` / `{% block %}` (no variables),
 * so this script assembles them into plain static HTML, which Pages serves
 * from its global CDN instead of running a Python server.
 *
 * Output layout (in ./dist):
 *   index.html            -> /
 *   about/index.html      -> /about
 *   bar/index.html        -> /bar
 *   contact/index.html    -> /contact
 *   gallery/index.html    -> /gallery
 *   philex-index/index.html -> /philex-index
 *   static/**             -> /static/**
 *   _headers, _redirects  -> Pages config
 */
import { cp, mkdir, readdir, readFile, rm, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const TEMPLATES = path.join(ROOT, "templates");
const STATIC = path.join(ROOT, "static");
const DIST = path.join(ROOT, "dist");

const PAGES = ["about", "bar", "contact", "gallery", "philex-index"];
const MAX_ASSET_BYTES = 24 * 1024 * 1024; // keep under Pages' 25 MiB per-file limit
const SKIP_DIRS = new Set(["cdn-cgi"]); // Cloudflare serves /cdn-cgi/* itself

const blockRe = (name) =>
  new RegExp(`\\{%\\s*block\\s+${name}\\s*%\\}[\\s\\S]*?\\{%\\s*endblock\\s+${name}\\s*%\\}`);

function extractBlocks(src) {
  const blocks = new Map();
  const re = /\{%\s*block\s+([\w-]+)\s*%\}([\s\S]*?)\{%\s*endblock\s+\1\s*%\}/g;
  let m;
  while ((m = re.exec(src)) !== null) {
    if (!blocks.has(m[1])) blocks.set(m[1], m[2]);
  }
  return blocks;
}

function renderPage(pageSrc, baseSrc) {
  // Standalone pages (index.html, philex-index.html) are used as-is.
  if (!/\{%\s*extends\s+['"]base\.html['"]\s*%\}/.test(pageSrc)) {
    return pageSrc;
  }
  let out = baseSrc;
  for (const [name, content] of extractBlocks(pageSrc)) {
    const re = blockRe(name);
    if (!re.test(out)) {
      throw new Error(`block "${name}" not found in base.html`);
    }
    out = out.replace(re, content);
  }
  return out;
}

/** Make asset paths absolute so directory-style URLs (/bar/) still resolve them. */
function absolutizeAssetPaths(html) {
  return html
    .replaceAll('"static/', '"/static/')
    .replaceAll("'static/", "'/static/")
    .replaceAll("&quot;static/", "&quot;/static/");
}

async function copyStatic() {
  const src = STATIC;
  const dst = path.join(DIST, "static");
  await rm(dst, { recursive: true, force: true });
  await mkdir(dst, { recursive: true });

  let copied = 0;
  let skipped = 0;

  async function walk(dir, rel) {
    const entries = await readdir(dir, { withFileTypes: true });
    for (const entry of entries) {
      const relPath = rel ? `${rel}/${entry.name}` : entry.name;
      const abs = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        if (SKIP_DIRS.has(entry.name)) {
          console.warn(`skip (reserved/served by Cloudflare): ${relPath}/`);
          continue;
        }
        await mkdir(path.join(dst, relPath), { recursive: true });
        await walk(abs, relPath);
      } else {
        const size = (await stat(abs)).size;
        if (size > MAX_ASSET_BYTES) {
          console.warn(`SKIPPED (${(size / 1024 / 1024).toFixed(1)} MiB > 24 MiB): ${relPath}`);
          skipped++;
          continue;
        }
        await cp(abs, path.join(dst, relPath));
        copied++;
      }
    }
  }

  await walk(src, "");
  return { copied, skipped };
}

async function main() {
  await rm(DIST, { recursive: true, force: true });
  await mkdir(DIST, { recursive: true });

  const baseSrc = await readFile(path.join(TEMPLATES, "base.html"), "utf8");

  // Standalone homepage
  const indexHtml = absolutizeAssetPaths(await readFile(path.join(TEMPLATES, "index.html"), "utf8"));
  await writeFile(path.join(DIST, "index.html"), indexHtml);

  for (const page of PAGES) {
    const pageSrc = await readFile(path.join(TEMPLATES, `${page}.html`), "utf8");
    let html = renderPage(pageSrc, baseSrc);
    html = absolutizeAssetPaths(html);
    if (/{%|{{/.test(html)) {
      throw new Error(`unrendered Jinja remains in ${page}.html`);
    }
    await mkdir(path.join(DIST, page), { recursive: true });
    await writeFile(path.join(DIST, page, "index.html"), html);
    console.log(`rendered ${page} -> /${page}`);
  }

  const { copied, skipped } = await copyStatic();
  console.log(`static: ${copied} files copied${skipped ? `, ${skipped} skipped (too large)` : ""}`);

  console.log("done -> ./dist");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
