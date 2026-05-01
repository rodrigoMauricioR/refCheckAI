"""Central registry of supported sports.

Adding a new sport is a two-step process:
  1. Drop a `<sport_key>.md` file into src/rules/ with sectioned rule text.
  2. Add a Sport entry to SPORTS below.

Everything else (UI sport selector, prompt routing, cache key, CLI sidecars)
reads from this registry.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Sport:
    key: str                       # short, lowercase, no spaces (used in URLs / cache keys)
    display_name: str              # what we show humans
    rules_file: str                # filename inside src/rules/
    role_name: str                 # how the model should self-identify, e.g. "tennis chair umpire"
    call_kind: str                 # what kind of call we're judging, e.g. "line call", "goaltending review"
    play_types: tuple[str, ...]    # play categories the model should consider, e.g. ("serve", "groundstroke")
    contexts: tuple[str, ...]      # situational context options, e.g. ("singles", "doubles")
    call_options: tuple[str, ...]  # what the user picks as the on-court call, in display form
    notes: str = ""                # short note about clip expectations / scope

    @property
    def call_options_pretty(self) -> str:
        return " / ".join(self.call_options)


SPORTS: dict[str, Sport] = {
    "tennis": Sport(
        key="tennis",
        display_name="Tennis",
        rules_file="tennis.md",
        role_name="tennis chair umpire",
        call_kind="line call",
        play_types=("serve", "groundstroke"),
        contexts=("singles", "doubles"),
        call_options=("IN", "OUT"),
        notes=(
            "We judge whether the ball was in or out, including service-box "
            "calls on serves. We do not judge foot faults, lets, or hindrance."
        ),
    ),
}


def get(key: str) -> Sport:
    if key not in SPORTS:
        raise KeyError(f"Unknown sport: {key!r}. Known: {list(SPORTS)}")
    return SPORTS[key]


def list_sports() -> list[Sport]:
    return list(SPORTS.values())


def keys() -> list[str]:
    return list(SPORTS.keys())
