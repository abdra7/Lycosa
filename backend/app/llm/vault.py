"""Credential vault for provider accounts (ADR-031).

Default store "db": AES-256-GCM with a random 96-bit nonce per write and the
account id as associated data, so a ciphertext copied onto another account
row fails to decrypt. The key comes from CREDENTIAL_ENCRYPTION_KEY or a file
generated once under the data dir; it never touches the database. Store
"keyring" keeps the secret in the controller's OS vault (no plaintext
fallback, same backends as the legacy provider keys).

Plaintext credentials exist only transiently in memory for one call; Python
cannot guarantee their erasure.
"""

import asyncio
import base64
import hashlib
import os
import uuid
from functools import lru_cache

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.bootstrap import ensure_credential_key
from app.core.config import get_settings
from app.llm.errors import CredentialUnavailableError
from app.models.llm import LLMCredential, LLMProviderAccount

KEYRING_SERVICE = "Lycosa/llm-accounts"
MAX_SECRET_CHARS = 4096


def decode_key(value: str) -> bytes:
    raw = base64.urlsafe_b64decode(value.strip() + "=" * (-len(value.strip()) % 4))
    if len(raw) != 32:
        raise ValueError("credential key must be 32 bytes")
    return raw


def _aad(account_id: uuid.UUID) -> bytes:
    return f"lycosa:llm-credential:v1:{account_id}".encode()


class CredentialCipher:
    def __init__(self, key: str) -> None:
        raw = decode_key(key)
        self.key_id = hashlib.sha256(b"lycosa-key-id:" + raw).hexdigest()[:16]
        self._aead = AESGCM(raw)

    def encrypt(self, plaintext: str, account_id: uuid.UUID) -> tuple[bytes, bytes]:
        nonce = os.urandom(12)
        return nonce, self._aead.encrypt(nonce, plaintext.encode("utf-8"), _aad(account_id))

    def decrypt(self, nonce: bytes, ciphertext: bytes, account_id: uuid.UUID, key_id: str) -> str:
        if key_id != self.key_id:
            raise CredentialUnavailableError(
                "The credential was encrypted with a different key; reconnect the account"
            )
        try:
            return self._aead.decrypt(nonce, ciphertext, _aad(account_id)).decode("utf-8")
        except (InvalidTag, ValueError):
            raise CredentialUnavailableError() from None

    # opaque, tamper-proof blobs for short-lived server state (OAuth flows)
    def seal(self, plaintext: bytes, purpose: str) -> str:
        nonce = os.urandom(12)
        blob = nonce + self._aead.encrypt(nonce, plaintext, purpose.encode())
        return base64.urlsafe_b64encode(blob).decode("ascii").rstrip("=")

    def unseal(self, token: str, purpose: str) -> bytes:
        try:
            blob = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
            return self._aead.decrypt(blob[:12], blob[12:], purpose.encode())
        except (InvalidTag, ValueError):
            raise ValueError("invalid sealed token") from None


@lru_cache
def get_cipher() -> CredentialCipher:
    settings = get_settings()
    key = settings.credential_encryption_key or ensure_credential_key(settings.data_dir)
    return CredentialCipher(key)


def validate_secret(value: str) -> str:
    """Printable ASCII, no surrounding whitespace, bounded (as legacy keys)."""
    if (
        not value
        or value != value.strip()
        or len(value) > MAX_SECRET_CHARS
        or any(ord(c) < 33 or ord(c) > 126 for c in value)
    ):
        raise ValueError("invalid credential")
    return value


def _keyring():
    from app.services.provider_secrets import SecretStoreUnavailable, _vault

    try:
        return _vault()
    except SecretStoreUnavailable:
        raise CredentialUnavailableError(
            "The controller OS credential vault is unavailable"
        ) from None


async def _credential_row(db: AsyncSession, account_id: uuid.UUID) -> LLMCredential | None:
    return (
        await db.execute(select(LLMCredential).where(LLMCredential.account_id == account_id))
    ).scalar_one_or_none()


async def store_credential(db: AsyncSession, account: LLMProviderAccount, secret: str) -> None:
    validate_secret(secret)
    row = await _credential_row(db, account.id) or LLMCredential(account_id=account.id, store="db")
    if get_settings().llm_credential_store == "keyring":
        vault = _keyring()
        try:
            await asyncio.to_thread(vault.set_password, KEYRING_SERVICE, str(account.id), secret)
        except Exception:
            raise CredentialUnavailableError("The controller OS credential vault failed") from None
        row.store, row.key_id, row.nonce, row.ciphertext = "keyring", None, None, None
    else:
        cipher = get_cipher()
        row.nonce, row.ciphertext = cipher.encrypt(secret, account.id)
        row.store, row.key_id = "db", cipher.key_id
    db.add(row)
    await db.flush()


async def load_credential(db: AsyncSession, account: LLMProviderAccount) -> str | None:
    row = await _credential_row(db, account.id)
    if row is None:
        return None
    if row.store == "keyring":
        vault = _keyring()
        try:
            return await asyncio.to_thread(vault.get_password, KEYRING_SERVICE, str(account.id))
        except Exception:
            raise CredentialUnavailableError("The controller OS credential vault failed") from None
    if row.nonce is None or row.ciphertext is None or row.key_id is None:
        raise CredentialUnavailableError()
    return get_cipher().decrypt(row.nonce, row.ciphertext, account.id, row.key_id)


async def has_credential(db: AsyncSession, account_id: uuid.UUID) -> bool:
    return await _credential_row(db, account_id) is not None


async def delete_credential(db: AsyncSession, account: LLMProviderAccount) -> None:
    row = await _credential_row(db, account.id)
    if row is None:
        return
    if row.store == "keyring":
        vault = _keyring()
        try:
            await asyncio.to_thread(vault.delete_password, KEYRING_SERVICE, str(account.id))
        except Exception:
            pass  # already absent; the row below is what grants access
    await db.delete(row)
    await db.flush()
