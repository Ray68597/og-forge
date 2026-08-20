/**
 * OG Forge — client SDK.
 *
 * Zero dependencies. Works in Node 18+, browsers, Deno, Bun, edge runtimes.
 *
 * Usage:
 *   import { createClient } from "og-forge";
 *   const og = createClient();                     // public instance
 *   const url = og.imageUrl({ title: "Hello" });   // -> string URL
 *   const buf = await og.fetchImage({ title: "Hello" }); // -> ArrayBuffer
 */

const DEFAULT_BASE_URL = "https://og-forge.xyz";

const TEMPLATES = /** @type {const} */ ([
  "gradient",
  "split",
  "spotlight",
  "banner",
  "minimal",
]);

/**
 * @typedef {Object} CardOptions
 * @property {string} title                      Main heading (required, ≤200 chars).
 * @property {string} [subtitle]                 Secondary line (≤300 chars).
 * @property {string} [brand]                    Small brand/label text (≤60 chars).
 * @property {"gradient"|"split"|"spotlight"|"banner"|"minimal"} [template="gradient"]
 * @property {string} [bgColor]                  Hex color, e.g. "#1e1b4b".
 * @property {string} [bgColor2]                 Second gradient color.
 * @property {string} [accentColor]              Accent elements.
 * @property {string} [textColor]                Body text.
 * @property {number} [width=1200]               200–2400.
 * @property {number} [height=630]               200–2400.
 * @property {"auto"|"light"|"dark"} [theme="auto"]
 */

/** Convert camelCase keys to the API's snake_case params. */
const CAMEL_TO_SNAKE = {
  bgColor: "bg_color",
  bgColor2: "bg_color2",
  accentColor: "accent_color",
  textColor: "text_color",
};

/**
 * Build the /v1/generate query string for a card.
 * @param {CardOptions} opts
 * @returns {string}
 */
function buildParams(opts) {
  if (!opts || typeof opts.title !== "string" || !opts.title.trim()) {
    throw new Error("og-forge: `title` is required.");
  }
  const p = new URLSearchParams();
  p.set("title", opts.title);
  if (opts.subtitle) p.set("subtitle", opts.subtitle);
  if (opts.brand) p.set("brand", opts.brand);
  if (opts.template) p.set("template", opts.template);
  if (opts.width != null) p.set("width", String(opts.width));
  if (opts.height != null) p.set("height", String(opts.height));
  if (opts.theme) p.set("theme", opts.theme);
  for (const [camel, snake] of Object.entries(CAMEL_TO_SNAKE)) {
    if (opts[camel]) p.set(snake, opts[camel]);
  }
  return p.toString();
}

/**
 * @param {string} baseUrl
 * @param {CardOptions} opts
 * @returns {string} absolute image URL
 */
function imageUrl(baseUrl, opts) {
  return `${baseUrl.replace(/\/+$/, "")}/v1/generate?${buildParams(opts)}`;
}

/**
 * Fetch a rendered PNG. Uses global fetch (Node 18+/browsers).
 * @param {string} baseUrl
 * @param {CardOptions} opts
 * @param {{apiKey?: string}} [extra]
 * @returns {Promise<ArrayBuffer>}
 */
async function fetchImage(baseUrl, opts, extra = {}) {
  const headers = {};
  if (extra.apiKey) headers["x-api-key"] = extra.apiKey;
  const res = await fetch(imageUrl(baseUrl, opts), { headers });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`og-forge: HTTP ${res.status} ${res.statusText} ${detail}`.trim());
  }
  return res.arrayBuffer();
}

/**
 * Create a bound client.
 * @param {{baseUrl?: string, apiKey?: string}} [config]
 */
function createClient(config = {}) {
  const baseUrl = config.baseUrl || DEFAULT_BASE_URL;
  return {
    baseUrl,
    /** @param {CardOptions} opts @returns {string} */
    imageUrl: (opts) => imageUrl(baseUrl, opts),
    /** @param {CardOptions} opts @returns {Promise<ArrayBuffer>} */
    fetchImage: (opts) => fetchImage(baseUrl, opts, { apiKey: config.apiKey }),
    /** Convenience: full <meta property="og:image"> tag. */
    metaTag: (opts) =>
      `<meta property="og:image" content="${imageUrl(baseUrl, opts)}">`,
    /** Convenience: <img> tag (escapes quotes in params). */
    imgTag: (opts, alt = "") =>
      `<img src="${imageUrl(baseUrl, opts)}" alt="${alt.replace(/"/g, "&quot;")}">`,
  };
}

module.exports = { DEFAULT_BASE_URL, TEMPLATES, buildParams, imageUrl, fetchImage, createClient };
