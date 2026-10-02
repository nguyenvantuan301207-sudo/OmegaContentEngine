"""Credential encryption and the authoritative secret-file keyring loader.

Keyring resolution belongs here, outside Settings. Only the established legacy
Settings secret field is consulted for backwards-compatible .env single-key use.
"""

from __future__ import annotations

import base64
import json
import os
import stat
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

DEFAULT_SECRET_KEYRING_FILE = "/run/secrets/omega_keyring.json"
MAX_KEYRING_FILE_SIZE = 64 * 1024


@dataclass(frozen=True)
class KeyringLoadResult:
    """Loaded ciphers with safe source metadata; cipher objects excluded from repr."""

    keyring: dict[int, Fernet] = field(repr=False)
    active_version: int
    source: str
    source_path: str | None
    versions: list[int]


class VaultConfigurationError(Exception):
    """Missing or invalid encryption configuration; messages contain no inputs."""


class VaultDecryptionError(Exception):
    """Ciphertext cannot be authenticated or decrypted."""


def validate_and_build_fernet(key_str: str) -> Fernet:
    if not isinstance(key_str, str) or not key_str.strip():
        raise VaultConfigurationError("Encryption key must be a non-empty string.")
    try:
        key_bytes = key_str.strip().encode("ascii")
        if len(base64.b64decode(key_bytes, altchars=b"-_", validate=True)) != 32:
            raise ValueError
        return Fernet(key_bytes)
    except (ValueError, TypeError, UnicodeError):
        raise VaultConfigurationError("Invalid encryption key format.") from None


def _normalize_version(value: object, *, active: bool = False) -> int:
    label = "Active key version" if active else "Key version"
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise VaultConfigurationError(f"{label} must be an integer.")
    try:
        version = int(value)
    except (ValueError, TypeError):
        raise VaultConfigurationError(f"{label} must be an integer.") from None
    if version <= 0:
        raise VaultConfigurationError(f"{label} must be a positive integer.")
    return version


def _build_keyring(items: Iterable[tuple[object, str]]) -> dict[int, Fernet]:
    keyring: dict[int, Fernet] = {}
    for raw_version, value in items:
        version = _normalize_version(raw_version)
        if version in keyring:
            raise VaultConfigurationError("Duplicate normalized key version detected.")
        keyring[version] = validate_and_build_fernet(value)
    if not keyring:
        raise VaultConfigurationError("Keyring cannot be empty.")
    return keyring


