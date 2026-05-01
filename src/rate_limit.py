"""Process-local rate limiter. Resets on redeploy — fine for a hackathon.

If you need persistence across restarts, swap the dicts for a tiny SQLite store.
"""

from __future__ import annotations

import time
from collections import defaultdict
from threading import Lock

from src import config


class RateLimiter:
    def __init__(
        self,
        per_ip_per_hour: int = config.PER_IP_PER_HOUR,
        global_per_day: int = config.GLOBAL_PER_DAY,
    ):
        self.per_ip_per_hour = per_ip_per_hour
        self.global_per_day = global_per_day
        self.ip_log: dict[str, list[float]] = defaultdict(list)
        self.global_log: list[float] = []
        self.lock = Lock()

    def _prune(self, now: float):
        cutoff_hour = now - 3600
        cutoff_day = now - 86400
        for ip, log in list(self.ip_log.items()):
            self.ip_log[ip] = [t for t in log if t > cutoff_hour]
            if not self.ip_log[ip]:
                del self.ip_log[ip]
        self.global_log = [t for t in self.global_log if t > cutoff_day]

    def allow(self, ip: str) -> tuple[bool, str]:
        if config.DISABLE_RATE_LIMIT:
            return True, "ok (rate limit disabled)"
        now = time.time()
        with self.lock:
            self._prune(now)
            if len(self.global_log) >= self.global_per_day:
                return False, (
                    "Daily demo limit reached. Try tomorrow, or paste your own "
                    "Gemini key in the sidebar."
                )
            if len(self.ip_log[ip]) >= self.per_ip_per_hour:
                return False, (
                    f"You've hit the per-IP limit of {self.per_ip_per_hour} "
                    "analyses this hour. Try again later."
                )
            self.ip_log[ip].append(now)
            self.global_log.append(now)
            return True, "ok"

    def stats(self) -> dict:
        now = time.time()
        with self.lock:
            self._prune(now)
            return {
                "global_today": len(self.global_log),
                "global_cap": self.global_per_day,
                "tracked_ips": len(self.ip_log),
            }
