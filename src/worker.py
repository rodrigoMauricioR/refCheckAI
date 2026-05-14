"""Background worker — runs the heavy Gemini pipeline off the main thread.

This module owns the transition:
    pending  →  processing  →  done
                            →  failed

It is intentionally kept thin: all real logic lives in preprocess.py,
video_analyzer.py, and cache.py.  The worker just orchestrates them and
keeps the JobStore up to date so the UI can poll progress.

Typical usage in app.py
-----------------------
    from src.worker import submit_job

    job_id = submit_job(
        raw_bytes   = content,          # bytes from st.file_uploader
        sport_key   = sport.key,
        original_call = original_call,
        mode_key    = cache.MODE_GEMINI_DIRECT,
        source_name = uploaded.name,
        ip          = _client_ip(),
        store       = st.session_state.job_store,
        api_key_override = user_key or None,
    )
    # Then poll:  store.get(job_id).status / .result / .progress_msg
"""

from __future__ import annotations

import threading
import uuid
from pathlib import Path
from typing import Any

from src import cache, config
from src.jobs import JobStore
from src.rules import registry
from src.rules.loader import load_rules
from src.verdict.video_analyzer import analyze_video
from src.video.preprocess import preprocess


def submit_job(
    raw_bytes: bytes,
    sport_key: str,
    original_call: str,
    mode_key: str,
    source_name: str,
    ip: str,
    store: JobStore,
    api_key_override: str | None = None,
) -> str:
    """Enqueue a job and immediately spin up a daemon thread to process it.

    Returns the job_id straight away — the caller does NOT wait for the
    Gemini call to finish.
    """
    job_id = store.enqueue(
        sport_key=sport_key,
        original_call=original_call,
        mode_key=mode_key,
        source_name=source_name,
        ip=ip,
    )

    thread = threading.Thread(
        target=_run,
        args=(job_id, raw_bytes, sport_key, original_call,
              mode_key, source_name, store, api_key_override),
        daemon=True,          # dies with the main process — no orphan threads
        name=f"worker-{job_id[:8]}",
    )
    thread.start()
    return job_id


# ---------------------------------------------------------------------------
# Internal — runs inside the background thread
# ---------------------------------------------------------------------------
def _run(
    job_id: str,
    raw_bytes: bytes,
    sport_key: str,
    original_call: str,
    mode_key: str,
    source_name: str,
    store: JobStore,
    api_key_override: str | None,
) -> None:
    """Full pipeline: preprocess → Gemini → cache.  Updates job at each step."""

    store.set_processing(job_id, progress_msg="Starting…")

    work_dir = config.TMP_DIR / f"job_{job_id}"
    work_dir.mkdir(parents=True, exist_ok=True)

    try:
        sport = registry.get(sport_key)
        rules_text = load_rules(sport_key)

        # ── Step 1: write raw bytes to disk ─────────────────────────────
        store.set_progress(job_id, "Saving uploaded clip…")
        raw_path = work_dir / "raw.mp4"
        raw_path.write_bytes(raw_bytes)

        # ── Step 2: ffmpeg preprocess ────────────────────────────────────
        store.set_progress(job_id, "Compressing video…")
        clip_path = preprocess(raw_path, work_dir / "clip.mp4")

        # ── Step 3: Gemini analysis ──────────────────────────────────────
        store.set_progress(
            job_id,
            f"Asking Gemini to analyze the clip ({sport.display_name})…"
        )
        result: dict[str, Any] = analyze_video(
            clip_path,
            rules_text,
            original_call,
            sport,
            api_key_override=api_key_override,
        )

        # ── Step 4: save to file cache ───────────────────────────────────
        store.set_progress(job_id, "Saving result to cache…")
        try:
            cache.save(
                raw_bytes,
                sport_key,
                mode_key,
                original_call,
                result,
                clip_path,
                evidence=None,
                source_name=source_name,
            )
        except Exception:
            pass  # cache failure is non-fatal

        # ── Step 5: mark done ────────────────────────────────────────────
        store.set_done(job_id, result, video_path=str(clip_path))

    except Exception as exc:
        store.set_failed(job_id, error=str(exc))
