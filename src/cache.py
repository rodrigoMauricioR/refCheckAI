"""Result cache: turn a slow Gemini call into an instant lookup.

Use cases:
  1. Demo safety net — a judge replays the same clip you tested on, no API call.
  2. Repeat uploads — if someone re-uploads the same file, we serve from disk.
  3. CI/local dev — avoid burning quota when iterating on the UI.

Key: md5(file_bytes + mode + original_call). Different modes or different
on-court calls on the same clip get separate cache entries.

Layout under assets/cached_results/:
  <key>.json           # the verdict dict
  <key>.mp4            # the video to display (trimmed clip OR annotated clip)
  <key>.evidence.json  # CV evidence (only for CV mode), optional
  <key>.meta.json      # sidecar: source name, mode, original_call, timestamp
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src import config


# Stable string keys we use for the cache. Don't change these without wiping
# the cache directory — they're baked into existing keys.
MODE_GEMINI_DIRECT = "gemini_direct"     # raw clip → Gemini → verdict; raw clip displayed


@dataclass
class CachedResult:
    key: str
    result: dict[str, Any]
    video_path: Path
    evidence: dict[str, Any] | None
    meta: dict[str, Any]


def cache_key(content: bytes, sport: str, mode: str, original_call: str) -> str:
    h = hashlib.md5(content)
    h.update(b"|"); h.update(config.CACHE_VERSION.encode())
    h.update(b"|"); h.update(sport.encode())
    h.update(b"|"); h.update(mode.encode())
    h.update(b"|"); h.update(original_call.upper().encode())
    return h.hexdigest()[:16]


def _paths(key: str) -> dict[str, Path]:
    base = config.CACHED_RESULTS_DIR
    return {
        "json": base / f"{key}.json",
        "video": base / f"{key}.mp4",
        "evidence": base / f"{key}.evidence.json",
        "meta": base / f"{key}.meta.json",
    }


def lookup(content: bytes, sport: str, mode: str, original_call: str) -> CachedResult | None:
    key = cache_key(content, sport, mode, original_call)
    paths = _paths(key)
    if not paths["json"].exists() or not paths["video"].exists():
        return None
    result = json.loads(paths["json"].read_text(encoding="utf-8"))
    evidence = (
        json.loads(paths["evidence"].read_text(encoding="utf-8"))
        if paths["evidence"].exists() else None
    )
    meta = (
        json.loads(paths["meta"].read_text(encoding="utf-8"))
        if paths["meta"].exists() else {}
    )
    return CachedResult(
        key=key,
        result=result,
        video_path=paths["video"],
        evidence=evidence,
        meta=meta,
    )


def save(
    content: bytes,
    sport: str,
    mode: str,
    original_call: str,
    result: dict[str, Any],
    video_src: Path,
    evidence: dict[str, Any] | None = None,
    source_name: str | None = None,
) -> str:
    """Persist a result. Returns the cache key."""
    key = cache_key(content, sport, mode, original_call)
    config.CACHED_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    paths = _paths(key)

    paths["json"].write_text(json.dumps(result, indent=2), encoding="utf-8")
    shutil.copy(video_src, paths["video"])
    if evidence is not None:
        paths["evidence"].write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    meta = {
        "source_name": source_name or "uploaded",
        "sport": sport,
        "mode": mode,
        "original_call": original_call.upper(),
        "model": config.GEMINI_MODEL,
        "saved_at": int(time.time()),
    }
    paths["meta"].write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return key


def list_entries() -> list[dict[str, Any]]:
    """Inventory of all cached entries — useful for debugging or admin UIs."""
    base = config.CACHED_RESULTS_DIR
    if not base.exists():
        return []
    out = []
    for meta_file in sorted(base.glob("*.meta.json")):
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            meta["key"] = meta_file.stem.removesuffix(".meta")
            out.append(meta)
        except Exception:
            continue
    return out
