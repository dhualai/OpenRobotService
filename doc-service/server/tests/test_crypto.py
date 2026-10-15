from __future__ import annotations

import pytest

from app.infra.crypto import ContentCrypto, CryptoError, decode_kek


def test_crypto_roundtrip():
    c = ContentCrypto("local-dev-key-material-please-32")
    blob = c.encrypt("hello 拓扑", aad="document:usp:biz:v1.0")
    assert c.decrypt_text(blob, aad="document:usp:biz:v1.0") == "hello 拓扑"


def test_crypto_wrong_key():
    a = ContentCrypto("test-kek-alpha-material-32bytes!!")
    b = ContentCrypto("test-kek-bravo-material-32bytes!!")
    blob = a.encrypt("plain", aad="x")
    with pytest.raises(CryptoError):
        b.decrypt(blob, aad="x")


def test_crypto_aad_mismatch():
    c = ContentCrypto("test-kek-charlie-material-32bytes!")
    blob = c.encrypt("plain", aad="document:a")
    with pytest.raises(CryptoError):
        c.decrypt(blob, aad="document:b")


def test_blob_has_no_plaintext():
    c = ContentCrypto("test-kek-delta-material-32bytes!!!")
    text = "UNIQUE_PLAINTEXT_MARKER_XYZ"
    blob = c.encrypt(text, aad="m")
    assert not c.blob_contains_plaintext(blob, text)
    assert blob.startswith(b"TP1")


def test_decode_kek_rejects_empty():
    with pytest.raises(CryptoError):
        decode_kek("")
