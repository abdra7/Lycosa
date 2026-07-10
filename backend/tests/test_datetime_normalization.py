"""UT-BE-02: SQLite naive-datetime normalization.

Postgres returns tz-aware datetimes for `DateTime(timezone=True)` columns;
SQLite returns naive ones. The `as_utc` helpers stamp UTC onto naive values so
comparisons against `datetime.now(UTC)` work on both backends. These tests pin
the helper semantics, the driver behavior they exist for (as canaries), and
the three call sites where getting this wrong breaks auth or node health.

Behavioral tests call `expire_all()` (or go through the HTTP client's own
sessions) before checking, so values are genuinely re-read from SQLite as
naive datetimes instead of served from the identity map still tz-aware.
"""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import API_KEY_HEADER, sha256_hex
from app.models import NodeStatus, Session
from app.services.auth import as_utc, get_valid_session
from app.services.node import _as_utc, sweep_offline_nodes
from tests.conftest import make_node

NORMALIZERS = [as_utc, _as_utc]  # auth and node keep their own copies


@pytest.mark.parametrize("normalize", NORMALIZERS)
def test_aware_datetimes_pass_through_unchanged(normalize) -> None:
    utc = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    assert normalize(utc) is utc

    offset = datetime(2026, 7, 10, 12, 0, tzinfo=timezone(timedelta(hours=3)))
    assert normalize(offset) is offset  # Postgres path: never re-stamped


@pytest.mark.parametrize("normalize", NORMALIZERS)
def test_naive_datetimes_are_stamped_utc(normalize) -> None:
    naive = datetime(2026, 7, 10, 12, 0)
    normalized = normalize(naive)
    assert normalized.tzinfo == UTC
    # stamping, not converting: the wall clock must not shift
    assert normalized.replace(tzinfo=None) == naive


async def test_canary_sqlite_round_trips_aware_utc_to_naive(
    db_session: AsyncSession, users: dict
) -> None:
    """Pins the driver behavior the helpers exist for: an aware UTC write
    comes back naive from SQLite, and as_utc restores the exact instant. If
    this ever fails, aiosqlite/SQLAlchemy changed and the helpers are moot."""
    written = datetime(2026, 7, 10, 12, 30, 45, 123456, tzinfo=UTC)
    session = Session(
        user_id=users["admin"].id, token_hash=sha256_hex("canary-jti"), expires_at=written
    )
    db_session.add(session)
    await db_session.commit()
    db_session.expire_all()

    read_back = (
        await db_session.execute(
            select(Session).where(Session.token_hash == sha256_hex("canary-jti"))
        )
    ).scalar_one()
    assert read_back.expires_at.tzinfo is None  # SQLite dropped the tz
    assert as_utc(read_back.expires_at) == written  # …but not the instant


async def test_canary_non_utc_offsets_are_silently_lost(
    db_session: AsyncSession, users: dict
) -> None:
    """The normalization contract holds ONLY if writers always write UTC:
    SQLite stores the wall clock and drops the offset, so a +03:00 write comes
    back three hours in the future once as_utc stamps UTC onto it. This canary
    documents why every datetime written to the DB must be datetime.now(UTC),
    never a local or offset time."""
    plus3 = timezone(timedelta(hours=3))
    written = datetime(2026, 7, 10, 12, 0, tzinfo=plus3)  # == 09:00 UTC
    session = Session(
        user_id=users["admin"].id, token_hash=sha256_hex("offset-jti"), expires_at=written
    )
    db_session.add(session)
    await db_session.commit()
    db_session.expire_all()

    read_back = (
        await db_session.execute(
            select(Session).where(Session.token_hash == sha256_hex("offset-jti"))
        )
    ).scalar_one()
    assert as_utc(read_back.expires_at) - written == timedelta(hours=3)


async def test_expired_session_rejected_after_naive_read(
    db_session: AsyncSession, users: dict
) -> None:
    session = Session(
        user_id=users["admin"].id,
        token_hash=sha256_hex("expired-jti"),
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    db_session.add(session)
    await db_session.commit()
    db_session.expire_all()  # force the naive re-read from SQLite

    assert await get_valid_session(db_session, "expired-jti") is None


async def test_live_session_accepted_after_naive_read(
    db_session: AsyncSession, users: dict
) -> None:
    session = Session(
        user_id=users["admin"].id,
        token_hash=sha256_hex("live-jti"),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    db_session.add(session)
    await db_session.commit()
    db_session.expire_all()

    found = await get_valid_session(db_session, "live-jti")
    assert found is not None and found.id == session.id


async def test_api_key_expiry_enforced_over_http(
    client: AsyncClient, db_session: AsyncSession, node_api_key: tuple, users: dict
) -> None:
    """End to end on SQLite: the HTTP layer reads the key through its own
    fresh session (naive datetimes), so this breaks loudly if the expiry
    comparison ever mixes naive and aware."""
    full_key, record = node_api_key

    record.expires_at = datetime.now(UTC) + timedelta(hours=1)
    await db_session.commit()
    response = await client.get("/api/v1/me", headers={API_KEY_HEADER: full_key})
    assert response.status_code == 200

    record.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    await db_session.commit()
    response = await client.get("/api/v1/me", headers={API_KEY_HEADER: full_key})
    assert response.status_code == 401


async def test_sweep_compares_naive_heartbeats_without_crashing(
    db_session: AsyncSession, users: dict
) -> None:
    stale = await make_node(db_session, "stale-box", status="online")
    fresh = await make_node(db_session, "fresh-box", status="online")
    stale.last_heartbeat_at = datetime.now(UTC) - timedelta(days=1)
    fresh.last_heartbeat_at = datetime.now(UTC)
    await db_session.commit()
    db_session.expire_all()  # heartbeats now come back naive from SQLite

    flipped = await sweep_offline_nodes(db_session)

    assert flipped == 1
    db_session.expire_all()
    await db_session.refresh(stale)
    await db_session.refresh(fresh)
    assert stale.status == NodeStatus.OFFLINE
    assert fresh.status == NodeStatus.ONLINE
