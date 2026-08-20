export declare const DEFAULT_BASE_URL: string;
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
