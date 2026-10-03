"""Credential vault: AES-256-GCM bound to the account id, key outside the DB,
optional OS keyring, first-run key generation shared by workers."""

import base64
import os
import sys
import uuid

import pytest
from sqlalchemy import select

from app.core.bootstrap import ensure_credential_key
from app.core.config import Settings, enforce_llm_settings, get_settings
from app.llm import errors, vault
from app.models.llm import LLMCredential, LLMProviderAccount

KEY_A = base64.urlsafe_b64encode(b"a" * 32).decode()
KEY_B = base64.urlsafe_b64encode(b"b" * 32).decode()
SECRET = "sk-test-vault-secret-0123456789"


def test_roundtrip_and_binding_to_the_account():
    cipher = vault.CredentialCipher(KEY_A)
    account, other = uuid.uuid4(), uuid.uuid4()
    nonce, ciphertext = cipher.encrypt(SECRET, account)
    assert SECRET.encode() not in ciphertext
    assert cipher.decrypt(nonce, ciphertext, account, cipher.key_id) == SECRET
    with pytest.raises(errors.CredentialUnavailableError):  # copied onto another row
        cipher.decrypt(nonce, ciphertext, other, cipher.key_id)
    tampered = bytes([ciphertext[0] ^ 1]) + ciphertext[1:]
    with pytest.raises(errors.CredentialUnavailableError):
        cipher.decrypt(nonce, tampered, account, cipher.key_id)


def test_each_write_uses_a_fresh_nonce():
    cipher = vault.CredentialCipher(KEY_A)
    account = uuid.uuid4()
    assert cipher.encrypt(SECRET, account)[0] != cipher.encrypt(SECRET, account)[0]


def test_a_changed_key_is_reported_not_garbled():
    old, new = vault.CredentialCipher(KEY_A), vault.CredentialCipher(KEY_B)
    account = uuid.uuid4()
    nonce, ciphertext = old.encrypt(SECRET, account)
    with pytest.raises(errors.CredentialUnavailableError) as excinfo:
        new.decrypt(nonce, ciphertext, account, old.key_id)
    assert "different key" in excinfo.value.public_message


def test_sealed_tokens_are_purpose_bound():
    cipher = vault.CredentialCipher(KEY_A)
    token = cipher.seal(b"payload", "purpose-1")
    assert cipher.unseal(token, "purpose-1") == b"payload"
    with pytest.raises(ValueError):
        cipher.unseal(token, "purpose-2")
    with pytest.raises(ValueError):
        cipher.unseal(token[:-2] + "AA", "purpose-1")


@pytest.mark.parametrize("bad", ["short", base64.urlsafe_b64encode(b"x" * 16).decode(), "!!!"])
def test_malformed_keys_fail_fast_at_startup(bad):
    with pytest.raises(ValueError):
        vault.decode_key(bad)
    with pytest.raises(RuntimeError):
        enforce_llm_settings(Settings(credential_encryption_key=bad))


def test_malformed_network_lists_fail_fast():
    with pytest.raises(RuntimeError):
        enforce_llm_settings(Settings(llm_user_endpoint_networks="10.0.0.0/8,not-a-net"))


def test_first_run_key_is_created_once_and_reused(tmp_path):
    first = ensure_credential_key(tmp_path)
    assert ensure_credential_key(tmp_path) == first  # a second worker reads the same key
    assert len(vault.decode_key(first)) == 32
    if sys.platform != "win32":
        assert oct(os.stat(tmp_path / "credential-encryption.key").st_mode & 0o777) == "0o600"


@pytest.fixture
async def account(db_session):
    row = LLMProviderAccount(
        provider="openai",
        label="t",
        auth_method="api_key",
        base_url="https://api.openai.com/v1",
    )
    db_session.add(row)
    await db_session.commit()
    return row


async def test_db_store_never_persists_plaintext(db_session, account):
    await vault.store_credential(db_session, account, SECRET)
    await db_session.commit()
    row = (await db_session.execute(select(LLMCredential))).scalar_one()
    assert row.store == "db" and row.key_id == vault.get_cipher().key_id
    assert SECRET.encode() not in (row.ciphertext or b"") + (row.nonce or b"")
    assert await vault.load_credential(db_session, account) == SECRET
    # rotation replaces the ciphertext in place
    await vault.store_credential(db_session, account, SECRET + "x")
    assert await vault.load_credential(db_session, account) == SECRET + "x"
    assert len((await db_session.execute(select(LLMCredential))).scalars().all()) == 1
    await vault.delete_credential(db_session, account)
    assert await vault.load_credential(db_session, account) is None


@pytest.mark.parametrize("bad", ["", " padded ", "line\nbreak", "x" * 5000, "ключ"])
async def test_invalid_secrets_are_refused(db_session, account, bad):
    with pytest.raises(ValueError):
        await vault.store_credential(db_session, account, bad)


async def test_keyring_store_keeps_no_ciphertext(db_session, account, monkeypatch):
    stored: dict[tuple[str, str], str] = {}

    class FakeKeyring:
        def set_password(self, service, user, value):
            stored[(service, user)] = value

        def get_password(self, service, user):
            return stored.get((service, user))

        def delete_password(self, service, user):
            stored.pop((service, user), None)

    from app.services import provider_secrets

    monkeypatch.setattr(provider_secrets, "_vault", lambda: FakeKeyring())
    monkeypatch.setattr(get_settings(), "llm_credential_store", "keyring")
    await vault.store_credential(db_session, account, SECRET)
    row = (await db_session.execute(select(LLMCredential))).scalar_one()
    assert row.store == "keyring" and row.ciphertext is None
    assert stored == {(vault.KEYRING_SERVICE, str(account.id)): SECRET}
    assert await vault.load_credential(db_session, account) == SECRET
    await vault.delete_credential(db_session, account)
    assert stored == {}


async def test_unavailable_keyring_fails_closed(db_session, account, monkeypatch):
    from app.services import provider_secrets

    def broken():
        raise provider_secrets.SecretStoreUnavailable("x")

    monkeypatch.setattr(provider_secrets, "_vault", broken)
    monkeypatch.setattr(get_settings(), "llm_credential_store", "keyring")
    with pytest.raises(errors.CredentialUnavailableError):
        await vault.store_credential(db_session, account, SECRET)
    assert (await db_session.execute(select(LLMCredential))).scalar_one_or_none() is None
