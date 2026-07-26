"""AES-256-GCM envelope encryption for protected import payloads."""

from __future__ import annotations

import base64
import binascii
import os
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

if TYPE_CHECKING:
    from ingestion.config import Settings


AES_GCM_ALGORITHM = "AES-256-GCM"
AES_GCM_NONCE_BYTES = 12
AES_256_KEY_BYTES = 32


class PayloadKeyUnavailableError(RuntimeError):
    """A retained payload references a key that is no longer configured."""


@dataclass(frozen=True, slots=True)
class EncryptedPayload:
    key_id: str
    algorithm: str
    nonce: bytes
    ciphertext: bytes


def parse_keyring(value: str) -> dict[str, bytes]:
    """Parse ``key-id=base64-key`` pairs and reject malformed AES-256 keys."""

    keyring: dict[str, bytes] = {}
    if not value.strip():
        return keyring
    for pair in value.split(","):
        key_id, separator, encoded_key = pair.strip().partition("=")
        if not separator or not key_id or not encoded_key:
            raise ValueError("payload keyring entries must use key-id=base64-key")
        if key_id in keyring:
            raise ValueError(f"payload keyring has duplicate key id: {key_id}")
        try:
            key = base64.b64decode(encoded_key, validate=True)
        except binascii.Error as error:
            raise ValueError(f"payload key {key_id!r} is not valid base64") from error
        if len(key) != AES_256_KEY_BYTES:
            raise ValueError(f"payload key {key_id!r} must be exactly 32 bytes")
        keyring[key_id] = key
    return keyring


class PayloadCipher:
    """Encrypt new payloads with the active key and decrypt retained old payloads."""

    def __init__(self, *, active_key_id: str, keys: Mapping[str, bytes]) -> None:
        self._active_key_id = active_key_id
        self._keys = dict(keys)
        if not active_key_id:
            raise ValueError("payload active key id is required")
        if active_key_id not in self._keys:
            raise PayloadKeyUnavailableError(f"active payload key is unavailable: {active_key_id}")
        for key_id, key in self._keys.items():
            if len(key) != AES_256_KEY_BYTES:
                raise ValueError(f"payload key {key_id!r} must be exactly 32 bytes")

    @classmethod
    def from_keyring(cls, *, active_key_id: str, keyring: str) -> PayloadCipher:
        return cls(active_key_id=active_key_id, keys=parse_keyring(keyring))

    @classmethod
    def from_settings(cls, settings: Settings) -> PayloadCipher:
        return cls.from_keyring(
            active_key_id=settings.payload_active_key_id,
            keyring=settings.payload_keyring.get_secret_value(),
        )

    @property
    def active_key_id(self) -> str:
        return self._active_key_id

    def encrypt(self, plaintext: bytes) -> EncryptedPayload:
        nonce = os.urandom(AES_GCM_NONCE_BYTES)
        ciphertext = AESGCM(self._keys[self._active_key_id]).encrypt(nonce, plaintext, None)
        return EncryptedPayload(
            key_id=self._active_key_id,
            algorithm=AES_GCM_ALGORITHM,
            nonce=nonce,
            ciphertext=ciphertext,
        )

    def decrypt(self, payload: EncryptedPayload) -> bytes:
        if payload.algorithm != AES_GCM_ALGORITHM:
            raise ValueError(f"unsupported payload encryption algorithm: {payload.algorithm}")
        if len(payload.nonce) != AES_GCM_NONCE_BYTES:
            raise ValueError("AES-GCM payload nonce must be 12 bytes")
        try:
            key = self._keys[payload.key_id]
        except KeyError as error:
            raise PayloadKeyUnavailableError(
                f"payload encryption key is unavailable: {payload.key_id}"
            ) from error
        return AESGCM(key).decrypt(payload.nonce, payload.ciphertext, None)

    def require_keys_available(self, key_ids: Collection[str]) -> None:
        missing = sorted(set(key_ids).difference(self._keys))
        if missing:
            raise PayloadKeyUnavailableError(
                f"retained payload encryption keys are unavailable: {', '.join(missing)}"
            )

    def retire_key(self, key_id: str, *, referenced_key_ids: Collection[str] = ()) -> PayloadCipher:
        if key_id == self._active_key_id:
            raise ValueError("cannot retire the active payload key")
        if key_id in referenced_key_ids:
            raise ValueError(f"cannot retire referenced payload key: {key_id}")
        keys = dict(self._keys)
        keys.pop(key_id, None)
        return PayloadCipher(active_key_id=self._active_key_id, keys=keys)
