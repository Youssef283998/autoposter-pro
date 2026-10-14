"""Encrypted storage of platform account credentials in the database."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from autoposter.auth.crypto import decrypt_json, encrypt_json
from autoposter.core.errors import AuthError
from autoposter.core.models import AccountCredentials, AccountStatus
from autoposter.core.orm import AccountRow
from autoposter.utils.redact import register_secret
from autoposter.utils.timeutil import utcnow

UTC = timezone.utc

DEFAULT_LABEL = "default"
_EXPIRY_MARGIN = timedelta(minutes=5)


class TokenStore:
    """Persist and load account credentials (Fernet-encrypted at rest)."""

    def __init__(self, key: bytes) -> None:
        self._key = key

    def save_account(
        self,
        session: Session,
        *,
        platform: str,
        secrets: dict[str, str],
        label: str = DEFAULT_LABEL,
        kind: str = "manual",
        token_expires_at: datetime | None = None,
        status: AccountStatus = AccountStatus.ACTIVE,
    ) -> int:
        """Insert or update an account; returns the account id.

        Existing rows with the same (platform, label) are replaced so
        ``autoposter setup`` can simply re-run to rotate credentials.
        """
        if not platform:
            raise AuthError("platform is required when saving an account.")
        for value in secrets.values():
            register_secret(value)
        payload = {"__kind__": kind, **secrets}
        encrypted = encrypt_json(payload, self._key)
        row = session.execute(
            select(AccountRow).where(
                AccountRow.platform == platform, AccountRow.label == label
            )
        ).scalar_one_or_none()
        if row is None:
            row = AccountRow(platform=platform, label=label, encrypted_credentials=encrypted)
            session.add(row)
        else:
            row.encrypted_credentials = encrypted
        row.token_expires_at = token_expires_at
        row.status = status.value
        session.flush()
        return int(row.id)

    def get_account(
        self, session: Session, platform: str, label: str = DEFAULT_LABEL
    ) -> AccountCredentials | None:
        """Load and decrypt one account; None when it does not exist."""
        row = session.execute(
            select(AccountRow).where(
                AccountRow.platform == platform, AccountRow.label == label
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return self._to_credentials(row)

    def get_account_by_id(self, session: Session, account_id: int) -> AccountCredentials | None:
        row = session.get(AccountRow, account_id)
        if row is None:
            return None
        return self._to_credentials(row)

    def list_accounts(
        self, session: Session, platform: str | None = None
    ) -> list[AccountCredentials]:
        """List all accounts, optionally filtered by platform."""
        stmt = select(AccountRow).order_by(AccountRow.platform, AccountRow.label)
        if platform is not None:
            stmt = stmt.where(AccountRow.platform == platform)
        rows = session.execute(stmt).scalars().all()
        return [self._to_credentials(row) for row in rows]

    def set_status(self, session: Session, account_id: int, status: AccountStatus) -> None:
        """Update an account's status (active / needs_reauth / disabled)."""
        row = session.get(AccountRow, account_id)
        if row is None:
            raise AuthError(
                f"Account id {account_id} not found.",
                fix="Run `autoposter doctor` to list configured accounts.",
            )
        row.status = status.value

    def set_expiry(
        self, session: Session, account_id: int, expires_at: datetime | None
    ) -> None:
        """Update the OAuth token expiry timestamp (UTC)."""
        row = session.get(AccountRow, account_id)
        if row is None:
            raise AuthError(
                f"Account id {account_id} not found.",
                fix="Run `autoposter doctor` to list configured accounts.",
            )
        if expires_at is not None and expires_at.tzinfo is not None:
            expires_at = expires_at.astimezone(UTC)
        row.token_expires_at = expires_at

    @staticmethod
    def is_expired(
        creds: AccountCredentials,
        *,
        now: datetime | None = None,
        margin: timedelta = _EXPIRY_MARGIN,
    ) -> bool:
        """True when the token expires within ``margin`` (or already did)."""
        if creds.token_expires_at is None:
            return False
        current = now or utcnow()
        return creds.token_expires_at - margin <= current

    def _to_credentials(self, row: AccountRow) -> AccountCredentials:
        payload = decrypt_json(row.encrypted_credentials, self._key)
        kind = payload.pop("__kind__", "manual")
        for value in payload.values():
            register_secret(value)
        return AccountCredentials(
            id=int(row.id),
            platform=row.platform,
            label=row.label,
            kind=kind,
            secrets=payload,
            token_expires_at=row.token_expires_at,
            status=AccountStatus(row.status),
        )
