"""Per-IP failed-login throttle (ADR-023).

The global rate limiter (ratelimit.py) caps total request volume per IP, but on
a production node that budget (120/min) is generous enough to grind password
guesses against a known account. This adds a much tighter, login-specific
sliding window keyed on client IP that counts only *failed* attempts: a
successful login clears the counter, so legitimate users are never locked out
by their own activity.

Deliberately a throttle, not an account lockout — locking an account by email
would let an attacker deny service to a known admin. Keyed by IP, so at LAN
scope one noisy host cannot lock out others. Single-process, like the rate
limiter; move to a shared store when the API scales horizontally.
"""

import time
from collections import deque

# IP -> monotonic timestamps of recent failed logins.
_failures: dict[str, deque[float]] = {}


def reset_login_guard() -> None:
    """Clear all failed-login buckets (test isolation)."""
    _failures.clear()


def _prune(bucket: deque[float], window_start: float) -> None:
    while bucket and bucket[0] < window_start:
        bucket.popleft()


def is_locked_out(ip: str, *, max_failures: int, window_seconds: int) -> int:
    """Return seconds to wait if `ip` has too many recent failures, else 0."""
    bucket = _failures.get(ip)
    if not bucket:
        return 0
    now = time.monotonic()
    window_start = now - window_seconds
    _prune(bucket, window_start)
    if len(bucket) < max_failures:
        return 0
    # locked until the oldest failure ages out of the window
    return max(1, int(bucket[0] - window_start) + 1)


def record_failure(ip: str, *, window_seconds: int) -> None:
    now = time.monotonic()
    bucket = _failures.setdefault(ip, deque())
    _prune(bucket, now - window_seconds)
    bucket.append(now)


def clear_failures(ip: str) -> None:
    """A successful login wipes the IP's failure history."""
    _failures.pop(ip, None)
