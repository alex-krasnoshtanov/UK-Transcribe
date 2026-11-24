/**
 * The page's Content Security Policy (pattern from the Florensis dashboard).
 * The browser only ever talks to this server: no outside scripts, fonts or
 * images (the cluster is campus-only), and the API is reached through
 * /api/backend on this origin.
 *
 * Scripts need this request's nonce: Next.js reads it from the CSP header that
 * proxy.ts puts on the request and stamps it on its own inline scripts.
 * Styles keep 'unsafe-inline' for progress-bar widths set as style attributes.
 * No upgrade-insecure-requests: the cluster serves plain HTTP.
 */
export function contentSecurityPolicy(nonce: string, dev = false): string {
  return [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${dev ? " 'unsafe-eval'" : ""}`,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data:",
    "media-src 'self'",
    "font-src 'self'",
    "connect-src 'self'",
    "object-src 'none'",
    "frame-ancestors 'none'",
    "base-uri 'none'",
    "form-action 'self'",
  ].join("; ");
}

/** 128 random bits, base64: a fresh one for every page. */
export function newNonce(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return btoa(String.fromCharCode(...bytes));
}
