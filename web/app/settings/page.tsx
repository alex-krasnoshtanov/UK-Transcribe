"use client";

import { useEffect, useState } from "react";

import { ErrorNote } from "@/components/bits";
import { ApiError, type Health, type Settings, call, health } from "@/lib/api";

export default function SettingsPage() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [h, setH] = useState<Health | null>(null);
  const [audioDays, setAudioDays] = useState("");
  const [transcriptDays, setTranscriptDays] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const s = await call<Settings>("settings");
        setSettings(s);
        setAudioDays(String(s.audio_retention_days));
        setTranscriptDays(String(s.transcript_retention_days));
      } catch (e) {
        setError(e instanceof ApiError ? e.message : "could not load the settings");
      }
      setH(await health());
    })();
  }, []);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setSaved(false);
    try {
      const s = await call<Settings>("settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          audio_retention_days: Number(audioDays),
          transcript_retention_days: Number(transcriptDays),
        }),
      });
      setSettings(s);
      setSaved(true);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "saving failed");
    }
  }

  return (
    <div className="max-w-2xl space-y-6">
      <h1 className="text-lg font-semibold">Settings</h1>
      <ErrorNote message={error} />

      <form onSubmit={save} className="card space-y-4 p-4">
        <h2 className="font-semibold">Retention</h2>
        <p className="text-ink-3">
          Cleanup runs every hour. 0 keeps things forever. The storage is a shared university NFS share, so
          delete audio you no longer need.
        </p>
        <label className="flex items-center justify-between gap-4">
          <span>Delete uploaded audio after (days)</span>
          <input
            type="number"
            min={0}
            max={3650}
            required
            className="input w-24"
            value={audioDays}
            onChange={(e) => setAudioDays(e.target.value)}
          />
        </label>
        <label className="flex items-center justify-between gap-4">
          <span>Delete whole jobs and transcripts after (days)</span>
          <input
            type="number"
            min={0}
            max={3650}
            required
            className="input w-24"
            value={transcriptDays}
            onChange={(e) => setTranscriptDays(e.target.value)}
          />
        </label>
        <div className="flex items-center gap-3">
          <button type="submit" className="btn btn-primary" disabled={!settings}>
            Save
          </button>
          {saved && <span className="text-ok">Saved</span>}
        </div>
      </form>

      <section className="card p-4">
        <h2 className="mb-2 font-semibold">Service</h2>
        <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1">
          <dt className="text-ink-3">Model</dt>
          <dd>{settings?.model ?? "–"}</dd>
          <dt className="text-ink-3">Decoding</dt>
          <dd>{settings ? `beam ${settings.beam_size}, VAD ${settings.vad ? "on" : "off"}, language uk` : "–"}</dd>
          <dt className="text-ink-3">Device</dt>
          <dd>
            {h?.status === "ok"
              ? `${h.device === "cuda" ? h.gpu ?? "GPU" : "CPU"} (${h.compute_type}) · ${h.device_reason}`
              : h?.status ?? "–"}
          </dd>
          <dt className="text-ink-3">Uploads</dt>
          <dd>{settings ? `up to ${settings.max_upload_mb} MB, ${settings.max_queue} jobs in the queue` : "–"}</dd>
          <dt className="text-ink-3">Version</dt>
          <dd className="font-mono text-xs">{h?.version ?? "–"}</dd>
        </dl>
      </section>
    </div>
  );
}
