/**
 * Which API paths the browser may reach through /api/backend/<path>, and with
 * which methods. Everything else is refused before it leaves this server, so
 * the proxy cannot be used to reach other services in the shared namespace.
 */

const ID = "[0-9a-f]{32}";

const ROUTES: Array<[RegExp, ReadonlyArray<string>]> = [
  [/^jobs$/, ["GET", "POST"]],
  [new RegExp(`^jobs/${ID}$`), ["GET", "DELETE"]],
  [new RegExp(`^jobs/${ID}/segments$`), ["GET"]],
  [new RegExp(`^jobs/${ID}/transcript\\.(txt|srt|vtt)$`), ["GET"]],
  [new RegExp(`^jobs/${ID}/audio$`), ["GET", "DELETE"]],
  [new RegExp(`^jobs/${ID}/(cancel|retry)$`), ["POST"]],
  [/^queue$/, ["GET"]],
  [/^settings$/, ["GET", "PUT"]],
  [/^healthz$/, ["GET"]],
];

export function allowed(path: string, method: string): boolean {
  return ROUTES.some(([re, methods]) => re.test(path) && methods.includes(method));
}

/** Request headers passed on to the API. */
export const FORWARD_REQUEST = ["content-type", "range", "accept"] as const;

/** Response headers passed back to the browser. */
export const FORWARD_RESPONSE = [
  "content-type",
  "content-length",
  "content-disposition",
  "content-range",
  "accept-ranges",
  "etag",
  "last-modified",
] as const;
