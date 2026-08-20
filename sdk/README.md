# og-forge

Generate Open Graph social card images with a single URL. Client SDK for the [OG Forge](https://og-forge.xyz) API — zero dependencies, works in Node 18+, browsers, Deno, Bun, and edge runtimes.

## Install

```bash
npm install og-forge
```

## Quick start

```js
import { createClient } from "og-forge";

const og = createClient(); // public instance (10 images/min per IP)

// 1. Just a URL — perfect for <meta> tags and Markdown.
const url = og.imageUrl({
  title: "Ship your side project",
  subtitle: "A developer's guide to launching in weekends",
  template: "gradient",
});

// 2. Get the PNG bytes.
const png = await og.fetchImage({ title: "Hello world" });

// 3. Ready-made HTML tags.
og.metaTag({ title: "My Page" });
// <meta property="og:image" content="https://.../v1/generate?title=My%20Page">
```

## Options

| Option | Type | Default | Notes |
|---|---|---|---|
| `title` | `string` | required | ≤200 chars |
| `subtitle` | `string` | — | ≤300 chars |
| `brand` | `string` | — | small label, ≤60 chars |
| `template` | `string` | `"gradient"` | `gradient` `split` `spotlight` `banner` `minimal` |
| `bgColor` / `bgColor2` | `string` | — | hex, e.g. `"#1e1b4b"` |
| `accentColor` / `textColor` | `string` | — | hex |
| `width` / `height` | `number` | `1200` / `630` | 200–2400 |
| `theme` | `string` | `"auto"` | `auto` `light` `dark` |

## Self-hosting

The [og-forge server](https://github.com/Ray68597/og-forge) is MIT-licensed and ships as a ~60MB Docker image. Point the SDK at your own deployment:

```js
const og = createClient({ baseUrl: "https://og.yourdomain.com", apiKey: "..." });
```

## License

MIT
