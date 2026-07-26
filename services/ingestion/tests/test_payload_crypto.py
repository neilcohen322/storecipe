import base64

import pytest

from ingestion.config import Settings
from ingestion.crypto import PayloadCipher, PayloadKeyUnavailableError


def _keyring(**keys: bytes) -> str:
    return ",".join(f"{key_id}={base64.b64encode(key).decode()}" for key_id, key in keys.items())


def test_cipher_round_trips_aes_gcm_with_the_active_key() -> None:
    cipher = PayloadCipher.from_keyring(
        active_key_id="current",
        keyring=_keyring(current=b"c" * 32),
    )

    encrypted = cipher.encrypt(b"recipe input")

    assert encrypted.key_id == "current"
    assert encrypted.algorithm == "AES-256-GCM"
    assert encrypted.nonce != b"recipe input"
    assert encrypted.ciphertext != b"recipe input"
    assert cipher.decrypt(encrypted) == b"recipe input"


def test_rotated_cipher_reads_old_payloads_but_writes_the_new_active_key() -> None:
    old_cipher = PayloadCipher.from_keyring(
        active_key_id="old",
        keyring=_keyring(old=b"o" * 32),
    )
    old_payload = old_cipher.encrypt(b"retained checkpoint")
    rotated_cipher = PayloadCipher.from_keyring(
        active_key_id="new",
        keyring=_keyring(old=b"o" * 32, new=b"n" * 32),
    )

    assert rotated_cipher.decrypt(old_payload) == b"retained checkpoint"
    assert rotated_cipher.encrypt(b"new checkpoint").key_id == "new"


def test_cipher_fails_closed_for_missing_or_referenced_retired_keys() -> None:
    cipher = PayloadCipher.from_keyring(
        active_key_id="current",
        keyring=_keyring(old=b"o" * 32, current=b"c" * 32),
    )
    payload = PayloadCipher.from_keyring(
        active_key_id="old",
        keyring=_keyring(old=b"o" * 32),
    ).encrypt(b"retained checkpoint")

    with pytest.raises(PayloadKeyUnavailableError, match="old"):
        PayloadCipher.from_keyring(
            active_key_id="current",
            keyring=_keyring(current=b"c" * 32),
        ).decrypt(payload)
    with pytest.raises(ValueError, match="referenced"):
        cipher.retire_key("old", referenced_key_ids={"old"})


def test_settings_reject_invalid_payload_key_material() -> None:
    with pytest.raises(ValueError, match="32 bytes"):
        Settings(
            payload_active_key_id="current",
            payload_keyring=_keyring(current=b"too-short"),
        )
