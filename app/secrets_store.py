"""Encrypted credential storage for long-lived application secrets.

Architecture (spec section 15):

    Settings API
        ↓
    CredentialStore  (this module)
        ├── EventStore / SQLite   (core.persistence — existing app DB)
        └── EncryptionService     (Fernet authenticated encryption)

Storage model:
  * Ciphertext lives in the EXISTING application SQLite database
    (data/events.db), table ``secrets`` (schema v10) — no second DB.
  * The Fernet master key lives OUTSIDE the database at
    ``data/master.key`` — generated once, reused forever, never logged,
    never returned by any API, never committed (data/ is gitignored).
  * The daily OAuth ACCESS TOKEN is deliberately NOT stored here; it
    remains runtime-memory-only (see brokers.upstox.auth).

Failure policy (lost/corrupt master key):
  * If ciphertext exists but the key is missing/unreadable/invalid, the
    store does NOT silently regenerate a key (that would hide permanent
    data loss). It reports credentials as unreadable via
    CredentialDecryptError so the operator can re-enter them.
  * An explicit operator SAVE with a lost key generates a fresh key and
    replaces the unreadable rows — replacement is always possible.

Plaintext secrets exist only transiently in backend memory: WebUI POST →
encrypt → SQLite. Never in logs, temp files, responses, or the frontend.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.paths import DATA_ROOT, ensure_dir

logger = logging.getLogger("event_server")

ENCRYPTION_SCHEME = "fernet-v1"
_UPSTOX_PROVIDER = "upstox"
_KEY_FILE_NAME = "master.key"


class CredentialDecryptError(Exception):
    """Stored credentials exist but cannot be decrypted (key lost/rotated).

    Safe to surface as a boolean-ish condition to the WebUI; the exception
    message itself contains no secret or ciphertext material.
    """


class EncryptionService:
    """Thin Fernet wrapper — the only place crypto primitives are touched."""

    def __init__(self, master_key: bytes) -> None:
        try:
            self._fernet = Fernet(master_key)
        except Exception as exc:
            raise CredentialDecryptError(
                "master key file is not a valid Fernet key"
            ) from exc

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode("ascii")).decode(
                "utf-8")
        except InvalidToken as exc:
            raise CredentialDecryptError(
                "stored credentials cannot be decrypted "
                "(master key missing or rotated)"
            ) from exc


def _master_key_path(base_dir: Path | None) -> Path:
    root = Path(base_dir) if base_dir is not None else DATA_ROOT
    return root / _KEY_FILE_NAME


def _load_or_create_master_key(
    key_path: Path,
    *,
    ciphertext_exists: bool,
) -> bytes | None:
    """Load the master key, creating one ONLY when safe.

    Returns None when ciphertext exists but the key is unusable — callers
    must treat stored credentials as unreadable, never regenerate silently.
    """
    if key_path.is_file():
        try:
            key = key_path.read_bytes().strip()
        except OSError as exc:
            logger.warning("master.key unreadable: %s", type(exc).__name__)
            return None if ciphertext_exists else None
        if key:
            return key
        # Empty key file.
        logger.warning("master.key is empty")
        return None if ciphertext_exists else None

    # No key file. Safe to generate ONLY if no ciphertext depends on a
    # lost key (i.e. nothing encrypted yet).
    if ciphertext_exists:
        logger.error(
            "master.key missing but encrypted credentials exist - "
            "NOT regenerating key; credentials marked unreadable"
        )
        return None

    key = Fernet.generate_key()
    ensure_dir(key_path.parent)
    fd = os.open(str(key_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, key)
    finally:
        os.close(fd)
    logger.info("generated new master key (%s)", key_path.name)
    return key


class CredentialStore:
    """Persistent encrypted credential storage backed by the app's SQLite DB."""

    def __init__(
        self,
        event_store: Any,
        *,
        data_dir: Path | None = None,
    ) -> None:
        self._store = event_store
        self._data_dir = Path(data_dir) if data_dir is not None else DATA_ROOT
        self._key_path = _master_key_path(self._data_dir)
        self._encryption: EncryptionService | None = None
        self._key_unusable = False

    # -- master key lifecycle -------------------------------------------------

    def _ciphertext_exists(self) -> bool:
        return self._store.has_secret(_UPSTOX_PROVIDER, "api_key") or \
            self._store.has_secret(_UPSTOX_PROVIDER, "api_secret")

    def _get_encryption(self, *, allow_generate: bool) -> EncryptionService | None:
        """Return a usable EncryptionService, or None if the key is unusable.

        ``allow_generate=True`` permits first-run key creation AND explicit
        replacement after a lost key (operator-driven save).
        """
        if self._encryption is not None:
            return self._encryption
        ciphertext = self._ciphertext_exists()
        key = _load_or_create_master_key(
            self._key_path,
            ciphertext_exists=ciphertext,
        )
        if key is None:
            self._key_unusable = True
            if allow_generate:
                # Explicit operator SAVE after a lost/unreadable key:
                # rotate deliberately (operator is overwriting the
                # unreadable credentials knowingly). Never done silently.
                logger.warning("rotating master key via explicit save")
                key = Fernet.generate_key()
                self._write_key(key)
                self._encryption = EncryptionService(key)
                self._key_unusable = False
                return self._encryption
            return None
        try:
            self._encryption = EncryptionService(key)
        except CredentialDecryptError:
            # Invalid key material on disk. Never overwrite it silently;
            # only an explicit save (allow_generate) may rotate it.
            self._key_unusable = True
            if not allow_generate:
                return None
            logger.warning("replacing invalid master key via explicit save")
            key = Fernet.generate_key()
            self._write_key(key)
            self._encryption = EncryptionService(key)
        self._key_unusable = False
        return self._encryption

    def _write_key(self, key: bytes) -> None:
        ensure_dir(self._key_path.parent)
        fd = os.open(str(self._key_path),
                     os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, key)
        finally:
            os.close(fd)

    @property
    def key_unusable(self) -> bool:
        """True when ciphertext exists but cannot be decrypted."""
        return self._key_unusable

    # -- public API ------------------------------------------------------------

    def save_upstox_app_credentials(self, api_key: str, api_secret: str) -> None:
        """Encrypt and transactionally persist both credential rows.

        Explicit operator action: permitted to create/replace the master key
        even after a previous key was lost (the operator is overwriting the
        unreadable credentials knowingly).
        """
        for label, value in (("api_key", api_key), ("api_secret", api_secret)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{label} must be a non-empty string")
            if len(value) > 512:
                raise ValueError(f"{label} too long")
        enc = self._get_encryption(allow_generate=True)
        if enc is None:
            raise CredentialDecryptError(
                "master key unavailable; cannot encrypt credentials")
        self._store.upsert_secrets(_UPSTOX_PROVIDER, {
            "api_key": (enc.encrypt(api_key.strip()), ENCRYPTION_SCHEME),
            "api_secret": (enc.encrypt(api_secret.strip()), ENCRYPTION_SCHEME),
        })
        logger.info("upstox app credentials saved (encrypted, %d rows)",
                    2)

    def load_upstox_app_credentials(self) -> dict[str, str] | None:
        """Decrypt and return credentials, or None if absent/unreadable.

        Returns None (not an exception) for the plain not-configured case;
        raises CredentialDecryptError only when ciphertext exists but cannot
        be decrypted, so callers can show the re-enter prompt safely.
        """
        enc = self._get_encryption(allow_generate=False)
        if enc is None:
            if self._key_unusable and self._ciphertext_exists():
                raise CredentialDecryptError(
                    "stored credentials cannot be decrypted; "
                    "re-enter credentials")
            return None
        api_key_row = self._store.get_secret(_UPSTOX_PROVIDER, "api_key")
        api_secret_row = self._store.get_secret(_UPSTOX_PROVIDER, "api_secret")
        if api_key_row is None and api_secret_row is None:
            return None
        # Partial rows should not occur (transactional writes); treat as
        # corrupt configuration requiring re-entry.
        if api_key_row is None or api_secret_row is None:
            raise CredentialDecryptError(
                "stored credentials incomplete; re-enter credentials")
        if api_key_row[1] != ENCRYPTION_SCHEME or \
                api_secret_row[1] != ENCRYPTION_SCHEME:
            raise CredentialDecryptError(
                "unsupported encryption scheme; re-enter credentials")
        return {
            "api_key": enc.decrypt(api_key_row[0]),
            "api_secret": enc.decrypt(api_secret_row[0]),
        }

    def status(self) -> dict[str, bool]:
        """Redacted configuration state — booleans only, no decryption."""
        return {
            "api_key_configured":
                self._store.has_secret(_UPSTOX_PROVIDER, "api_key"),
            "api_secret_configured":
                self._store.has_secret(_UPSTOX_PROVIDER, "api_secret"),
        }

    def delete_upstox_app_credentials(self) -> bool:
        n = self._store.delete_provider_secrets(_UPSTOX_PROVIDER)
        return n > 0

    # -- generic multi-provider support ---------------------------------------

    def save_app_credentials(self, provider: str, api_key: str,
                             api_secret: str) -> None:
        """Encrypt and persist credentials for ANY provider."""
        enc = self._get_encryption(allow_generate=True)
        if enc is None:
            raise CredentialDecryptError(
                "master key unavailable; cannot encrypt credentials")
        self._store.upsert_secrets(provider, {
            "api_key": (enc.encrypt(api_key.strip()), ENCRYPTION_SCHEME),
            "api_secret": (enc.encrypt(api_secret.strip()),
                           ENCRYPTION_SCHEME),
        })
        logger.info("%s app credentials saved (encrypted)", provider)

    def load_app_credentials(self, provider: str) -> dict[str, str] | None:
        """Decrypt and return credentials for a provider, or None."""
        enc = self._get_encryption(allow_generate=False)
        if enc is None:
            return None
        key_row = self._store.get_secret(provider, "api_key")
        secret_row = self._store.get_secret(provider, "api_secret")
        if key_row is None or secret_row is None:
            return None
        if key_row[1] != ENCRYPTION_SCHEME or secret_row[1] != ENCRYPTION_SCHEME:
            raise CredentialDecryptError("unsupported encryption scheme")
        return {
            "api_key": enc.decrypt(key_row[0]),
            "api_secret": enc.decrypt(secret_row[0]),
        }

    def delete_app_credentials(self, provider: str) -> bool:
        return self._store.delete_provider_secrets(provider) > 0

    # -- Fyers-specific credential model (single source of truth) ------------
    #
    # Fyers secrets live ONLY here, never in config.json. The source factory
    # and the OAuth routes both read from this store, so there is exactly one
    # authoritative credential record.

    def save_fyers_credentials(self, app_id: str, app_secret: str) -> None:
        """Encrypt + persist Fyers App ID + Secret Key under provider 'fyers'."""
        self.save_app_credentials("fyers", app_id, app_secret)

    def load_fyers_credentials(self) -> dict[str, str] | None:
        """Return {'app_id', 'app_secret'} or None if not configured."""
        creds = self.load_app_credentials("fyers")
        if not creds:
            return None
        return {
            "app_id": creds.get("api_key", ""),
            "app_secret": creds.get("api_secret", ""),
        }

    def save_fyers_refresh_token(self, token: str) -> None:
        """Encrypt + persist the Fyers refresh token (long-lived)."""
        if not isinstance(token, str) or not token.strip():
            raise ValueError("refresh token must be a non-empty string")
        enc = self._get_encryption(allow_generate=True)
        if enc is None:
            raise CredentialDecryptError(
                "master key unavailable; cannot encrypt refresh token")
        self._store.upsert_secrets("fyers", {
            "refresh_token": (enc.encrypt(token.strip()), ENCRYPTION_SCHEME),
        })

    def load_fyers_refresh_token(self) -> str | None:
        """Return the Fyers refresh token, or None.

        Reads the canonical 'fyers'/'refresh_token' row; falls back to the
        legacy 'fyers_refresh' provider layout for backward compatibility.
        """
        enc = self._get_encryption(allow_generate=False)
        if enc is None:
            return None
        row = self._store.get_secret("fyers", "refresh_token")
        if row is not None:
            try:
                return enc.decrypt(row[0])
            except Exception:
                return None
        # Legacy layout: provider 'fyers_refresh', api_secret = refresh token.
        legacy = self._store.get_secret("fyers_refresh", "api_secret")
        if legacy is not None:
            try:
                return enc.decrypt(legacy[0])
            except Exception:
                return None
        return None


def build_default_store() -> "CredentialStore":
    """Build a CredentialStore over the application's existing SQLite DB."""
    from core.persistence.store import EventStore
    db_path = str(DATA_ROOT / "events.db")
    return CredentialStore(EventStore(db_path), data_dir=DATA_ROOT)


# Backwards-compat shim for the pre-SQLite JSON store API used in earlier
# tests/routes: redacted_status(dict) helper retained.
def redacted_status(active: dict[str, str] | None) -> dict[str, Any]:
    return {
        "api_key_configured": bool(active and active.get("api_key")),
        "api_secret_configured": bool(active and active.get("api_secret")),
    }

# Backwards-compat shim for the pre-SQLite JSON store API used in earlier
# tests/routes: redacted_status(dict) helper retained.
def redacted_status(active: dict[str, str] | None) -> dict[str, Any]:
    return {
        "api_key_configured": bool(active and active.get("api_key")),
        "api_secret_configured": bool(active and active.get("api_secret")),
    }

    # -- generic multi-provider support ---------------------------------------

    def save_provider_credentials(
        self, provider: str, api_key: str, api_secret: str,
    ) -> None:
        """Encrypt and persist credentials for ANY provider (generic)."""
        self.save_upstox_app_credentials.__wrapped__ if False else None
        enc = self._get_encryption(allow_generate=True)
        if enc is None:
            raise CredentialDecryptError(
                "master key unavailable; cannot encrypt credentials")
        self._store.upsert_secrets(provider, {
            "api_key": (enc.encrypt(api_key.strip()), ENCRYPTION_SCHEME),
            "api_secret": (enc.encrypt(api_secret.strip()), ENCRYPTION_SCHEME),
        })
        logger.info("%s app credentials saved (encrypted)", provider)

    def load_provider_credentials(
        self, provider: str,
    ) -> dict[str, str] | None:
        enc = self._get_encryption(allow_generate=False)
        if enc is None:
            return None
        key_row = self._store.get_secret(provider, "api_key")
        secret_row = self._store.get_secret(provider, "api_secret")
        if key_row is None or secret_row is None:
            return None
        return {
            "api_key": enc.decrypt(key_row[0]),
            "api_secret": enc.decrypt(secret_row[0]),
        }

    def delete_provider_credential_rows(self, provider: str) -> bool:
        return self._store.delete_provider_secrets(provider) > 0
