import os
import json
import base64
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

AES_256_KEY = os.getenv("AES_256_KEY")
if not AES_256_KEY:
    raise RuntimeError("AES_256_KEY not set")

KEY = base64.b64decode(AES_256_KEY)
if len(KEY) != 32:
    raise RuntimeError("AES_256_KEY must be 32 bytes")

aesgcm = AESGCM(KEY)

# v1: base64(nonce + ciphertext) — legacy section/message payloads
# v2: base64(0x02 + nonce + ciphertext) with optional AAD context binding
_V2_PREFIX = b"\x02"


def _aad_bytes(context: str | None) -> bytes | None:
    if not context:
        return None
    return context.encode("utf-8")


def encrypt_data(data: dict, context: str | None = None) -> str:
    """
    Encrypt dict using AES-256-GCM.
    Returns a base64 string containing nonce + authenticated ciphertext.
    When context is provided, the payload is bound to that record scope.
    """
    plaintext = json.dumps(data, sort_keys=True, default=str).encode("utf-8")
    nonce = os.urandom(12)
    aad = _aad_bytes(context)

    if aad is not None:
        ciphertext = aesgcm.encrypt(nonce, plaintext, aad)
        return base64.b64encode(_V2_PREFIX + nonce + ciphertext).decode("ascii")

    ciphertext = aesgcm.encrypt(nonce, plaintext, None)
    return base64.b64encode(nonce + ciphertext).decode("ascii")


def decrypt_data(token: str, context: str | None = None) -> dict:
    """
    Decrypt AES-256-GCM payload.
    Supports legacy v1 blobs and v2 context-bound blobs.
    """
    if not token:
        return {}

    raw = base64.b64decode(token)
    if len(raw) < 13:
        raise ValueError("Invalid encrypted payload")

    if raw[0:1] == _V2_PREFIX:
        nonce = raw[1:13]
        ciphertext = raw[13:]
        aad = _aad_bytes(context)
        plaintext = aesgcm.decrypt(nonce, ciphertext, aad)
    else:
        nonce = raw[:12]
        ciphertext = raw[12:]
        plaintext = aesgcm.decrypt(nonce, ciphertext, None)

    return json.loads(plaintext.decode("utf-8"))


def is_encrypted_payload(value: str | None) -> bool:
    if not value:
        return False
    try:
        raw = base64.b64decode(value)
        return len(raw) >= 13
    except Exception:
        return False
