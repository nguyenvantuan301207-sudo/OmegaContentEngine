"""Cryptographic Credential Vault for OMEGA-011 Publisher.

Provides authenticated encryption at rest for OAuth tokens, refresh tokens,
and sensitive PKCE verifiers using Fernet (AES-128-CBC + HMAC-SHA256).
Fails closed if the master encryption key is missing or misconfigured.
"""

from __future__ import annotations

import base64
import json
import os
from collections.abc import Mapping

from cryptography.fernet import Fernet, InvalidToken, MultiFernet


class VaultConfigurationError(Exception):
    """Raised when vault encryption keys are missing or invalid."""

    pass


class VaultDecryptionError(Exception):
    """Raised when ciphertext cannot be authenticated or decrypted."""

    pass


class CredentialVaultService:
    """Manages encryption, decryption, and key rotation for secrets at rest."""

    def __init__(
        self,
        master_key: str | None = None,
        keyring: Mapping[int, str] | None = None,
        active_version: int = 1,
    ) -> None:
        self.active_version = active_version
        self._keyring: dict[int, Fernet] = {}

        if keyring:
            for v, key_str in keyring.items():
                self._keyring[v] = self._validate_and_build_fernet(key_str)
        elif master_key:
            self._keyring[self.active_version] = self._validate_and_build_fernet(master_key)
        else:
            # Check environment variables
            env_key = os.getenv("OMEGA_SECRET_ENCRYPTION_KEY")
            env_keyring_json = os.getenv("OMEGA_KEYRING")
            env_active_v = os.getenv("OMEGA_CURRENT_KEY_VERSION")

            if env_keyring_json:
                try:
                    parsed = json.loads(env_keyring_json)
                    for k, val in parsed.items():
                        self._keyring[int(k)] = self._validate_and_build_fernet(str(val))
                except Exception as exc:
                    raise VaultConfigurationError(
                        f"Failed to parse OMEGA_KEYRING JSON: {exc}"
                    ) from exc
                if env_active_v:
                    self.active_version = int(env_active_v)
            elif env_key:
                self._keyring[self.active_version] = self._validate_and_build_fernet(env_key)
            else:
                from omega.config import get_settings

                settings = get_settings()
                if settings.omega_secret_encryption_key:
                    self._keyring[self.active_version] = self._validate_and_build_fernet(
                        settings.omega_secret_encryption_key
                    )
                else:
                    raise VaultConfigurationError(
                        "Credential vault failed closed: OMEGA_SECRET_ENCRYPTION_KEY or OMEGA_KEYRING is required."
                    )

        if self.active_version not in self._keyring:
            raise VaultConfigurationError(
                f"Active key version {self.active_version} not found in configured keyring."
            )

    @staticmethod
    def _validate_and_build_fernet(key_str: str) -> Fernet:
        """Validate key format and instantiate Fernet cipher."""
        try:
            key_bytes = key_str.strip().encode("utf-8")
            decoded = base64.urlsafe_b64decode(key_bytes)
            if len(decoded) != 32:
                raise ValueError("Fernet key must be 32 url-safe base64-encoded bytes.")
            return Fernet(key_bytes)
        except Exception as exc:
            raise VaultConfigurationError(f"Invalid encryption key format: {exc}") from exc

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

    def decrypt(self, ciphertext: str, key_version: int) -> str:
        """Decrypt base64 ciphertext using the exact stored key version."""
        if not ciphertext:
            return ""

        cipher = self._keyring.get(key_version)
        if not cipher:
            # Fall back to trying all keys via MultiFernet
            all_fernet = MultiFernet(list(self._keyring.values()))
            try:
                decrypted_bytes = all_fernet.decrypt(ciphertext.encode("utf-8"))
                return decrypted_bytes.decode("utf-8")
            except InvalidToken as exc:
                raise VaultDecryptionError(
                    f"Decryption failed: key version {key_version} missing and no key in keyring matches."
                ) from exc

        try:
            decrypted_bytes = cipher.decrypt(ciphertext.encode("utf-8"))
            return decrypted_bytes.decode("utf-8")
        except InvalidToken as exc:
            raise VaultDecryptionError(
                "Ciphertext integrity authentication failed or key mismatch."
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
