"use client";

import Link from "next/link";
import { useCallback, useState } from "react";

import { ErrorNote, ProgressBar, StatusBadge } from "@/components/bits";
import { Uploader } from "@/components/Uploader";
import { ApiError, type Job, call } from "@/lib/api";
import { clock, eta, percent, when } from "@/lib/format";
import { usePoll } from "@/lib/usePoll";

export default function Home() {
  const [queue, setQueue] = useState<Job[] | null>(null);
  const [recent, setRecent] = useState<Job[]>([]);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [q, r] = await Promise.all([
        call<{ items: Job[] }>("queue"),
        call<{ items: Job[] }>("jobs?limit=5"),
      ]);
      setQueue(q.items);
      setRecent(r.items.filter((j) => j.status !== "queued" && j.status !== "processing"));
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "could not reach the API");
    }
  }, []);
  usePoll(refresh, 2000);

  async function cancel(id: string) {
    try {
      await call(`jobs/${id}/cancel`, { method: "POST" });
      await refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "cancel failed");
    }
  }

  return (
    <div className="space-y-6">
      <Uploader onQueued={() => void refresh()} />
      <ErrorNote message={error} />

      <section>
        <h2 className="mb-2 text-base font-semibold">Queue</h2>
        <div className="card divide-y divide-line">
          {queue === null && <p className="p-4 text-ink-3">Loading…</p>}
          {queue?.length === 0 && <p className="p-4 text-ink-3">Nothing is waiting. Drop a file above.</p>}
          {queue?.map((job) => (
            <div key={job.id} className="flex items-center gap-4 p-4">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <Link href={`/jobs/${job.id}`} className="truncate font-medium">
                    {job.filename}
                  </Link>
                  <StatusBadge status={job.status} />
                </div>
                <div className="mt-1 text-ink-3">
                  {clock(job.duration)} of audio
                  {job.status === "queued" && job.queue_position != null && ` · #${job.queue_position} in line`}
                  {job.status === "processing" && ` · ${percent(job.progress)}`}
                  {job.eta_seconds != null && ` · done in ${eta(job.eta_seconds)}`}
                  {job.device === "cpu" && " · on CPU (slow)"}
                </div>
                {job.status === "processing" && (
                  <div className="mt-2">
                    <ProgressBar value={job.progress} label={`transcription of ${job.filename}`} />
                  </div>
                )}
              </div>
              <button type="button" className="btn" onClick={() => cancel(job.id)}>
                Cancel
              </button>
            </div>
          ))}
        </div>
      </section>

      {recent.length > 0 && (
        <section>
          <div className="mb-2 flex items-baseline justify-between">
            <h2 className="text-base font-semibold">Recently finished</h2>
            <Link href="/history">All history →</Link>
          </div>
          <div className="card divide-y divide-line">
            {recent.map((job) => (
              <Link
                key={job.id}
                href={`/jobs/${job.id}`}
                className="flex items-center gap-3 p-3 text-ink no-underline hover:bg-hover"
              >
                <span className="min-w-0 flex-1 truncate">{job.filename}</span>
                <StatusBadge status={job.status} />
                <span className="w-24 text-right text-ink-3">{when(job.finished_at)}</span>
              </Link>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
