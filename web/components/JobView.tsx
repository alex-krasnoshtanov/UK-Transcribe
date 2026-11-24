"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useMemo, useRef, useState } from "react";

import { ErrorNote, ProgressBar, StatusBadge } from "@/components/bits";
import { ApiError, type Job, type SegmentRow, call, jobUrl } from "@/lib/api";
import { bytes, clock, eta, percent, when } from "@/lib/format";
import { activeSegment, splitMatches } from "@/lib/highlight";
import { usePoll } from "@/lib/usePoll";

export function JobView({ id }: { id: string }) {
  const router = useRouter();
  const [job, setJob] = useState<Job | null>(null);
  const [missing, setMissing] = useState(false);
  const [segments, setSegments] = useState<SegmentRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [find, setFind] = useState("");
  const [now, setNow] = useState(0);
  const [copied, setCopied] = useState(false);
  const audio = useRef<HTMLAudioElement>(null);
  const lastIdx = useRef(-1);

  const active = job?.status === "queued" || job?.status === "processing";

  const load = useCallback(async () => {
    try {
      const j = await call<Job>(`jobs/${id}`);
      // A retry restarts the transcript from scratch.
      if (j.status === "queued" || (j.status === "processing" && j.progress === 0)) {
        if (lastIdx.current >= 0) {
          lastIdx.current = -1;
          setSegments([]);
        }
      }
      const more = await call<SegmentRow[]>(`jobs/${id}/segments?after=${lastIdx.current}`);
      if (more.length) {
        lastIdx.current = Math.max(lastIdx.current, more[more.length - 1]!.idx);
        // Merge by index: two overlapping loads (React's dev double effect, a
        // slow network) must not show a segment twice.
        setSegments((s) => {
          const last = s.length ? s[s.length - 1]!.idx : -1;
          const fresh = more.filter((m) => m.idx > last);
          return fresh.length ? [...s, ...fresh] : s;
        });
      }
      setJob(j);
      setError(null);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) setMissing(true);
      else setError(e instanceof ApiError ? e.message : "could not reach the API");
    }
  }, [id]);
  // Fast while it runs, slow once finished (someone else may delete it).
  usePoll(load, active || !job ? 2000 : 15000, !missing);

  const playing = useMemo(() => activeSegment(segments, now), [segments, now]);
  const shown = useMemo(() => {
    const f = find.trim().toLocaleLowerCase("uk");
    return f ? segments.filter((s) => s.text.toLocaleLowerCase("uk").includes(f)) : segments;
  }, [segments, find]);

  async function act(path: string, method: string, confirmText?: string) {
    if (confirmText && !confirm(confirmText)) return;
    try {
      await call(path, { method });
      if (method === "DELETE" && path === `jobs/${id}`) {
        router.push("/history");
        return;
      }
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "the action failed");
    }
  }

  function seek(t: number) {
    const a = audio.current;
    if (!a) return;
    a.currentTime = t;
    void a.play();
  }

  async function copyAll() {
    const text = segments.map((s) => s.text).join("\n");
    // navigator.clipboard only exists in secure contexts (HTTPS or localhost);
    // the cluster serves plain HTTP, so fall back to a hidden textarea.
    if (navigator.clipboard) {
      await navigator.clipboard.writeText(text);
    } else {
      const area = document.createElement("textarea");
      area.value = text;
      area.setAttribute("readonly", "");
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      document.execCommand("copy");
      area.remove();
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  if (missing) {
    return (
      <div className="card p-6">
        <p>This job does not exist (any more).</p>
        <Link href="/history">Back to the history</Link>
      </div>
    );
  }
  if (!job) return <p className="text-ink-3">Loading…</p>;

  return (
    <div className="space-y-4">
      <div className="card space-y-3 p-4">
        <div className="flex flex-wrap items-start gap-3">
          <div className="min-w-0 flex-1">
            <h1 className="truncate text-lg font-semibold" title={job.filename}>
              {job.filename}
            </h1>
            <p className="text-ink-3">
              {clock(job.duration)} · {bytes(job.size_bytes)} · uploaded {when(job.created_at)}
              {job.model && ` · ${job.model} on ${job.device === "cuda" ? "GPU" : "CPU"}`}
            </p>
          </div>
          <StatusBadge status={job.status} />
        </div>

        {active && (
          <div className="space-y-1.5">
            <ProgressBar value={job.progress} label="transcription" />
            <p className="text-ink-3">
              {job.status === "queued"
                ? `Waiting: #${job.queue_position ?? "?"} in line`
                : `Transcribing: ${percent(job.progress)}`}
              {job.eta_seconds != null && ` · done in ${eta(job.eta_seconds)}`}
            </p>
          </div>
        )}
        {job.status === "error" && <ErrorNote message={job.error ?? "the transcription failed"} />}

        <div className="flex flex-wrap gap-2">
          {(["txt", "srt", "vtt"] as const).map((fmt) => (
            <a
              key={fmt}
              className={`btn ${segments.length ? "" : "pointer-events-none opacity-50"}`}
              href={jobUrl(id, `/transcript.${fmt}`)}
              download
            >
              Download .{fmt}
            </a>
          ))}
          <button type="button" className="btn" disabled={!segments.length} onClick={copyAll}>
            {copied ? "Copied" : "Copy text"}
          </button>
          <span className="flex-1" />
          {active && (
            <button type="button" className="btn" onClick={() => act(`jobs/${id}/cancel`, "POST")}>
              Cancel
            </button>
          )}
          {(job.status === "error" || job.status === "cancelled") && job.audio_available && (
            <button type="button" className="btn" onClick={() => act(`jobs/${id}/retry`, "POST")}>
              Retry
            </button>
          )}
          {!active && job.audio_available && (
            <button
              type="button"
              className="btn"
              onClick={() =>
                act(`jobs/${id}/audio`, "DELETE", "Delete the audio file? The transcript is kept.")
              }
            >
              Delete audio
            </button>
          )}
          <button
            type="button"
            className="btn btn-danger"
            onClick={() =>
              act(`jobs/${id}`, "DELETE", "Delete this job, its transcript and its audio? This cannot be undone.")
            }
          >
            Delete job
          </button>
        </div>
      </div>
      <ErrorNote message={error} />

      {job.audio_available && (
        <audio
          ref={audio}
          controls
          preload="metadata"
          src={jobUrl(id, "/audio")}
          className="w-full"
          onTimeUpdate={(e) => setNow(e.currentTarget.currentTime)}
        />
      )}

      <section className="card">
        <div className="flex items-center gap-2 border-b border-line p-3">
          <h2 className="mr-auto font-semibold">
            Transcript <span className="font-normal text-ink-3">({segments.length} segments)</span>
          </h2>
          <input
            type="search"
            className="input w-56"
            placeholder="Find in transcript"
            value={find}
            onChange={(e) => setFind(e.target.value)}
            aria-label="Find in transcript"
          />
        </div>
        {segments.length === 0 ? (
          <p className="p-4 text-ink-3">
            {active ? "Text appears here as it is transcribed." : "No text was recognised."}
          </p>
        ) : (
          <ol lang="uk" className="divide-y divide-line">
            {shown.map((s) => (
              <li
                key={s.idx}
                className={`flex gap-3 px-3 py-2 ${s.idx === segments[playing]?.idx ? "bg-accent-soft" : ""}`}
              >
                <button
                  type="button"
                  className="w-16 shrink-0 text-left font-mono text-xs text-accent hover:underline disabled:text-ink-3 disabled:no-underline"
                  onClick={() => seek(s.start)}
                  disabled={!job.audio_available}
                  title={job.audio_available ? "Play from here" : "Audio was deleted"}
                >
                  {clock(s.start)}
                </button>
                <p className="min-w-0 flex-1">
                  {splitMatches(s.text, find).map((p, i) => (p.hit ? <mark key={i}>{p.text}</mark> : p.text))}
                </p>
              </li>
            ))}
            {shown.length === 0 && <li className="p-4 text-ink-3">No segment contains “{find}”.</li>}
          </ol>
        )}
      </section>
    </div>
  );
}
