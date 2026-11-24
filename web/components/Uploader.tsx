"use client";

import { useRef, useState } from "react";

import { ProgressBar } from "@/components/bits";
import { ApiError, type Job, uploadFile } from "@/lib/api";
import { bytes, percent } from "@/lib/format";

type Item = {
  key: string;
  file: File;
  progress: number;
  state: "waiting" | "uploading" | "done" | "error";
  error?: string;
  abort?: () => void;
};

// The dashboard is served over plain HTTP on a campus IP, which browsers don't
// treat as a secure context, so crypto.randomUUID() doesn't exist there.
let counter = 0;

const ACCEPT = ".aac,.flac,.m4a,.mka,.mkv,.mov,.mp3,.mp4,.oga,.ogg,.opus,.wav,.webm,.wma,audio/*,video/*";

/** Drag-and-drop or pick several files; they upload one after another. */
export function Uploader({ onQueued }: { onQueued: (job: Job) => void }) {
  const [items, setItems] = useState<Item[]>([]);
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const busy = useRef(false);
  const pending = useRef<Item[]>([]);

  const patch = (key: string, change: Partial<Item>) =>
    setItems((list) => list.map((it) => (it.key === key ? { ...it, ...change } : it)));

  async function drain() {
    if (busy.current) return;
    busy.current = true;
    while (pending.current.length) {
      const item = pending.current.shift()!;
      const { promise, abort } = uploadFile(item.file, (p) => patch(item.key, { progress: p }));
      patch(item.key, { state: "uploading", abort });
      try {
        const job = await promise;
        patch(item.key, { state: "done", progress: 1, abort: undefined });
        onQueued(job);
      } catch (e) {
        patch(item.key, {
          state: "error",
          abort: undefined,
          error: e instanceof ApiError ? e.message : "upload failed",
        });
      }
    }
    busy.current = false;
  }

  function add(files: FileList | null) {
    if (!files?.length) return;
    const fresh = Array.from(files).map((file) => ({
      key: `${file.name}-${file.size}-${++counter}`,
      file,
      progress: 0,
      state: "waiting" as const,
    }));
    pending.current.push(...fresh);
    setItems((list) => [...fresh, ...list.filter((i) => i.state !== "done")].slice(0, 20));
    void drain();
  }

  return (
    <section className="card p-4">
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(false);
          add(e.dataTransfer.files);
        }}
        className={`flex flex-col items-center justify-center gap-2 rounded-md border-2 border-dashed px-4 py-10 text-center ${
          over ? "border-accent bg-accent-soft" : "border-line-strong"
        }`}
      >
        <p className="text-base font-medium">Drop audio or video files here</p>
        <p className="text-ink-3">mp3, wav, m4a, ogg, opus, flac, mp4, mkv, webm… · Ukrainian is forced</p>
        <button type="button" className="btn btn-primary mt-2" onClick={() => input.current?.click()}>
          Choose files
        </button>
        <input
          ref={input}
          type="file"
          multiple
          accept={ACCEPT}
          className="hidden"
          onChange={(e) => {
            add(e.target.files);
            e.target.value = "";
          }}
        />
      </div>

      {items.length > 0 && (
        <ul className="mt-4 divide-y divide-line">
          {items.map((it) => (
            <li key={it.key} className="flex items-center gap-3 py-2">
              <div className="min-w-0 flex-1">
                <div className="flex justify-between gap-3">
                  <span className="truncate">{it.file.name}</span>
                  <span className="shrink-0 text-ink-3">
                    {it.state === "uploading" && `uploading ${percent(it.progress)} of ${bytes(it.file.size)}`}
                    {it.state === "waiting" && "waiting"}
                    {it.state === "done" && "queued"}
                    {it.state === "error" && <span className="text-bad">{it.error}</span>}
                  </span>
                </div>
                {it.state === "uploading" && (
                  <div className="mt-1.5">
                    <ProgressBar value={it.progress} label={`upload of ${it.file.name}`} />
                  </div>
                )}
              </div>
              {it.state === "uploading" && it.abort && (
                <button type="button" className="btn" onClick={it.abort}>
                  Cancel
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
