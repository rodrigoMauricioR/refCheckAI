"""ffmpeg-based clip trimming + downsampling.

We keep quality reasonably high so line calls remain visually clear while still
controlling upload size/cost.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from src import config


class FfmpegMissing(RuntimeError):
    pass


def _ensure_ffmpeg():
    if shutil.which("ffmpeg") is None:
        raise FfmpegMissing(
            "ffmpeg not found on PATH. Install with `brew install ffmpeg` "
            "(macOS), `sudo apt install ffmpeg` (Linux), or include it in "
            "packages.txt for Hugging Face Spaces."
        )


def preprocess(
    input_path: Path,
    output_path: Path,
    max_seconds: int = config.MAX_CLIP_SECONDS,
    fps: int = config.TARGET_FPS,
    height: int = config.TARGET_HEIGHT,
) -> Path:
    """Trim, downsample, and re-encode a clip in place. Returns output_path."""
    _ensure_ffmpeg()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    vf_parts = [f"fps={fps}", f"scale=-2:{height}:flags=lanczos"]
    if config.APPLY_UNSHARP:
        # Mild edge enhancement helps ball/line separation without aggressive artifacts.
        vf_parts.append("unsharp=5:5:0.8:5:5:0.0")
    vf_expr = ",".join(vf_parts)
    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-t", str(max_seconds),
        "-vf", vf_expr,
        "-an",
        "-c:v", "libx264",
        "-preset", config.X264_PRESET,
        "-crf", str(config.X264_CRF),
        "-profile:v", "high",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{result.stderr[-1500:]}")
    return output_path
