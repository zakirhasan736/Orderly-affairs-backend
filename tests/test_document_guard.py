from io import BytesIO

import fitz
from PIL import Image

from app.security.document_guard import DocumentGuardError, guard_upload
from app.security.malware_scan import MalwareScanError


def _tiny_jpeg() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (8, 8), color=(32, 80, 120)).save(buf, format="JPEG", quality=80)
    return buf.getvalue()


def _tiny_png() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (8, 8), color=(240, 240, 240)).save(buf, format="PNG")
    return buf.getvalue()


def _one_page_pdf() -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=200, height=200)
    page.insert_text((24, 48), "Account statement")
    data = doc.tobytes()
    doc.close()
    return data


def test_guard_reencodes_jpeg():
    result = guard_upload(_tiny_jpeg(), mime_type="image/jpeg", filename="id.jpg")
    assert result.sanitized is True
    assert result.mime_type == "image/jpeg"
    assert result.payload.startswith(b"\xff\xd8\xff")
    assert result.scan.status == "clean"


def test_guard_reencodes_png():
    result = guard_upload(_tiny_png(), mime_type="image/png", filename="shot.png")
    assert result.sanitized is True
    assert result.payload.startswith(b"\x89PNG")
    assert result.mime_type in {"image/png", "image/jpeg"}


def test_guard_rasterizes_pdf_with_javascript():
    payload = _one_page_pdf().replace(b"%%EOF", b"/JavaScript (app.alert(1))\n%%EOF")
    result = guard_upload(payload, mime_type="application/pdf", filename="stmt.pdf")
    assert result.sanitized is True
    assert result.mime_type == "application/pdf"
    assert result.payload.startswith(b"%PDF")
    lowered = result.payload.lower()
    assert b"/javascript" not in lowered
    assert b"/launch" not in lowered
    assert b"/embeddedfile" not in lowered


def test_guard_blocks_exe():
    try:
        guard_upload(b"MZ" + b"\x00" * 200, mime_type="application/pdf", filename="x.pdf")
        assert False, "expected block"
    except (MalwareScanError, DocumentGuardError):
        pass


def test_guard_blocks_nul_text():
    try:
        guard_upload(b"hello\x00world", mime_type="text/plain", filename="note.txt")
        assert False, "expected block"
    except (MalwareScanError, DocumentGuardError):
        pass
