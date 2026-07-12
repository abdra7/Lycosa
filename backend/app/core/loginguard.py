"""Per-IP failed-login throttle (ADR-023; state moved behind the window store
in ADR-027).

The global rate limiter (ratelimit.py) caps total request volume per IP, but on
a production node that budget (120/min) is generous enough to grind password
guesses against a known account. This adds a much tighter, login-specific
sliding window keyed on client IP that counts only *failed* attempts: a
successful login clears the counter, so legitimate users are never locked out
by their own activity.

Deliberately a throttle, not an account lockout — locking an account by email
would let an attacker deny service to a known admin. Keyed by IP, so at LAN
scope one noisy host cannot lock out others.

Failure windows live in the window store: per-process by default, shared
across uvicorn workers when REDIS_URL is set. Unlike the rate limiter, store
errors here propagate (fail closed): a Redis outage must not hand an attacker
an unthrottled window (ADR-027).
"""

from app.core.window_store import InProcessWindowStore, get_window_store

_KEY_PREFIX = "login:"


def reset_login_guard() -> None:
    """Clear all in-process failed-login buckets (test isolation). Tests that
    install a Redis-backed store manage its lifetime themselves."""
    store = get_window_store()
    if isinstance(store, InProcessWindowStore):
        store.clear_prefix(_KEY_PREFIX)


async def is_locked_out(ip: str, *, max_failures: int, window_seconds: int) -> int:
    """Return seconds to wait if `ip` has too many recent failures, else 0."""
    return await get_window_store().penalty(
        f"{_KEY_PREFIX}{ip}", limit=max_failures, window_seconds=window_seconds
    )


async def record_failure(ip: str, *, window_seconds: int) -> None:
    await get_window_store().add(f"{_KEY_PREFIX}{ip}", window_seconds=window_seconds)


async def clear_failures(ip: str) -> None:
    """A successful login wipes the IP's failure history."""
    await get_window_store().clear(f"{_KEY_PREFIX}{ip}")