def _unique_json_object(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise VaultConfigurationError("Duplicate normalized key version detected.")
        result[key] = value
    return result


def _parse_keyring_json(content: str) -> dict[int, Fernet]:
    try:
        parsed = json.loads(content, object_pairs_hook=_unique_json_object)
    except (ValueError, RecursionError):
        raise VaultConfigurationError("Failed to parse keyring JSON.") from None
    if not isinstance(parsed, dict):
        raise VaultConfigurationError("Keyring must be a JSON object.")
    return _build_keyring(parsed.items())


def _read_and_validate_keyring_file(file_path: str | Path) -> str:
    # Check the opened descriptor too, preventing stat/open substitution and
    # bounding the actual read even if a file grows after the size check.
    try:
        initial = os.stat(file_path)
        if not stat.S_ISREG(initial.st_mode):
            raise VaultConfigurationError("Keyring secret path is not a regular file.")
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
        descriptor = os.open(file_path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode):
                raise VaultConfigurationError("Keyring secret path is not a regular file.")
            if os.name == "posix" and opened.st_mode & stat.S_IWOTH:
                raise VaultConfigurationError("Keyring secret file is world-writable.")
            if opened.st_size > MAX_KEYRING_FILE_SIZE:
                raise VaultConfigurationError("Keyring secret file exceeds maximum allowed size.")
            content = stream.read(MAX_KEYRING_FILE_SIZE + 1)
    except FileNotFoundError:
        raise VaultConfigurationError("Keyring secret file does not exist.") from None
    except (OSError, ValueError):
        raise VaultConfigurationError("Failed to read keyring secret file.") from None
    if len(content) > MAX_KEYRING_FILE_SIZE:
        raise VaultConfigurationError("Keyring secret file exceeds maximum allowed size.")
    if not content.strip():
        raise VaultConfigurationError("Keyring secret file is empty.")
    try:
        return content.decode("utf-8")
    except UnicodeError:
        raise VaultConfigurationError("Keyring secret file is not valid UTF-8.") from None


def _default_file_present(path: str | None) -> bool:
    if path is None:
        return False
    try:
        os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError:
        raise VaultConfigurationError("Cannot inspect default keyring secret file.") from None
    return True


def _validate_active_version(keyring: Mapping[int, Fernet], active_version: int) -> None:
    if active_version not in keyring:
        raise VaultConfigurationError(f"Active key version {active_version} not found in keyring.")


def load_keyring(
    keyring_file: str | None = None,
    default_secret_file: str | None = DEFAULT_SECRET_KEYRING_FILE,
    env_keyring_json: str | None = None,
    legacy_env_key: str | None = None,
    active_version: int | None = None,
) -> KeyringLoadResult:
    """Resolve explicit file > existing default file > env JSON > legacy key.

    A configured source is authoritative even when empty or invalid. Only an
    absent default file allows fallback. Docker-readable 0444/0440 mounts work;
    POSIX world-writable files fail closed. No source content appears in errors.
    """
    active = _normalize_version(
        active_version
        if active_version is not None
        else os.environ.get("OMEGA_CURRENT_KEY_VERSION", "1"),
        active=True,
    )
    explicit = keyring_file if keyring_file is not None else os.environ.get("OMEGA_KEYRING_FILE")
    source_path = None
    if explicit is not None:
        if not explicit.strip():
            raise VaultConfigurationError("Explicit keyring secret file path is empty.")
        source, source_path = "EXPLICIT_FILE", str(explicit)
        keyring = _parse_keyring_json(_read_and_validate_keyring_file(explicit))
    elif _default_file_present(default_secret_file):
        source, source_path = "DEFAULT_FILE", str(default_secret_file)
        keyring = _parse_keyring_json(_read_and_validate_keyring_file(default_secret_file))
    else:
        env_json = (
            env_keyring_json if env_keyring_json is not None else os.environ.get("OMEGA_KEYRING")
        )
        if env_json is not None:
            source = "ENV_KEYRING"
            keyring = _parse_keyring_json(env_json)
        else:
            legacy = (
                legacy_env_key
                if legacy_env_key is not None
                else os.environ.get("OMEGA_SECRET_ENCRYPTION_KEY")
            )
            if legacy is None:
                from omega.config import get_settings

                try:
                    legacy = get_settings().omega_secret_encryption_key
                except Exception:
                    raise VaultConfigurationError(
                        "Legacy vault configuration could not be loaded."
                    ) from None
            if legacy is None:
                raise VaultConfigurationError(
                    "Credential vault failed closed: OMEGA_KEYRING_FILE, OMEGA_KEYRING, "
                    "or OMEGA_SECRET_ENCRYPTION_KEY is required."
                )
            source = "LEGACY_KEY"
            keyring = _build_keyring([(active, legacy)])
    _validate_active_version(keyring, active)
    return KeyringLoadResult(keyring, active, source, source_path, sorted(keyring))


class CredentialVaultService:
    """Manages encryption, decryption, and key rotation for secrets at rest."""

    def __init__(
        self,
        master_key: str | None = None,
        keyring: Mapping[int, str] | None = None,
        active_version: int | None = None,
        keyring_file: str | None = None,
    ) -> None:
        self._keyring: dict[int, Fernet] = {}
        self.keyring_source: str = "IN_MEMORY"
        self.keyring_file_path: str | None = None

        if keyring is not None or master_key is not None:
            self.active_version = _normalize_version(
                1 if active_version is None else active_version, active=True
            )
            values = keyring if keyring is not None else {self.active_version: master_key}
            self._keyring = _build_keyring(values.items())
            _validate_active_version(self._keyring, self.active_version)
        else:
            load_res = load_keyring(keyring_file=keyring_file, active_version=active_version)
            self._keyring = load_res.keyring
            self.active_version = load_res.active_version
            self.keyring_source = load_res.source
            self.keyring_file_path = load_res.source_path

    # Compatibility alias; validation has one implementation.
    _validate_and_build_fernet = staticmethod(validate_and_build_fernet)

    def encrypt(self, plaintext: str, key_version: int | None = None) -> tuple[str, int]:
        """Encrypt plaintext string into base64 ciphertext with active or specified key version."""
        if not plaintext:
            return "", self.active_version

        v = key_version or self.active_version
        cipher = self._keyring.get(v)
        if not cipher:
            raise VaultConfigurationError(f"Encryption key version {v} not configured in vault.")

        raw_ciphertext = cipher.encrypt(plaintext.encode("utf-8"))
        return raw_ciphertext.decode("utf-8"), v

    def decrypt(self, ciphertext: str, key_version: int | None = None) -> str:
        """Decrypt base64 ciphertext using the specified key version or keyring fallback.

        If key_version is provided and present in the keyring, that key is tried first.
        If key_version is None or fails authentication, decrypt falls back to trying all
        keys in the configured keyring via MultiFernet, supporting seamless transitions.
        """
        if not ciphertext:
            return ""

        if key_version is not None:
            cipher = self._keyring.get(key_version)
            if cipher is not None:
                try:
                    decrypted_bytes = cipher.decrypt(ciphertext.encode("utf-8"))
                    return decrypted_bytes.decode("utf-8")
                except InvalidToken:
                    pass  # Fall through to MultiFernet across all configured keyring keys

        # Multi-key read fallback across all configured keys in the keyring
        all_fernet = MultiFernet(list(self._keyring.values()))
        try:
            decrypted_bytes = all_fernet.decrypt(ciphertext.encode("utf-8"))
            return decrypted_bytes.decode("utf-8")
        except InvalidToken as exc:
            v_desc = f"key version {key_version}" if key_version is not None else "any keyring key"
            raise VaultDecryptionError(
                f"Decryption failed: {v_desc} could not authenticate ciphertext."
            ) from exc

    def rotate_ciphertext(self, ciphertext: str, current_version: int) -> tuple[str, int]:
        """Decrypt with current key version and re-encrypt with active version."""
        if current_version == self.active_version:
            return ciphertext, current_version

        plaintext = self.decrypt(ciphertext, current_version)
        return self.encrypt(plaintext, self.active_version)


_default_vault: CredentialVaultService | None = None


def get_credential_vault() -> CredentialVaultService:
    """Global singleton provider for credential vault."""
    global _default_vault
    if _default_vault is None:
        _default_vault = CredentialVaultService()
    return _default_vault
