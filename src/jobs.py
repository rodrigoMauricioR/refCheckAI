"""Persistent job queue backed by SQLite.

Replaces the in-memory RateLimiter weakness — jobs and rate-limit counts
survive server restarts and redeploys.

Job lifecycle:
    pending → processing → done
                        → failed

Typical usage in app.py
-----------------------
    import threading
    from src.jobs import JobStore, Job

    store = JobStore()                         # opens / creates the DB

    job_id = store.enqueue(                    # drop a job in the queue
        sport_key="tennis",
        original_call="OUT",
        mode_key="gemini_direct",
        source_name="clip.mp4",
        ip="1.2.3.4",
    )

    def worker(raw_bytes: bytes):
        try:
            store.set_processing(job_id)
            result = analyze_video(...)        # slow Gemini call
            store.set_done(job_id, result, video_path=str(clip_path))
        except Exception as e:
            store.set_failed(job_id, str(e))

    threading.Thread(target=worker, args=(content,), daemon=True).start()

    # Poll from the UI:
    job = store.get(job_id)
    job.status   # "pending" | "processing" | "done" | "failed"
    job.result   # dict once done, None otherwise
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src import config


# ---------------------------------------------------------------------------
# Where we put the database.  One file, lives next to the assets dir.
# ---------------------------------------------------------------------------
DB_PATH: Path = config.ROOT / "refcheck_jobs.db"

# How long to keep finished jobs before they're pruned (seconds).
JOB_TTL_SECONDS: int = 60 * 60 * 24 * 7  # 7 days


# ---------------------------------------------------------------------------
# Data class returned to callers — no raw sqlite3.Row exposure.
# ---------------------------------------------------------------------------
@dataclass
class Job:
    job_id: str
    status: str                          # pending | processing | done | failed
    sport_key: str
    original_call: str
    mode_key: str
    source_name: str
    ip: str
    created_at: float
    updated_at: float
    result: dict[str, Any] | None = None
    video_path: str | None = None
    error: str | None = None
    progress_msg: str = ""


# ---------------------------------------------------------------------------
# JobStore
# ---------------------------------------------------------------------------
class JobStore:
    """Thread-safe SQLite-backed job store.

    A single instance is enough for the whole Streamlit process — SQLite
    handles concurrent access across threads via the check_same_thread=False
    flag and the write lock we keep internally.
    """

    def __init__(self, db_path: Path = DB_PATH):
        self._db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            str(db_path),
            check_same_thread=False,
            isolation_level=None,   # autocommit — we manage transactions ourselves
        )
        self._conn.row_factory = sqlite3.Row
        self._create_schema()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------
    def _create_schema(self) -> None:
        with self._lock:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id       TEXT PRIMARY KEY,
                    status       TEXT NOT NULL DEFAULT 'pending',
                    sport_key    TEXT NOT NULL,
                    original_call TEXT NOT NULL,
                    mode_key     TEXT NOT NULL,
                    source_name  TEXT NOT NULL,
                    ip           TEXT NOT NULL DEFAULT '',
                    created_at   REAL NOT NULL,
                    updated_at   REAL NOT NULL,
                    result_json  TEXT,
                    video_path   TEXT,
                    error        TEXT,
                    progress_msg TEXT NOT NULL DEFAULT ''
                );

                CREATE INDEX IF NOT EXISTS idx_jobs_status
                    ON jobs (status);

                CREATE INDEX IF NOT EXISTS idx_jobs_ip_created
                    ON jobs (ip, created_at);

                -- Persistent rate-limit log (replaces the in-memory dicts
                -- in rate_limit.py that reset on every redeploy).
                CREATE TABLE IF NOT EXISTS rate_events (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    ip         TEXT NOT NULL,
                    ts         REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_rate_ip_ts
                    ON rate_events (ip, ts);
            """)

    # ------------------------------------------------------------------
    # Job CRUD
    # ------------------------------------------------------------------
    def enqueue(
        self,
        sport_key: str,
        original_call: str,
        mode_key: str,
        source_name: str,
        ip: str = "",
    ) -> str:
        """Insert a new job and return its job_id."""
        job_id = uuid.uuid4().hex
        now = time.time()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO jobs
                    (job_id, status, sport_key, original_call, mode_key,
                     source_name, ip, created_at, updated_at)
                VALUES (?, 'pending', ?, ?, ?, ?, ?, ?, ?)
                """,
                (job_id, sport_key, original_call, mode_key,
                 source_name, ip, now, now),
            )
        return job_id

    def get(self, job_id: str) -> Job | None:
        """Fetch a job by ID. Returns None if not found."""
        row = self._conn.execute(
            "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        return self._row_to_job(row) if row else None

    def set_processing(self, job_id: str, progress_msg: str = "Processing…") -> None:
        self._update(job_id, status="processing", progress_msg=progress_msg)

    def set_progress(self, job_id: str, progress_msg: str) -> None:
        """Update the progress message while still processing."""
        self._update(job_id, progress_msg=progress_msg)

    def set_done(
        self,
        job_id: str,
        result: dict[str, Any],
        video_path: str | None = None,
    ) -> None:
        self._update(
            job_id,
            status="done",
            result_json=json.dumps(result),
            video_path=video_path,
            progress_msg="Complete",
        )

    def set_failed(self, job_id: str, error: str) -> None:
        self._update(job_id, status="failed", error=error, progress_msg="Failed")

    def list_recent(self, limit: int = 50) -> list[Job]:
        """Most-recent jobs first — handy for an admin/debug view."""
        rows = self._conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self._row_to_job(r) for r in rows]

    def stats(self) -> dict[str, int]:
        """Counts by status — useful for a dashboard widget."""
        rows = self._conn.execute(
            "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status"
        ).fetchall()
        return {r["status"]: r["n"] for r in rows}

    def prune_old_jobs(self) -> int:
        """Delete jobs older than JOB_TTL_SECONDS. Returns number deleted."""
        cutoff = time.time() - JOB_TTL_SECONDS
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM jobs WHERE created_at < ?", (cutoff,)
            )
        return cur.rowcount

    # ------------------------------------------------------------------
    # Persistent rate limiting
    # ------------------------------------------------------------------
    def record_request(self, ip: str) -> None:
        """Log a new request for rate-limit accounting."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO rate_events (ip, ts) VALUES (?, ?)",
                (ip, time.time()),
            )

    def request_count_for_ip(self, ip: str, window_seconds: int = 3600) -> int:
        """How many requests has this IP made in the last window?"""
        cutoff = time.time() - window_seconds
        row = self._conn.execute(
            "SELECT COUNT(*) FROM rate_events WHERE ip = ? AND ts > ?",
            (ip, cutoff),
        ).fetchone()
        return row[0] if row else 0

    def request_count_global(self, window_seconds: int = 86400) -> int:
        """Total requests across all IPs in the last window."""
        cutoff = time.time() - window_seconds
        row = self._conn.execute(
            "SELECT COUNT(*) FROM rate_events WHERE ts > ?", (cutoff,)
        ).fetchone()
        return row[0] if row else 0

    def prune_rate_events(self) -> None:
        """Clean up rate events older than 24 h — call occasionally."""
        cutoff = time.time() - 86400
        with self._lock:
            self._conn.execute("DELETE FROM rate_events WHERE ts < ?", (cutoff,))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _update(self, job_id: str, **fields) -> None:
        if not fields:
            return
        fields["updated_at"] = time.time()
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [job_id]
        with self._lock:
            self._conn.execute(
                f"UPDATE jobs SET {set_clause} WHERE job_id = ?", values
            )

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> Job:
        result = None
        if row["result_json"]:
            try:
                result = json.loads(row["result_json"])
            except Exception:
                result = None
        return Job(
            job_id=row["job_id"],
            status=row["status"],
            sport_key=row["sport_key"],
            original_call=row["original_call"],
            mode_key=row["mode_key"],
            source_name=row["source_name"],
            ip=row["ip"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            result=result,
            video_path=row["video_path"],
            error=row["error"],
            progress_msg=row["progress_msg"] or "",
        )
