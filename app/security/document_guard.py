"""
Content Disarm & Reconstruction (CDR) for vault / AI document uploads.

The original bytes are never stored. Images are re-encoded (metadata stripped).
PDFs are rasterized into a new image-only PDF (no JavaScript, attachments, or
launch actions). The rebuilt file is scanned again before it is written.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from app.security.malware_scan import (
    MalwareScanError,
    MalwareScanResult,
    scan_upload_bytes,
    sniff_payload_kind,
)


class DocumentGuardError(MalwareScanError):
    """Upload failed closed at the document guard."""


@dataclass(frozen=True)
class DocumentGuardResult:
    payload: bytes
    mime_type: str
    scan: MalwareScanResult
    sanitized: bool
    original_kind: str


def _settings():
    try:
        from app.config import settings

        return settings
    except Exception:
        return None


def _sanitize_enabled() -> bool:
    cfg = _settings()
    if cfg is None:
        return True
    return bool(getattr(cfg, "DOCUMENT_GUARD_SANITIZE", True))


def _max_pdf_pages() -> int:
    cfg = _settings()
    if cfg is None:
        return 50
    return max(1, int(getattr(cfg, "DOCUMENT_GUARD_MAX_PDF_PAGES", 50) or 50))


def _pdf_scale() -> float:
    cfg = _settings()
    if cfg is None:
        return 1.5
    try:
        return max(0.5, min(2.5, float(getattr(cfg, "DOCUMENT_GUARD_PDF_SCALE", 1.5))))
    except (TypeError, ValueError):
        return 1.5


def _max_pixels() -> int:
    cfg = _settings()
    if cfg is None:
        return 40_000_000
    return max(1_000_000, int(getattr(cfg, "DOCUMENT_GUARD_MAX_PIXELS", 40_000_000) or 40_000_000))


def _reject(message: str) -> None:
    raise DocumentGuardError(message)


def _sanitize_image(payload: bytes, kind: str) -> tuple[bytes, str]:
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = _max_pixels()
    try:
        with Image.open(io.BytesIO(payload)) as src:
            src.load()
            has_alpha = src.mode in {"RGBA", "LA"} or (
                src.mode == "P" and "transparency" in src.info
            )
            if kind == "png" or has_alpha:
                converted = src.convert("RGBA" if has_alpha else "RGB")
                out = io.BytesIO()
                converted.save(out, format="PNG", optimize=True)
                return out.getvalue(), "image/png"
            converted = src.convert("RGB")
            out = io.BytesIO()
            converted.save(out, format="JPEG", quality=88, optimize=True)
            return out.getvalue(), "image/jpeg"
    except Image.DecompressionBombError:
        _reject("This image is too large to scan safely and was blocked.")
    except Exception:
        _reject("This image could not be rebuilt safely and was blocked.")
    raise DocumentGuardError("This image could not be rebuilt safely and was blocked.")


def _sanitize_pdf(payload: bytes) -> bytes:
    import fitz

    try:
        src = fitz.open(stream=payload, filetype="pdf")
    except Exception:
        _reject("This PDF could not be opened and was blocked.")

    try:
        if src.needs_pass:
            try:
                unlocked = bool(src.authenticate(""))
            except Exception:
                unlocked = False
            if not unlocked:
                _reject(
                    "Password-protected PDFs cannot be scanned safely. "
                    "Remove the password and upload again."
                )

        page_count = src.page_count
        if page_count < 1:
            _reject("This PDF has no pages and was blocked.")
        if page_count > _max_pdf_pages():
            _reject(
                f"This PDF has too many pages (max {_max_pdf_pages()}) "
                "and was blocked."
            )

        scale = _pdf_scale()
        max_pixels = _max_pixels()
        out = fitz.open()
        try:
            for index in range(page_count):
                page = src[index]
                width = abs(float(page.rect.width))
                height = abs(float(page.rect.height))
                pixels = width * height * scale * scale
                page_scale = scale
                if pixels > max_pixels and width > 0 and height > 0:
                    page_scale = max(0.4, (max_pixels / (width * height)) ** 0.5)

                pix = page.get_pixmap(matrix=fitz.Matrix(page_scale, page_scale), alpha=False)
                if pix.width * pix.height > max_pixels:
                    _reject("This PDF page is too large to scan safely and was blocked.")

                image_bytes = pix.tobytes("jpeg")
                new_page = out.new_page(width=pix.width, height=pix.height)
                new_page.insert_image(new_page.rect, stream=image_bytes)

            cleaned = out.tobytes(deflate=True, garbage=4)
        finally:
            out.close()
    finally:
        src.close()

    if not cleaned.startswith(b"%PDF"):
        _reject("This PDF could not be rebuilt safely and was blocked.")
    return cleaned


def _sanitize_text(payload: bytes) -> bytes:
    if b"\x00" in payload:
        _reject("This text file contains binary data and was blocked.")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        text = payload.decode("latin-1")
    cleaned = text.replace("\x00", "")
    return cleaned.encode("utf-8")


def _rebuild(payload: bytes, kind: str) -> tuple[bytes, str]:
    if kind == "heic":
        _reject(
            "iPhone HEIC photos are not supported. Save or export as JPG or PDF, then upload again."
        )
    if kind in {"png", "jpeg", "webp"}:
        return _sanitize_image(payload, kind)
    if kind == "pdf":
        return _sanitize_pdf(payload), "application/pdf"
    if kind == "text":
        return _sanitize_text(payload), "text/plain"
    _reject("Unsupported file type.")
    raise DocumentGuardError("Unsupported file type.")


def guard_upload(
    payload: bytes,
    *,
    mime_type: str | None,
    filename: str | None = None,
) -> DocumentGuardResult:
    """
    Scan, optionally rebuild, re-scan. Raise DocumentGuardError / MalwareScanError
    if the file must not be stored. Return only cleaned bytes when sanitizing.
    """
    kind = sniff_payload_kind(payload)
    sanitize = _sanitize_enabled()

    pre = scan_upload_bytes(
        payload,
        mime_type=mime_type,
        filename=filename,
        check_pdf_active_content=not sanitize,
    )

    if not sanitize:
        return DocumentGuardResult(
            payload=payload,
            mime_type=(mime_type or "").split(";")[0].strip().lower() or "application/octet-stream",
            scan=pre,
            sanitized=False,
            original_kind=kind,
        )

    try:
        cleaned, out_mime = _rebuild(payload, kind)
    except DocumentGuardError:
        raise
    except Exception:
        _reject("This document could not be rebuilt safely and was blocked.")

    if not cleaned:
        _reject("This document could not be rebuilt safely and was blocked.")

    post = scan_upload_bytes(
        cleaned,
        mime_type=out_mime,
        filename=filename,
        check_pdf_active_content=True,
    )
    engine = post.engine if post.engine == "clamav" else pre.engine
    if post.engine == "clamav" or pre.engine == "clamav":
        engine = "clamav"
    detail = "cdr"
    if post.detail:
        detail = f"cdr,{post.detail}"
    scan = MalwareScanResult(status="clean", engine=engine, detail=detail)
    return DocumentGuardResult(
        payload=cleaned,
        mime_type=out_mime,
        scan=scan,
        sanitized=True,
        original_kind=kind,
    )
