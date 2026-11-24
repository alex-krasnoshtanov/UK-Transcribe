"""Job history and queue in one SQLite file.

One connection guarded by a lock: the worker thread and the API threads all go
through it. At this scale (one GPU worker, a handful of users) that is simpler
and safer than a connection pool, and every write is atomic, unlike the old
status.json files. With a single connection there is no reader/writer
contention, so WAL is not needed, which matters because the cluster's volumes
are NFS folders and WAL's shared-memory file is unreliable on NFS.

The queue is the `jobs` table itself: the worker takes the oldest `queued` row.
On startup any `processing` row (the container died mid-job) is put back to
`queued`, so nothing is lost on a restart.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id            TEXT PRIMARY KEY,
    filename      TEXT NOT NULL,
    audio_path    TEXT,
    size_bytes    INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL,           -- queued | processing | done | error | cancelled
    progress      REAL NOT NULL DEFAULT 0,
    duration      REAL,                    -- audio length, seconds
    model         TEXT,
    device        TEXT,
    error         TEXT,
    cancel        INTEGER NOT NULL DEFAULT 0,
    created_at    REAL NOT NULL,
    started_at    REAL,
    finished_at   REAL,
    audio_deleted_at REAL
);
CREATE INDEX IF NOT EXISTS jobs_status_created ON jobs (status, created_at);
CREATE INDEX IF NOT EXISTS jobs_created ON jobs (created_at);

CREATE TABLE IF NOT EXISTS segments (
    job_id  TEXT NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
    idx     INTEGER NOT NULL,
    start   REAL NOT NULL,
    "end"   REAL NOT NULL,
    text    TEXT NOT NULL,
    PRIMARY KEY (job_id, idx)
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

ACTIVE = ("queued", "processing")


@dataclass(frozen=True)
class SegmentRow:
    idx: int
    start: float
    end: float
    text: str


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _all(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def _one(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def _exec(self, sql: str, params: tuple = ()) -> int:
        with self._lock:
            return self._conn.execute(sql, params).rowcount

    # --- jobs ------------------------------------------------------------------

    def add_job(self, job_id: str, filename: str, audio_path: Path, size_bytes: int,
                duration: float | None = None) -> None:
        self._exec(
            "INSERT INTO jobs (id, filename, audio_path, size_bytes, status, duration, created_at)"
            " VALUES (?, ?, ?, ?, 'queued', ?, ?)",
            (job_id, filename, str(audio_path), size_bytes, duration, time.time()),
        )

    def requeue(self, job_id: str) -> bool:
        """Run a failed or cancelled job again (its audio must still exist)."""
        return self._exec(
            "UPDATE jobs SET status = 'queued', progress = 0, error = NULL, cancel = 0,"
            " started_at = NULL, finished_at = NULL, created_at = ?"
            " WHERE id = ? AND status IN ('error', 'cancelled') AND audio_deleted_at IS NULL",
            (time.time(), job_id),
        ) > 0

    def speed(self) -> float | None:
        """Median-ish processing seconds per audio second, from the last 20 finished jobs."""
        rows = self._all(
            "SELECT (finished_at - started_at) / duration AS rtf FROM jobs"
            " WHERE status = 'done' AND duration > 30 AND started_at IS NOT NULL"
            " ORDER BY finished_at DESC LIMIT 20"
        )
        values = sorted(r["rtf"] for r in rows if r["rtf"] and r["rtf"] > 0)
        return values[len(values) // 2] if values else None

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM jobs WHERE id = ?", (job_id,))

    def list_jobs(
        self, *, q: str = "", status: str = "", limit: int = 50, offset: int = 0
    ) -> tuple[list[dict[str, Any]], int]:
        where, params = ["1=1"], []
        if status:
            where.append("status = ?")
            params.append(status)
        if q:
            # Matches the file name or anything said in the transcript.
            where.append(
                "(filename LIKE ? ESCAPE '\\' OR id IN"
                " (SELECT job_id FROM segments WHERE text LIKE ? ESCAPE '\\'))"
            )
            like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            params += [like, like]
        clause = " AND ".join(where)
        total = self._one(f"SELECT COUNT(*) AS n FROM jobs WHERE {clause}", tuple(params))
        rows = self._all(
            f"SELECT * FROM jobs WHERE {clause} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )
        return rows, int(total["n"]) if total else 0

    def active_jobs(self) -> list[dict[str, Any]]:
        return self._all(
            "SELECT * FROM jobs WHERE status IN ('queued', 'processing')"
            " ORDER BY CASE status WHEN 'processing' THEN 0 ELSE 1 END, created_at"
        )

    def queue_length(self) -> int:
        row = self._one("SELECT COUNT(*) AS n FROM jobs WHERE status IN ('queued', 'processing')")
        return int(row["n"]) if row else 0

    def queue_position(self, job_id: str) -> int | None:
        """1 = next to run. None if the job is not queued."""
        job = self.get_job(job_id)
        if not job or job["status"] != "queued":
            return None
        row = self._one(
            "SELECT COUNT(*) AS n FROM jobs WHERE status = 'queued' AND created_at < ?",
            (job["created_at"],),
        )
        return int(row["n"]) + 1 if row else 1

    def claim_next(self) -> dict[str, Any] | None:
        """Atomically move the oldest queued job to processing and return it."""
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM jobs WHERE status = 'queued' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            self._conn.execute(
                "UPDATE jobs SET status = 'processing', started_at = ?, progress = 0 WHERE id = ?",
                (time.time(), row["id"]),
            )
            self._conn.execute("DELETE FROM segments WHERE job_id = ?", (row["id"],))
            return self.get_job(row["id"])

    def requeue_interrupted(self) -> int:
        return self._exec(
            "UPDATE jobs SET status = 'queued', progress = 0, started_at = NULL"
            " WHERE status = 'processing'"
        )

    def set_running_info(self, job_id: str, model: str, device: str) -> None:
        self._exec("UPDATE jobs SET model = ?, device = ? WHERE id = ?", (model, device, job_id))

    def add_segment(self, job_id: str, idx: int, start: float, end: float, text: str,
                    progress: float, duration: float) -> None:
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                self._conn.execute(
                    'INSERT OR REPLACE INTO segments (job_id, idx, start, "end", text)'
                    " VALUES (?, ?, ?, ?, ?)",
                    (job_id, idx, start, end, text),
                )
                self._conn.execute(
                    "UPDATE jobs SET progress = ?, duration = ? WHERE id = ?",
                    (progress, duration, job_id),
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def finish(self, job_id: str, status: str, *, duration: float | None = None,
               error: str | None = None) -> None:
        progress_sql = ", progress = 1" if status == "done" else ""
        self._exec(
            f"UPDATE jobs SET status = ?, finished_at = ?, error = ?,"
            f" duration = COALESCE(?, duration){progress_sql} WHERE id = ?",
            (status, time.time(), error, duration, job_id),
        )

    def request_cancel(self, job_id: str) -> None:
        self._exec("UPDATE jobs SET cancel = 1 WHERE id = ?", (job_id,))

    def is_cancelled(self, job_id: str) -> bool:
        row = self._one("SELECT cancel FROM jobs WHERE id = ?", (job_id,))
        return bool(row and row["cancel"])

    def cancel_queued(self, job_id: str) -> bool:
        return self._exec(
            "UPDATE jobs SET status = 'cancelled', finished_at = ? WHERE id = ? AND status = 'queued'",
            (time.time(), job_id),
        ) > 0

    def mark_audio_deleted(self, job_id: str) -> None:
        self._exec(
            "UPDATE jobs SET audio_deleted_at = ? WHERE id = ? AND audio_deleted_at IS NULL",
            (time.time(), job_id),
        )

    def delete_job(self, job_id: str) -> None:
        self._exec("DELETE FROM jobs WHERE id = ?", (job_id,))

    def segments(self, job_id: str, after: int = -1) -> list[SegmentRow]:
        rows = self._all(
            'SELECT idx, start, "end", text FROM segments WHERE job_id = ? AND idx > ? ORDER BY idx',
            (job_id, after),
        )
        return [SegmentRow(r["idx"], r["start"], r["end"], r["text"]) for r in rows]

    def iter_expired_audio(self, older_than: float) -> Iterator[dict[str, Any]]:
        yield from self._all(
            "SELECT * FROM jobs WHERE audio_deleted_at IS NULL AND status NOT IN ('queued', 'processing')"
            " AND COALESCE(finished_at, created_at) < ?",
            (older_than,),
        )

    def iter_expired_jobs(self, older_than: float) -> Iterator[dict[str, Any]]:
        yield from self._all(
            "SELECT * FROM jobs WHERE status NOT IN ('queued', 'processing')"
            " AND COALESCE(finished_at, created_at) < ?",
            (older_than,),
        )

    # --- settings --------------------------------------------------------------

    def get_setting(self, key: str) -> str | None:
        row = self._one("SELECT value FROM settings WHERE key = ?", (key,))
        return row["value"] if row else None

    def set_setting(self, key: str, value: str) -> None:
        self._exec(
            "INSERT INTO settings (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
