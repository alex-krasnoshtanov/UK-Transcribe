import pkg from "@/package.json";

// Liveness of the dashboard only: it never calls the API, so the page stays up
// (and says what is wrong) while the API loads its model or is down.
export const dynamic = "force-dynamic";

export function GET() {
  return Response.json({ status: "ok", version: pkg.version });
}
