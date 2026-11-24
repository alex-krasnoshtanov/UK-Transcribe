import "server-only";

import { FORWARD_REQUEST, FORWARD_RESPONSE, allowed } from "@/lib/backend";

/**
 * Same-origin proxy to the transcription API (API_URL, http://ukt-api:8000 in
 * the stack). Bodies are streamed both ways, so multi-GB uploads and audio
 * range requests never sit in this server's memory. Route handlers have no
 * body-size limit (that limit applies to proxy.ts and server actions only).
 */
export const dynamic = "force-dynamic";

const API_URL = (process.env.API_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");

type Context = { params: Promise<{ path: string[] }> };

async function forward(request: Request, { params }: Context): Promise<Response> {
  const { path } = await params;
  const joined = path.join("/");
  if (!allowed(joined, request.method)) {
    return Response.json({ detail: "not found" }, { status: 404 });
  }
  const search = new URL(request.url).search;
  const headers = new Headers();
  for (const name of FORWARD_REQUEST) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  const hasBody = request.method === "POST" || request.method === "PUT";
  let upstream: Response;
  try {
    upstream = await fetch(`${API_URL}/${joined}${search}`, {
      method: request.method,
      headers,
      body: hasBody ? request.body : undefined,
      // Needed to stream a request body with Node's fetch.
      ...(hasBody ? { duplex: "half" } : {}),
      cache: "no-store",
      redirect: "manual",
    } as RequestInit);
  } catch {
    return Response.json({ detail: "the transcription API is unreachable" }, { status: 502 });
  }
  const out = new Headers({ "cache-control": "no-store" });
  for (const name of FORWARD_RESPONSE) {
    const value = upstream.headers.get(name);
    if (value) out.set(name, value);
  }
  return new Response(upstream.status === 204 ? null : upstream.body, {
    status: upstream.status,
    headers: out,
  });
}

export const GET = forward;
export const POST = forward;
export const PUT = forward;
export const DELETE = forward;
