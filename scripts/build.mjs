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
  // srcset uses ", static/..." with a space; keep the &quot; form working too.
  return html
    .replaceAll("&quot;static/", "&quot;/static/")
    .replace(/(?<=["'\s,(])static\//g, "/static/");
}

/** `(?:(?!</tag>)[\s\S])*?` — lazy match that cannot cross a closing tag. */
const withinTag = (close) => `(?:(?!${close})[\\s\\S])*?`;

/**
 * The templates are a mirror of the qode "Fidalgo" WordPress demo. Everything
 * below strips the demo-only third-party snippets (GTM, Zendesk chat, qode
 * toolbar) and points all demo-host assets at the local /static/ mirror, so
 * pages don't depend on https://fidalgo.qodeinteractive.com (slow, and some
 * files there have since been removed, which broke the layout).
 */

/**
 * Production domain used in canonical / og:url / og:image meta tags.
 */
const SITE_URL = "https://philexentertainment.com";

/** Meta/link tags that must keep absolute URLs (never the /static/ rewrite). */
const SITE_IDENTITY_TAG_RE =
  /<link\b[^>]*rel=["']canonical["'][^>]*>|<meta\b[^>]*(?:property|itemprop)=["'](?:og:url|og:image|og:image:secure_url|twitter:url|twitter:image|image|url)["'][^>]*>/gi;

function stripDemoCruft(html) {
  // WP head discovery links: rss/comment feeds, oembed, api.w.org, RSD.
  html = html.replace(/<link\b[^>]*rel=["'](?:alternate|https:\/\/api\.w\.org\/|EditURI|shortlink)["'][^>]*\/?>\s*/gi, "");
  // dns-prefetch hints for hosts we no longer use.
  html = html.replace(
    /<link\b[^>]*rel=["']dns-prefetch["'][^>]*href=["'][^"']*(?:fidalgo\.qodeinteractive\.com|export\.qodethemes\.com|static\.zdassets\.com)[^"']*["'][^>]*\/?>\s*/gi,
    ""
  );
  // Qode demo toolbar (export.qodethemes.com) stylesheet + script.
  html = html.replace(/<link\b[^>]*href=["'][^"']*export\.qodethemes\.com[^"']*["'][^>]*\/?>\s*/gi, "");
  html = html.replace(/<script\b[^>]*src=["'][^"']*export\.qodethemes\.com[^"']*["'][^>]*>\s*<\/script>\s*/gi, "");
  // Zendesk chat widget (the demo's own key, doesn't work for this site).
  html = html.replace(/<script\b[^>]*src=["'][^"']*static\.zdassets\.com[^"']*["'][^>]*>\s*<\/script>\s*/gi, "");
  // Google Tag Manager (the demo's GTM-KLJLSX7 container): head snippet,
  // body <noscript> iframe, footer loader, and leftover comment markers.
  // Each pattern is anchored so it can only match the script it targets
  // (an unanchored `[\s\S]*?` would start at the first <script> in the
  // document and swallow the whole head + body).
  html = html.replace(/<script\b[^>]*>\s*var gtm4wp_datalayer_name[\s\S]*?<\/script>\s*/gi, "");
  html = html.replace(
    new RegExp(`<noscript>${withinTag("</noscript>")}googletagmanager\\.com\\/ns\\.html${withinTag("</noscript>")}</noscript>\\s*`, "gi"),
    ""
  );
  html = html.replace(
    new RegExp(`<script\\b[^>]*>${withinTag("</script>")}googletagmanager[\\s\\S]*?${withinTag("</script>")}</script>\\s*`, "gi"),
    ""
  );
  html = html.replace(
    new RegExp(`<script\\b[^>]*>${withinTag("</script>")}dataLayer_content${withinTag("</script>")}</script>\\s*`, "gi"),
    ""
  );
  html = html.replace(/<!--\s*[^>]*Google Tag Manager[^>]*-->\s*/gi, "");
  return html;
}

function localizeDemoUrls(html, pagePath) {
  const pathFor = (p) => (p ? `/${p}/` : "/");

  // Pull site-identity meta tags aside so the generic rewrite below cannot
  // turn their absolute demo URLs into relative /static/ paths.
  const kept = [];
  html = html.replace(SITE_IDENTITY_TAG_RE, (tag) => {
    kept.push(tag);
    return `\u0000META${kept.length - 1}\u0000`;
  });

  // Demo-host assets (HTML attributes, inline style url(), JSON-escaped
  // forms in the Elementor config) -> local /static/ mirror.
  html = html.replaceAll("https://fidalgo.qodeinteractive.com/", "/static/");
  html = html.replaceAll("https:\\/\\/fidalgo.qodeinteractive.com\\/", "/static/");
  html = html.replaceAll("//fidalgo.qodeinteractive.com/", "/static/");

  // Drop the WordPress ?ver= cache-buster from local asset URLs.
  html = html.replace(/(\/static\/[^"'\s)]*)\?ver=[^"'\s)]*/g, "$1");

  // Restore the site-identity tags.
  html = html.replace(/\u0000META(\d+)\u0000/g, (_, i) => kept[Number(i)]);

  if (SITE_URL) {
    html = html.replace(
      /(<link\b[^>]*rel=["']canonical["'][^>]*href=["'])[^"']*(["'])/gi,
      `$1${SITE_URL}${pathFor(pagePath)}$2`
    );
    html = html.replace(
      /(<meta\b[^>]*property=["'](?:og:url|twitter:url)["'][^>]*content=["'])[^"']*(["'])/gi,
      `$1${SITE_URL}${pathFor(pagePath)}$2`
    );
    html = html.replace(
      /(<meta\b[^>]*itemprop=["']url["'][^>]*content=["'])[^"']*(["'])/gi,
      `$1${SITE_URL}${pathFor(pagePath)}$2`
    );
    html = html.replace(
      /(<meta\b[^>]*property=["'](?:og:image|og:image:secure_url|twitter:image)["'][^>]*content=["'])https?:\/\/fidalgo\.qodeinteractive\.com\//gi,
      `$1${SITE_URL}/static/`
    );
    html = html.replace(
      /(<meta\b[^>]*itemprop=["']image["'][^>]*content=["'])https?:\/\/fidalgo\.qodeinteractive\.com\//gi,
      `$1${SITE_URL}/static/`
    );
  }

  return html;
}

function finalizePage(html, pagePath) {
  html = stripDemoCruft(html);
  html = absolutizeAssetPaths(html);
  html = localizeDemoUrls(html, pagePath);
  if (/export\.qodethemes\.com/.test(html)) {
    throw new Error(`qode demo toolbar remains (page: ${pagePath || "index"})`);
  }
  const stripped = html.replace(SITE_IDENTITY_TAG_RE, "");
  if (/fidalgo\.qodeinteractive\.com/.test(stripped)) {
    throw new Error(`unlocalized demo URL remains (page: ${pagePath || "index"})`);
  }
  return html;
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
  const indexHtml = finalizePage(await readFile(path.join(TEMPLATES, "index.html"), "utf8"), "");
  await writeFile(path.join(DIST, "index.html"), indexHtml);

  for (const page of PAGES) {
    const pageSrc = await readFile(path.join(TEMPLATES, `${page}.html`), "utf8");
    let html = renderPage(pageSrc, baseSrc);
    html = finalizePage(html, page);
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
