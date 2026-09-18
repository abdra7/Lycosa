"""Controller-owned OS vault. Never fall back to a plaintext keyring backend."""

import sys

# Explicit opt-in session storage for single-worker controllers without an OS vault.
# Never a fallback: only the authenticated admin session-key endpoint writes here.
_session_keys: dict[str, str] = {}


def set_session_key(provider: str, value: str | None) -> None:
    from app.core.config import get_settings

    if get_settings().workers != 1:
        raise SecretStoreUnavailable("Session credentials require one controller worker")
    if value is None:
        _session_keys.pop(provider, None)
    else:
        _session_keys[provider] = value


class SecretStoreUnavailable(Exception):
    pass


def _vault():
    try:
        if sys.platform == "win32":
            from keyring.backends.Windows import WinVaultKeyring

            return WinVaultKeyring()
        if sys.platform == "darwin":
            from keyring.backends.macOS import Keyring

            return Keyring()
        from keyring.backends.SecretService import Keyring

        return Keyring()
    except Exception:
        raise SecretStoreUnavailable("OS secure credential store unavailable") from None


def provider_key(provider: str) -> str | None:
    from app.core.config import get_settings

    if get_settings().workers == 1 and provider in _session_keys:
        return _session_keys[provider]
    try:
        return _vault().get_password("Lycosa/providers", provider)
    except Exception:
        raise SecretStoreUnavailable("OS secure credential store unavailable") from None


def manage_key(provider: str, value: str | None) -> None:
    try:
        vault = _vault()
        if value is None:
            vault.delete_password("Lycosa/providers", provider)
        else:
            vault.set_password("Lycosa/providers", provider, value)
    except Exception:
        raise SecretStoreUnavailable("OS secure credential store operation failed") from None


def main() -> None:
    import argparse
    import getpass

    parser = argparse.ArgumentParser(
        description="Manage controller BYOK in the OS credential vault"
    )
    parser.add_argument("action", choices=["set", "delete", "status"])
    parser.add_argument("provider", choices=["anthropic", "openrouter"])
    args = parser.parse_args()
    try:
        if args.action == "status":
            print("configured" if provider_key(args.provider) else "not configured")
        elif args.action == "delete":
            manage_key(args.provider, None)
            print("deleted")
        else:
            value = getpass.getpass("Provider key (hidden): ")
            if not value.strip() or "\n" in value or "\r" in value:
                raise SystemExit("Invalid credential")
            manage_key(args.provider, value)
            value = ""
            print("stored in OS credential vault")
    except SecretStoreUnavailable:
        raise SystemExit("OS secure credential store unavailable; no plaintext fallback") from None


if __name__ == "__main__":
    main()
