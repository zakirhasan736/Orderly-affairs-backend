"""Ping clamd and prove clean vs EICAR through the upload scanner."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from app.security.malware_scan import (  # noqa: E402
    MalwareScanError,
    describe_clamd_status,
    ping_clamd,
    scan_upload_bytes,
)

EICAR = (
    b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
)


def main() -> int:
    print(describe_clamd_status())
    if not ping_clamd():
        print("FAIL: clamd is not answering PONG on CLAMD_HOST:CLAMD_PORT")
        return 1

    jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 128
    clean = scan_upload_bytes(jpeg, mime_type="image/jpeg", filename="ok.jpg")
    print(f"clean jpeg: status={clean.status} engine={clean.engine}")
    if clean.engine != "clamav" or clean.status != "clean":
        print("FAIL: expected engine=clamav for a clean JPEG")
        return 1

    blocked = False
    try:
        scan_upload_bytes(EICAR, mime_type="text/plain", filename="eicar.txt")
    except MalwareScanError:
        blocked = True
    if not blocked:
        print("FAIL: EICAR test file was not blocked")
        return 1
    print("EICAR: blocked")
    print("OK: ClamAV is scanning vault uploads")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
