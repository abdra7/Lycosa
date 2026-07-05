"""Idempotent bootstrap: create the three roles and a default admin from env vars.

Run after migrations: python scripts/seed_admin.py
"""

import asyncio

from sqlalchemy import select

from app.core.config import get_settings
from app.core.security import hash_password
from app.db.session import get_sessionmaker
from app.models import Role, User
from app.models.user import ALL_ROLES, ROLE_ADMIN


async def seed() -> None:
    settings = get_settings()
    async with get_sessionmaker()() as db:
        roles: dict[str, Role] = {}
        for name in ALL_ROLES:
            role = (await db.execute(select(Role).where(Role.name == name))).scalar_one_or_none()
            if role is None:
                role = Role(name=name)
                db.add(role)
                await db.flush()
                print(f"created role: {name}")
            roles[name] = role

        admin = (
            await db.execute(select(User).where(User.email == settings.default_admin_email))
        ).scalar_one_or_none()
        if admin is None:
            db.add(
                User(
                    email=settings.default_admin_email,
                    password_hash=hash_password(settings.default_admin_password),
                    role_id=roles[ROLE_ADMIN].id,
                    is_active=True,
                )
            )
            print(f"created admin user: {settings.default_admin_email}")
        else:
            print(f"admin user already exists: {settings.default_admin_email}")

        await db.commit()


if __name__ == "__main__":
    asyncio.run(seed())
