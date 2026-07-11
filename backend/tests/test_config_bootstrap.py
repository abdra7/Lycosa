"""Zero-config secret bootstrap + production fail-fast (ADR-022, issue #7).

apply_runtime_secrets: placeholder JWT/admin secrets are replaced with
generated values persisted under the data dir, stable across restarts.
enforce_production_secrets: ENVIRONMENT=production refuses to start on
default/placeholder secrets that cannot be auto-generated (the DB password).
"""

import json

import pytest

from app.core.bootstrap import RUNTIME_SECRETS_FILENAME, is_placeholder
from app.core.config import Settings, apply_runtime_secrets, enforce_production_secrets


def make_settings(tmp_path, **overrides) -> Settings:
    defaults = {
        "jwt_secret": "change-me",
        "default_admin_password": "change-me",
        "data_dir": str(tmp_path),
    }
    defaults.update(overrides)
    return Settings(**defaults)


class TestApplyRuntimeSecrets:
    def test_placeholder_jwt_is_replaced_and_persisted(self, tmp_path):
        settings = make_settings(tmp_path)
        apply_runtime_secrets(settings)

        assert not is_placeholder(settings.jwt_secret)
        assert len(settings.jwt_secret) >= 32  # HS256 needs >= 32 bytes (RFC 7518)

        stored = json.loads((tmp_path / RUNTIME_SECRETS_FILENAME).read_text())
        assert stored["jwt_secret"] == settings.jwt_secret

    def test_generated_secrets_are_stable_across_restarts(self, tmp_path):
        first = make_settings(tmp_path)
        apply_runtime_secrets(first)
        second = make_settings(tmp_path)
        apply_runtime_secrets(second)

        assert second.jwt_secret == first.jwt_secret
        assert second.default_admin_password == first.default_admin_password

    def test_explicit_secrets_are_untouched(self, tmp_path):
        settings = make_settings(
            tmp_path,
            jwt_secret="an-explicit-secret-longer-than-32-bytes!",
            default_admin_password="operator-chosen-password",
        )
        apply_runtime_secrets(settings)

        assert settings.jwt_secret == "an-explicit-secret-longer-than-32-bytes!"
        assert settings.default_admin_password == "operator-chosen-password"
        assert not (tmp_path / RUNTIME_SECRETS_FILENAME).exists()

    def test_admin_password_generated_and_flagged(self, tmp_path):
        settings = make_settings(tmp_path)
        apply_runtime_secrets(settings)

        assert not is_placeholder(settings.default_admin_password)
        stored = json.loads((tmp_path / RUNTIME_SECRETS_FILENAME).read_text())
        assert stored["admin_password"] == settings.default_admin_password
        assert stored["admin_password_generated"] is True


class TestEnforceProductionSecrets:
    def test_weak_db_password_fails_in_production(self, tmp_path):
        for weak in ("change-me", "lycosa"):
            settings = make_settings(
                tmp_path,
                environment="production",
                database_url=f"postgresql+asyncpg://lycosa:{weak}@postgres:5432/lycosa",
            )
            apply_runtime_secrets(settings)
            with pytest.raises(RuntimeError, match="production"):
                enforce_production_secrets(settings)

    def test_strong_secrets_pass_in_production(self, tmp_path):
        settings = make_settings(
            tmp_path,
            environment="production",
            database_url="postgresql+asyncpg://lycosa:8f3a9c1d2e4b5a6f@postgres:5432/lycosa",
        )
        apply_runtime_secrets(settings)
        enforce_production_secrets(settings)  # must not raise

    def test_placeholder_jwt_without_bootstrap_fails_in_production(self, tmp_path):
        # defense-in-depth: if apply_runtime_secrets were skipped, the guard
        # still refuses placeholder auth secrets
        settings = make_settings(
            tmp_path,
            environment="production",
            database_url="postgresql+asyncpg://lycosa:8f3a9c1d2e4b5a6f@postgres:5432/lycosa",
        )
        with pytest.raises(RuntimeError, match="JWT_SECRET"):
            enforce_production_secrets(settings)

    def test_development_never_blocks(self, tmp_path):
        settings = make_settings(tmp_path)  # all placeholders, environment=development
        enforce_production_secrets(settings)  # must not raise
