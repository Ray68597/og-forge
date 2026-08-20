// Minimal zero-dependency build: bundle ESM -> CJS twin + emit .d.ts.
// Avoids shipping dev dependencies (typescript, rollup) to consumers.
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { execFileSync } from "node:child_process";

mkdirSync("dist", { recursive: true });
const src = readFileSync("src/index.js", "utf8");

// 1. CJS twin: light transform of our own ESM syntax (we control the source shape).
//    JSDoc comments are harmless in CJS, so we keep them (no fragile stripping).
const cjs = src
  .replace(/^export const /gm, "const ")
  .replace(/^export function /gm, "function ")
  .replace(/^export async function /gm, "async function ")
  .concat(
    "\nmodule.exports = { DEFAULT_BASE_URL, TEMPLATES, buildParams, imageUrl, fetchImage, createClient };\n"
  );
writeFileSync("dist/index.cjs", cjs);

// 2. Copy ESM as-is.
writeFileSync("dist/index.js", src);

// 3. Types: use tsc if available, otherwise emit a hand-written minimal .d.ts.
try {
  execFileSync("npx", ["--yes", "typescript@5", "src/index.js", "--declaration", "--allowJs", "--emitDeclarationOnly", "--outDir", "dist"], { stdio: "inherit" });
} catch {
  writeFileSync(
    "dist/index.d.ts",
    `export declare const DEFAULT_BASE_URL: string;
export declare const TEMPLATES: readonly ["gradient", "split", "spotlight", "banner", "minimal"];
export interface CardOptions {
  title: string; subtitle?: string; brand?: string;
  template?: "gradient" | "split" | "spotlight" | "banner" | "minimal";
  bgColor?: string; bgColor2?: string; accentColor?: string; textColor?: string;
  width?: number; height?: number; theme?: "auto" | "light" | "dark";
}
export declare function buildParams(opts: CardOptions): string;
export declare function imageUrl(baseUrl: string, opts: CardOptions): string;
export declare function fetchImage(baseUrl: string, opts: CardOptions, extra?: { apiKey?: string }): Promise<ArrayBuffer>;
export declare function createClient(config?: { baseUrl?: string; apiKey?: string }): {
  baseUrl: string;
  imageUrl(opts: CardOptions): string;
  fetchImage(opts: CardOptions): Promise<ArrayBuffer>;
  metaTag(opts: CardOptions): string;
  imgTag(opts: CardOptions, alt?: string): string;
};
`
  );
}
console.log("sdk build done");
