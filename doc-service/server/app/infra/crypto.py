"""Envelope encryption for document bodies (AES-256-GCM).

Blob layout (version 1):
  b"TP1" | wrap_nonce(12) | wrapped_dek(48) | body_nonce(12) | ciphertext+tag
KEK never stored in the blob. AAD binds ciphertext to a resource id.
"""
from __future__ import annotations

import base64
import hashlib
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"TP1"
NONCE_LEN = 12
DEK_LEN = 32
TAG_LEN = 16
WRAPPED_DEK_LEN = DEK_LEN + TAG_LEN


class CryptoError(Exception):
    pass


def decode_kek(raw: str) -> bytes:
    s = (raw or "").strip()
    if not s:
        raise CryptoError("empty content key")
    for decoder in (
        lambda x: base64.urlsafe_b64decode(x + "=" * (-len(x) % 4)),
        lambda x: bytes.fromhex(x),
        lambda x: x.encode("utf-8"),
    ):
        try:
            key = decoder(s)
        except Exception:
            continue
        if len(key) >= 32:
            return hashlib.sha256(key).digest()
    raise CryptoError("content key too short")


class ContentCrypto:
    def __init__(self, kek_material: str):
        self._kek = decode_kek(kek_material)

    def encrypt(self, plaintext: str | bytes, *, aad: str) -> bytes:
        data = plaintext.encode("utf-8") if isinstance(plaintext, str) else plaintext
        bound = aad.encode("utf-8")
        dek = os.urandom(DEK_LEN)
        body_nonce = os.urandom(NONCE_LEN)
        ct = AESGCM(dek).encrypt(body_nonce, data, bound)
        wrap_nonce = os.urandom(NONCE_LEN)
        wrapped = AESGCM(self._kek).encrypt(wrap_nonce, dek, b"dek|" + bound)
        return MAGIC + wrap_nonce + wrapped + body_nonce + ct

    def decrypt(self, blob: bytes, *, aad: str) -> bytes:
        if not blob or not blob.startswith(MAGIC):
            raise CryptoError("unknown blob")
        bound = aad.encode("utf-8")
        p = len(MAGIC)
        wrap_nonce = blob[p : p + NONCE_LEN]
        p += NONCE_LEN
        wrapped = blob[p : p + WRAPPED_DEK_LEN]
        p += WRAPPED_DEK_LEN
        body_nonce = blob[p : p + NONCE_LEN]
        p += NONCE_LEN
        ct = blob[p:]
        try:
            dek = AESGCM(self._kek).decrypt(wrap_nonce, wrapped, b"dek|" + bound)
            return AESGCM(dek).decrypt(body_nonce, ct, bound)
        except InvalidTag as e:
            raise CryptoError("decrypt failed") from e

    def decrypt_text(self, blob: bytes, *, aad: str) -> str:
        return self.decrypt(blob, aad=aad).decode("utf-8")

    @staticmethod
    def content_hash(plaintext: str | bytes) -> str:
        data = plaintext.encode("utf-8") if isinstance(plaintext, str) else plaintext
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def blob_contains_plaintext(blob: bytes, plaintext: str) -> bool:
        needle = plaintext.encode("utf-8")
        return needle in blob if needle else False
