"""Load the markdown rule file for a given sport into a string."""

from __future__ import annotations

from src import config
from src.rules import registry


def load_rules(sport_key: str | None = None) -> str:
    sport_key = sport_key or config.SPORT
    sport = registry.get(sport_key)
    path = config.RULES_DIR / sport.rules_file
    if not path.exists():
        raise FileNotFoundError(f"No rules file at {path}")
    return path.read_text(encoding="utf-8")
