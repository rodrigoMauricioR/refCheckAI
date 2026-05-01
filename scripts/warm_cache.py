"""Pre-populate the result cache so demo clips replay instantly during judging.

USAGE
-----
Single clip:
    python3 scripts/warm_cache.py assets/demo_clips/federer.mp4 IN
    python3 scripts/warm_cache.py assets/demo_clips/federer.mp4 IN --sport tennis

Whole folder (uses sidecar <name>.json files for sport + original_call):
    python3 scripts/warm_cache.py --all-from assets/demo_clips/

Sidecar format (assets/demo_clips/federer.json):
    {
      "sport": "tennis",
      "original_call": "OUT",
      "name": "Federer US Open 2009 baseline (optional)"
    }

This script warms the AI-only Gemini cache path used by the app.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make `src.*` imports work when running this file directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import cache, config
from src.rules import registry
from src.rules.loader import load_rules
from src.verdict.video_analyzer import analyze_video
from src.video.preprocess import preprocess


VALID_MODES = {"gemini": cache.MODE_GEMINI_DIRECT}


def _read_sidecar(clip: Path) -> dict:
    sidecar = clip.with_suffix(".json")
    if not sidecar.exists():
        return {}
    try:
        return json.loads(sidecar.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _resolve(clip: Path, sport_arg: str | None, call_arg: str | None) -> tuple[str, str]:
    """Determine (sport_key, original_call) from CLI args + sidecar."""
    sidecar = _read_sidecar(clip)
    sport_key = (sport_arg or sidecar.get("sport") or config.DEFAULT_SPORT).lower()
    call = (call_arg or sidecar.get("original_call"))
    if call is None:
        raise SystemExit(
            f"No original_call given on the command line and none in "
            f"{clip.with_suffix('.json').name}."
        )
    sport = registry.get(sport_key)
    call = call.upper()
    if call not in sport.call_options:
        raise SystemExit(
            f"original_call {call!r} is not valid for {sport.display_name}. "
            f"Allowed: {sport.call_options_pretty}"
        )
    return sport_key, call


def warm(clip: Path, sport_key: str, original_call: str, mode_key: str, force: bool = False) -> None:
    sport = registry.get(sport_key)
    content = clip.read_bytes()

    if not force and cache.lookup(content, sport_key, mode_key, original_call):
        print(f"  [skip] already cached: {clip.name} ({sport.display_name}, {mode_key}, {original_call})")
        return

    work_dir = config.TMP_DIR / f"warm_{clip.stem}"
    work_dir.mkdir(parents=True, exist_ok=True)

    print(f"  [run]  {clip.name} ({sport.display_name}, {mode_key}, {original_call})")
    pre = preprocess(clip, work_dir / "clip.mp4")
    rules_text = load_rules(sport_key)

    # AI-only: Gemini makes verdict from the video and we cache the trimmed clip.
    result = analyze_video(pre, rules_text, original_call, sport)
    video_for_cache = pre
    evidence = None

    key = cache.save(
        content, sport_key, mode_key, original_call, result, video_for_cache,
        evidence=evidence, source_name=clip.name,
    )
    print(f"         -> verdict: {result.get('verdict')}  key={key}")


def main():
    parser = argparse.ArgumentParser(
        description="Warm the demo-clip cache so judging is fast and offline-safe."
    )
    parser.add_argument("clip", nargs="?", type=Path, help="Single clip path")
    parser.add_argument("call", nargs="?", help="Original on-court call (sport-specific; e.g. IN/OUT/GOALTEND_CALLED)")
    parser.add_argument("--sport", choices=registry.keys(),
                        help="Override sport (otherwise read from sidecar JSON)")
    parser.add_argument("--all-from", type=Path, metavar="DIR",
                        help="Warm every video in DIR (uses sidecar .json for sport + call)")
    parser.add_argument("--mode", choices=list(VALID_MODES.keys()), default="gemini",
                        help="Which cache to populate (default: gemini)")
    parser.add_argument("--force", action="store_true",
                        help="Re-run even if a cache entry already exists")
    args = parser.parse_args()

    mode_key = VALID_MODES[args.mode]

    if args.all_from:
        clips = sorted(
            p for p in args.all_from.iterdir()
            if p.suffix.lower() in {".mp4", ".mov", ".m4v", ".webm"}
        )
        if not clips:
            print(f"No clips found in {args.all_from}")
            sys.exit(0)
        for clip in clips:
            try:
                sport_key, call = _resolve(clip, args.sport, None)
                warm(clip, sport_key, call, mode_key, force=args.force)
            except SystemExit as e:
                print(f"  [skip] {clip.name}: {e}")
            except Exception as e:
                print(f"  [fail] {clip.name}: {e}")
    elif args.clip:
        sport_key, call = _resolve(args.clip, args.sport, args.call)
        warm(args.clip, sport_key, call, mode_key, force=args.force)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
