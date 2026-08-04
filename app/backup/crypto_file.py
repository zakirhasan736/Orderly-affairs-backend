"""AES-256-GCM chunked file encryption for backup packages."""

from __future__ import annotations

import base64
import os
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import settings

MAGIC = b"OA1B"
VERSION = 1
CHUNK_SIZE = 4 * 1024 * 1024  # 4 MiB plaintext per chunk
AAD = b"orderly-affairs-backup-v1"


def resolve_backup_key() -> bytes:
    """Prefer BACKUP_ENCRYPTION_KEY; otherwise reuse AES_256_KEY."""
    from dotenv import load_dotenv

    load_dotenv()
    raw = (settings.BACKUP_ENCRYPTION_KEY or "").strip()
    if not raw:
        raw = (os.getenv("AES_256_KEY") or "").strip()
    if not raw:
        raise RuntimeError(
            "BACKUP_ENCRYPTION_KEY or AES_256_KEY required to encrypt backups"
        )
    key = base64.b64decode(raw)
    if len(key) != 32:
        raise RuntimeError("Backup encryption key must decode to 32 bytes")
    return key


def encrypt_file(src: Path, dest: Path, key: bytes | None = None) -> None:
    """Encrypt src → dest (OA1B chunked AES-GCM)."""
    key = key or resolve_backup_key()
    aesgcm = AESGCM(key)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with src.open("rb") as fin, dest.open("wb") as fout:
        fout.write(MAGIC)
        fout.write(bytes([VERSION]))
        while True:
            chunk = fin.read(CHUNK_SIZE)
            if not chunk:
                break
            nonce = os.urandom(12)
            ciphertext = aesgcm.encrypt(nonce, chunk, AAD)
            fout.write(nonce)
            fout.write(len(ciphertext).to_bytes(4, "big"))
            fout.write(ciphertext)


def decrypt_file(src: Path, dest: Path, key: bytes | None = None) -> None:
    """Decrypt OA1B package → dest (for restore drills)."""
    key = key or resolve_backup_key()
    aesgcm = AESGCM(key)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with src.open("rb") as fin, dest.open("wb") as fout:
        magic = fin.read(4)
        if magic != MAGIC:
            raise ValueError("Not an Orderly Affairs backup package (bad magic)")
        version = fin.read(1)
        if not version or version[0] != VERSION:
            raise ValueError(f"Unsupported backup version: {version!r}")
        while True:
            nonce = fin.read(12)
            if not nonce:
                break
            if len(nonce) != 12:
                raise ValueError("Truncated backup (nonce)")
            len_bytes = fin.read(4)
            if len(len_bytes) != 4:
                raise ValueError("Truncated backup (length)")
            ct_len = int.from_bytes(len_bytes, "big")
            ciphertext = fin.read(ct_len)
            if len(ciphertext) != ct_len:
                raise ValueError("Truncated backup (ciphertext)")
            fout.write(aesgcm.decrypt(nonce, ciphertext, AAD))
