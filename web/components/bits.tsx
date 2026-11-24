import type { JobStatus } from "@/lib/api";
import { percent } from "@/lib/format";

const TONES: Record<JobStatus, string> = {
  queued: "bg-hover text-ink-2",
  processing: "bg-accent-soft text-accent",
  done: "bg-ok-soft text-ok",
  error: "bg-bad-soft text-bad",
  cancelled: "bg-hover text-ink-3",
};

export function StatusBadge({ status }: { status: JobStatus }) {
  return (
    <span className={`inline-block rounded px-1.5 py-0.5 text-xs font-medium ${TONES[status]}`}>
      {status}
    </span>
  );
}

export function ProgressBar({ value, label }: { value: number; label?: string }) {
  return (
    <div
      className="h-1.5 w-full overflow-hidden rounded-full bg-line"
      role="progressbar"
      aria-label={label ?? "progress"}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(value * 100)}
    >
      <div
        className="h-full rounded-full bg-accent transition-[width] duration-500"
        style={{ width: percent(value) }}
      />
    </div>
  );
}

export function ErrorNote({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <p role="alert" className="rounded border border-bad/30 bg-bad-soft px-3 py-2 text-bad">
      {message}
    </p>
  );
}
