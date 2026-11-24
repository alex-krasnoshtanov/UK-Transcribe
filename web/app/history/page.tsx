"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { ErrorNote, StatusBadge } from "@/components/bits";
import { ApiError, type Job, call } from "@/lib/api";
import { bytes, clock, when } from "@/lib/format";
import { usePoll } from "@/lib/usePoll";

const PAGE = 25;
const STATUSES = ["", "done", "processing", "queued", "error", "cancelled"] as const;

export default function History() {
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<string>("");
  const [page, setPage] = useState(0);
  const [data, setData] = useState<{ total: number; items: Job[] } | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Search as you type, but only after a short pause.
  useEffect(() => {
    const t = setTimeout(() => {
      setQuery(q.trim());
      setPage(0);
    }, 300);
    return () => clearTimeout(t);
  }, [q]);

  const load = useCallback(async () => {
    const params = new URLSearchParams({ limit: String(PAGE), offset: String(page * PAGE) });
    if (query) params.set("q", query);
    if (status) params.set("status", status);
    try {
      setData(await call(`jobs?${params}`));
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "could not load the history");
    }
  }, [query, status, page]);
  usePoll(load, 5000);

  async function remove(job: Job) {
    if (!confirm(`Delete "${job.filename}", its transcript and its audio? This cannot be undone.`)) return;
    try {
      await call(`jobs/${job.id}`, { method: "DELETE" });
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "delete failed");
    }
  }

  const pages = data ? Math.max(1, Math.ceil(data.total / PAGE)) : 1;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="mr-auto text-lg font-semibold">History</h1>
        <input
          type="search"
          className="input w-64"
          placeholder="Search file names and transcripts"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          aria-label="Search"
        />
        <select
          className="input"
          value={status}
          onChange={(e) => {
            setStatus(e.target.value);
            setPage(0);
          }}
          aria-label="Status"
        >
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {s || "all statuses"}
            </option>
          ))}
        </select>
      </div>
      <ErrorNote message={error} />

      <div className="card overflow-x-auto">
        <table className="w-full text-left text-[13px]">
          <thead className="border-b border-line text-ink-3">
            <tr>
              <th className="px-3 py-2 font-medium">File</th>
              <th className="px-3 py-2 font-medium">Status</th>
              <th className="px-3 py-2 font-medium">Length</th>
              <th className="px-3 py-2 font-medium">Size</th>
              <th className="px-3 py-2 font-medium">Uploaded</th>
              <th className="px-3 py-2 font-medium">Audio</th>
              <th className="px-3 py-2" />
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {data?.items.map((job) => (
              <tr key={job.id} className="hover:bg-hover">
                <td className="max-w-xs truncate px-3 py-2">
                  <Link href={`/jobs/${job.id}`}>{job.filename}</Link>
                </td>
                <td className="px-3 py-2">
                  <StatusBadge status={job.status} />
                </td>
                <td className="px-3 py-2">{clock(job.duration)}</td>
                <td className="px-3 py-2">{bytes(job.size_bytes)}</td>
                <td className="px-3 py-2" title={new Date(job.created_at * 1000).toLocaleString("en-GB")}>
                  {when(job.created_at)}
                </td>
                <td className="px-3 py-2 text-ink-3">{job.audio_available ? "kept" : "deleted"}</td>
                <td className="px-3 py-2 text-right">
                  <button type="button" className="btn btn-danger" onClick={() => remove(job)}>
                    Delete
                  </button>
                </td>
              </tr>
            ))}
            {data?.items.length === 0 && (
              <tr>
                <td colSpan={7} className="px-3 py-6 text-center text-ink-3">
                  {query || status ? "No jobs match." : "No jobs yet."}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {data && data.total > PAGE && (
        <div className="flex items-center justify-end gap-2">
          <span className="text-ink-3">
            {data.total} jobs · page {page + 1} of {pages}
          </span>
          <button type="button" className="btn" disabled={page === 0} onClick={() => setPage(page - 1)}>
            ← Newer
          </button>
          <button type="button" className="btn" disabled={page + 1 >= pages} onClick={() => setPage(page + 1)}>
            Older →
          </button>
        </div>
      )}
    </div>
  );
}
