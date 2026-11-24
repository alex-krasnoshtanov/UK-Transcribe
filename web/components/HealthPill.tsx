"use client";

import { useState } from "react";

import { type Health, health } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";

/** Where transcription runs right now: GPU, CPU fallback, loading, or down. */
export function HealthPill() {
  const [h, setH] = useState<Health | null | undefined>(undefined);
  usePoll(async () => setH(await health()), 10_000);

  let label = "checking…";
  let tone = "bg-hover text-ink-3";
  let title = "";
  if (h === null) {
    label = "API unreachable";
    tone = "bg-bad-soft text-bad";
  } else if (h) {
    title = `${h.model} · version ${h.version}`;
    if (h.status === "loading") {
      label = "loading model…";
      tone = "bg-warn-soft text-warn";
    } else if (h.status === "error") {
      label = "model failed";
      tone = "bg-bad-soft text-bad";
      title = h.error ?? title;
    } else if (h.device === "cuda") {
      label = `GPU · ${h.model}`;
      tone = "bg-ok-soft text-ok";
      title = `${h.gpu ?? "GPU"}, ${h.compute_type}${h.gpu_free_mib != null ? `, ${h.gpu_free_mib} MiB free on the card` : ""}`;
    } else {
      label = `CPU (slow) · ${h.model}`;
      tone = "bg-warn-soft text-warn";
      title = h.device_reason ?? "no GPU";
    }
  }
  return (
    <span title={title} className={`rounded-full px-2.5 py-1 text-xs font-medium ${tone}`}>
      {label}
    </span>
  );
}
