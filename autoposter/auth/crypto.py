"""Fernet encryption for credentials stored in the database.

Key resolution order:
1. ``ENCRYPTION_KEY`` env var / ``.env``
2. OS keychain via ``keyring`` (optional extra), service ``autoposter``
3. ``<data_dir>/.encryption_key`` file, created on first use with 0600 perms

The key never touches the database. Losing it means re-running
``autoposter setup <platform>`` for every account.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from autoposter.core.errors import AuthError
from autoposter.utils.logging import get_logger
from autoposter.utils.redact import register_secret

logger = get_logger("auth.crypto")

KEY_SERVICE = "autoposter"
KEY_USERNAME = "encryption-key"
KEY_FILENAME = ".encryption_key"


def generate_key() -> bytes:
    """Generate a new Fernet key."""
    return Fernet.generate_key()


def set_owner_only(path: Path) -> None:
    """Restrict a file to owner read/write on POSIX; no-op elsewhere."""
    if os.name == "posix":
        try:
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except OSError:  # pragma: no cover - exotic filesystems
            logger.warning("Could not set owner-only permissions on %s", path)


def resolve_encryption_key(
    env_key: str | None,
    data_dir: Path,
    *,
    create: bool = True,
) -> bytes:
    """Resolve the Fernet key, creating and persisting one if allowed.

    Args:
        env_key: Value from ``ENCRYPTION_KEY`` (may be None/empty).
        data_dir: Directory used for the on-disk key file fallback.
        create: Generate a key when none exists anywhere.

    Returns:
        A urlsafe base64 Fernet key (bytes).

    Raises:
        AuthError: The provided key is malformed, or none exists and
            ``create`` is False.
    """
    if env_key and env_key.strip():
        key = env_key.strip().encode("ascii")
        _validate_fernet_key(key, source="ENCRYPTION_KEY")
        register_secret(key.decode("ascii"))
        return key

    keyring_key = _load_from_keyring()
    if keyring_key is not None:
        register_secret(keyring_key.decode("ascii"))
        return keyring_key

    key_file = data_dir / KEY_FILENAME
    if key_file.is_file():
        key = key_file.read_text(encoding="utf-8").strip().encode("ascii")
        _validate_fernet_key(key, source=str(key_file))
        register_secret(key.decode("ascii"))
        return key

    if not create:
        raise AuthError(
            "No encryption key found.",
            fix=(
                "Set ENCRYPTION_KEY in .env (generate one with: "
                'python -c "from cryptography.fernet import Fernet; '
                'print(Fernet.generate_key().decode())")'
            ),
        )

    data_dir.mkdir(parents=True, exist_ok=True)
    new_key = generate_key()
    key_file.write_text(new_key.decode("ascii"), encoding="utf-8")
    set_owner_only(key_file)
    _save_to_keyring(new_key)
    register_secret(new_key.decode("ascii"))
    logger.info("Generated a new encryption key at %s", key_file)
    return new_key


def _validate_fernet_key(key: bytes, *, source: str) -> None:
    try:
        Fernet(key)
    except (ValueError, TypeError) as exc:
        raise AuthError(
            f"Invalid Fernet key from {source}.",
            fix=(
                "Regenerate with: python -c \"from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())\""
            ),
        ) from exc


def _load_from_keyring() -> bytes | None:
    try:
        import keyring
    except ImportError:
        return None
    try:
        value = keyring.get_password(KEY_SERVICE, KEY_USERNAME)
    except Exception:  # pragma: no cover - keyring backends vary
        return None
    if not value:
        return None
    return str(value).strip().encode("ascii")


def _save_to_keyring(key: bytes) -> None:
    try:
        import keyring
    except ImportError:
        return
    try:
        keyring.set_password(KEY_SERVICE, KEY_USERNAME, key.decode("ascii"))
    except Exception:  # pragma: no cover - headless systems have no keychain
        logger.debug("Could not store encryption key in the OS keychain")


def encrypt_json(data: dict[str, str], key: bytes) -> str:
    """Serialize ``data`` to JSON and encrypt it with ``key``."""
    payload = json.dumps(data, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return Fernet(key).encrypt(payload).decode("ascii")


def decrypt_json(token: str, key: bytes) -> dict[str, str]:
    """Decrypt an ``encrypt_json`` payload back into a string dict.

    Raises:
        AuthError: The token is corrupt or was encrypted with a different key.
    """
    try:
        payload = Fernet(key).decrypt(token.encode("ascii"))
    except (InvalidToken, ValueError) as exc:
        raise AuthError(
            "Could not decrypt stored credentials (wrong or rotated ENCRYPTION_KEY).",
            fix=(
                "Restore the original ENCRYPTION_KEY, or re-run "
                "`autoposter setup <platform>` for each account."
            ),
        ) from exc
    try:
        data = json.loads(payload.decode("utf-8"))
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AuthError(
            "Stored credentials are corrupt.",
            fix="Re-run `autoposter setup <platform>` for each account.",
        ) from exc
    if not isinstance(data, dict):
        raise AuthError(
            "Stored credentials are corrupt.",
            fix="Re-run `autoposter setup <platform>` for each account.",
        )
    return {str(k): str(v) for k, v in data.items()}
