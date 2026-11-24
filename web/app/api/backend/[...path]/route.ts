import "server-only";

import http from "node:http";
import { Readable } from "node:stream";
import { pipeline } from "node:stream/promises";
import type { ReadableStream as NodeReadableStream } from "node:stream/web";

import { FORWARD_REQUEST, FORWARD_RESPONSE, allowed } from "@/lib/backend";

/**
 * Same-origin proxy to the transcription API (API_URL, http://ukt-api:8000 in
 * the stack). Route handlers have no body-size limit (that limit applies to
 * proxy.ts and server actions only).
 *
 * This uses node:http with stream.pipeline, not fetch: Node's fetch keeps a
 * streamed request body in memory (a 900 MB upload grew the process to ~1 GB
 * and got the 384 MB container killed mid-upload). pipeline() honours
 * backpressure, so memory stays flat whatever the file size (~70 MB in the
 * same test). Long uploads also need server-timeouts.cjs.
 */
export const dynamic = "force-dynamic";

const API_URL = (process.env.API_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");

type Context = { params: Promise<{ path: string[] }> };

const unreachable = () => Response.json({ detail: "the transcription API is unreachable" }, { status: 502 });

async function forward(request: Request, { params }: Context): Promise<Response> {
  const { path } = await params;
  const joined = path.join("/");
  if (!allowed(joined, request.method)) {
    return Response.json({ detail: "not found" }, { status: 404 });
  }
  const target = `${API_URL}/${joined}${new URL(request.url).search}`;
  const hasBody = (request.method === "POST" || request.method === "PUT") && request.body !== null;

  const headers: Record<string, string> = {};
  for (const name of FORWARD_REQUEST) {
    const value = request.headers.get(name);
    if (value) headers[name] = value;
  }
  const length = request.headers.get("content-length");
  if (hasBody && length) headers["content-length"] = length;
  if (!hasBody && (request.method === "POST" || request.method === "PUT")) headers["content-length"] = "0";

  return new Promise<Response>((resolve) => {
    const upstream = http.request(target, { method: request.method, headers, signal: request.signal }, (res) => {
      const out = new Headers({ "cache-control": "no-store" });
      for (const name of FORWARD_RESPONSE) {
        const value = res.headers[name];
        if (typeof value === "string") out.set(name, value);
      }
      const status = res.statusCode ?? 502;
      if (status === 204 || status === 304) {
        res.resume();
        resolve(new Response(null, { status, headers: out }));
        return;
      }
      resolve(new Response(Readable.toWeb(res) as unknown as ReadableStream<Uint8Array>, { status, headers: out }));
    });
    // A second resolve() is a no-op, so an error after the response started
    // just ends that stream.
    upstream.on("error", () => resolve(unreachable()));
    if (hasBody) {
      const body = Readable.fromWeb(request.body as unknown as NodeReadableStream<Uint8Array>);
      // If the browser goes away mid-upload, drop the upstream request too, so
      // the API discards the partial file.
      pipeline(body, upstream).catch(() => upstream.destroy());
    } else {
      upstream.end();
    }
  });
}

export const GET = forward;
export const POST = forward;
export const PUT = forward;
export const DELETE = forward;
