import { type NextRequest, NextResponse } from "next/server";

import { contentSecurityPolicy, newNonce } from "@/lib/csp";

/**
 * A fresh CSP nonce for every page (lib/csp.ts). The matcher leaves out
 * /api/*, so uploads streamed through /api/backend never pass through here
 * (and are not subject to the proxy's request body limit).
 */
export function proxy(request: NextRequest) {
  const policy = contentSecurityPolicy(newNonce(), process.env.NODE_ENV === "development");
  const headers = new Headers(request.headers);
  headers.set("Content-Security-Policy", policy);
  const response = NextResponse.next({ request: { headers } });
  response.headers.set("Content-Security-Policy", policy);
  return response;
}

export const config = {
  matcher: [
    {
      source: "/((?!_next/static|_next/image|favicon.ico|health|api/).*)",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
  ],
};
