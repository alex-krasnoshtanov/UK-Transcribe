/** Browser-side client for the API, always through the same-origin proxy. */

export type JobStatus = "queued" | "processing" | "done" | "error" | "cancelled";

export type Job = {
  id: string;
  filename: string;
  status: JobStatus;
  progress: number;
  duration: number | null;
  size_bytes: number;
  model: string | null;
  device: string | null;
  error: string | null;
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
  audio_available: boolean;
  eta_seconds: number | null;
  queue_position: number | null;
};

export type SegmentRow = { idx: number; start: number; end: number; text: string };

export type Settings = {
  audio_retention_days: number;
  transcript_retention_days: number;
  max_upload_mb: number;
  max_queue: number;
  allowed_types: string[];
  model: string;
  beam_size: number;
  vad: boolean;
};

export type Health = {
  status: "ok" | "loading" | "error";
  version: string;
  model: string;
  queue: number;
  current_job: string | null;
  device?: string;
  compute_type?: string;
  device_reason?: string;
  gpu?: string | null;
  gpu_free_mib?: number | null;
  error?: string;
};

export const BASE = "/api/backend";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function detail(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body?.detail === "string") return body.detail;
    if (Array.isArray(body?.detail)) return body.detail.map((d: { msg?: string }) => d.msg).join("; ");
  } catch {
    /* not JSON */
  }
  return `request failed (${res.status})`;
}

export async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}/${path}`, { cache: "no-store", ...init });
  if (!res.ok) throw new ApiError(res.status, await detail(res));
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/** Health is read even when the API answers 503 (loading or failed). */
export async function health(): Promise<Health | null> {
  try {
    const res = await fetch(`${BASE}/healthz`, { cache: "no-store" });
    return (await res.json()) as Health;
  } catch {
    return null;
  }
}

export const jobUrl = (id: string, rest = "") => `${BASE}/jobs/${id}${rest}`;

/** Upload one file as the raw request body, reporting upload progress. */
export function uploadFile(
  file: File,
  onProgress: (fraction: number) => void,
): { promise: Promise<Job>; abort: () => void } {
  const xhr = new XMLHttpRequest();
  const promise = new Promise<Job>((resolve, reject) => {
    xhr.open("POST", `${BASE}/jobs?filename=${encodeURIComponent(file.name)}`);
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded / e.total);
    };
    xhr.onload = () => {
      let body: { detail?: unknown } | Job | null = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        /* not JSON */
      }
      if (xhr.status === 201 && body) resolve(body as Job);
      else {
        const d = body && "detail" in body && typeof body.detail === "string" ? body.detail : null;
        reject(new ApiError(xhr.status, d ?? `upload failed (${xhr.status})`));
      }
    };
    xhr.onerror = () => reject(new ApiError(0, "network error during upload"));
    xhr.onabort = () => reject(new ApiError(0, "upload cancelled"));
    xhr.send(file);
  });
  return { promise, abort: () => xhr.abort() };
}
