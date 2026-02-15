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


def encrypt_data(data: dict) -> str:
    """
    Encrypt dict using AES-256-GCM
    Returns base64 string containing nonce + ciphertext
    """
    plaintext = json.dumps(data).encode()
    nonce = os.urandom(12)  # 96-bit nonce (recommended)
    ciphertext = aesgcm.encrypt(nonce, plaintext, None)

    # store nonce + ciphertext together
    return base64.b64encode(nonce + ciphertext).decode()


def decrypt_data(token: str) -> dict:
    """
    Decrypt AES-256-GCM payload
    """
    raw = base64.b64decode(token)
    nonce = raw[:12]
    ciphertext = raw[12:]

    plaintext = aesgcm.decrypt(nonce, ciphertext, None)
    return json.loads(plaintext.decode())
