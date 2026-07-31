# app/ai/local_document_extract.py
"""
Local-first document text extraction.

Pipeline:
  TXT → decode (UTF-8 / UTF-16 / latin-1)
  PDF → embedded text (pypdf); if weak → OCR pages (PyMuPDF + Tesseract)
  Image → OCR (Tesseract with preprocessing)
  Quality gate → if weak/empty, GPT vision multimodal fallback (images/PDF pages)
"""

from __future__ import annotations

import base64
import io
import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_MIN_CHARS = 80
MAX_OCR_PDF_PAGES = 8
MAX_VISION_PDF_PAGES = 3
MAX_VISION_IMAGE_BYTES = 4_500_000


def prefer_local_text_extract() -> bool:
    raw = os.getenv("AI_PREFER_LOCAL_TEXT_EXTRACT", "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def vision_fallback_enabled() -> bool:
    raw = os.getenv("AI_ALLOW_VISION_FALLBACK", "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def local_text_min_chars() -> int:
    try:
        return max(1, int(os.getenv("AI_LOCAL_TEXT_MIN_CHARS", str(DEFAULT_MIN_CHARS))))
    except (TypeError, ValueError):
        return DEFAULT_MIN_CHARS


def text_result_min_confidence() -> float:
    try:
        return max(
            0.0,
            min(1.0, float(os.getenv("AI_TEXT_RESULT_MIN_CONFIDENCE", "0.45"))),
        )
    except (TypeError, ValueError):
        return 0.45


def _configure_tesseract() -> bool:
    """Return True when pytesseract + tesseract binary look usable."""
    try:
        import pytesseract
    except ImportError:
        return False

    cmd = (os.getenv("TESSERACT_CMD") or "").strip()
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd

    try:
        pytesseract.get_tesseract_version()
        return True
    except Exception as error:
        logger.info("Tesseract OCR unavailable: %s", repr(error))
        return False


def _score_text_quality(text: str, *, min_chars: int) -> tuple[float, bool]:
    """
    Returns (quality_score 0..1, needs_vision).
    needs_vision when text is too short or mostly non-linguistic garbage.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return 0.0, True

    length = len(cleaned)
    if length < min_chars:
        return min(0.4, length / float(min_chars)), True

    alnum = sum(1 for ch in cleaned if ch.isalnum() or ch.isspace())
    alnum_ratio = alnum / float(length) if length else 0.0
    words = re.findall(r"[A-Za-z0-9]{2,}", cleaned)
    word_density = len(words) / max(1.0, length / 5.0)

    # UTF-16 mis-decoded as latin-1 often has many NULs / low alnum.
    nul_ratio = cleaned.count("\x00") / float(length)
    if nul_ratio > 0.02:
        return 0.05, True

    if alnum_ratio < 0.45 or word_density < 0.15:
        return max(0.1, alnum_ratio * 0.5), True

    score = min(1.0, 0.55 + (length / 2000.0) * 0.35 + min(0.1, word_density * 0.05))
    return score, False


def _extract_txt(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    if not raw:
        return "", "txt"

    # BOM sniff first — Notepad "Unicode" is UTF-16 LE.
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        try:
            return raw.decode("utf-16"), "txt"
        except UnicodeDecodeError:
            pass
    if raw.startswith(b"\xef\xbb\xbf"):
        try:
            return raw.decode("utf-8-sig"), "txt"
        except UnicodeDecodeError:
            pass

    # Heuristic: lots of NULs in even positions → UTF-16 LE without reliable BOM use
    if len(raw) >= 4 and raw[1:2] == b"\x00" and raw[3:4] == b"\x00":
        try:
            return raw.decode("utf-16-le"), "txt"
        except UnicodeDecodeError:
            pass

    for encoding in ("utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "cp1252", "latin-1"):
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        # Reject latin-1 "success" that is mostly NULs / garbage from UTF-16
        if "\x00" in text[:200] and encoding in {"latin-1", "cp1252"}:
            continue
        return text, "txt"

    return raw.decode("utf-8", errors="replace"), "txt"


def _extract_pdf_embedded(path: Path) -> tuple[str, str]:
    try:
        from pypdf import PdfReader
    except ImportError:
        logger.info("pypdf not installed; PDF local extract unavailable")
        return "", "none"

    try:
        reader = PdfReader(str(path))
        parts: list[str] = []
        for page in reader.pages:
            try:
                page_text = page.extract_text() or ""
            except Exception:
                page_text = ""
            if page_text.strip():
                parts.append(page_text)
        return "\n\n".join(parts).strip(), "pypdf"
    except Exception as error:
        logger.warning("pypdf extract failed for %s: %s", path.name, repr(error))
        return "", "pypdf_failed"


def _ocr_pil_image(image) -> str:
    import pytesseract

    try:
        from PIL import ImageOps, ImageEnhance
    except ImportError:
        ImageOps = None  # type: ignore
        ImageEnhance = None  # type: ignore

    try:
        if ImageOps is not None:
            image = ImageOps.exif_transpose(image)
    except Exception:
        pass

    try:
        image = image.convert("L")
    except Exception:
        pass

    # Upscale small phone photos / insurance card screenshots.
    try:
        width, height = image.size
        if max(width, height) < 1400:
            scale = 2 if max(width, height) < 900 else 1.5
            image = image.resize(
                (int(width * scale), int(height * scale)),
            )
    except Exception:
        pass

    try:
        if ImageOps is not None:
            image = ImageOps.autocontrast(image)
        if ImageEnhance is not None:
            image = ImageEnhance.Contrast(image).enhance(1.35)
    except Exception:
        pass

    configs = ("--psm 6", "--psm 4", "--psm 11", "")
    best = ""
    for config in configs:
        try:
            text = (
                pytesseract.image_to_string(image, config=config)
                if config
                else pytesseract.image_to_string(image)
            ) or ""
            text = text.strip()
            if len(text) > len(best):
                best = text
            # Good enough — stop early.
            if len(best) >= local_text_min_chars():
                break
        except Exception:
            continue

    return best


def _extract_image_ocr(path: Path) -> tuple[str, str]:
    try:
        from PIL import Image
    except ImportError:
        return "", "ocr_unavailable"

    if not _configure_tesseract():
        return "", "ocr_unavailable"

    try:
        image = Image.open(path)
        text = _ocr_pil_image(image)
        return text, "pytesseract"
    except Exception as error:
        logger.warning("OCR extract failed for %s: %s", path.name, repr(error))
        return "", "ocr_failed"


def _extract_pdf_ocr(path: Path) -> tuple[str, str]:
    """Render PDF pages and OCR them (for scans with little/no text layer)."""
    if not _configure_tesseract():
        return "", "ocr_unavailable"

    try:
        import fitz  # PyMuPDF
        from PIL import Image
    except ImportError:
        return "", "ocr_unavailable"

    try:
        doc = fitz.open(str(path))
    except Exception as error:
        logger.warning("PyMuPDF open failed for %s: %s", path.name, repr(error))
        return "", "pdf_ocr_failed"

    parts: list[str] = []
    try:
        page_count = min(len(doc), MAX_OCR_PDF_PAGES)
        for index in range(page_count):
            page = doc.load_page(index)
            # ~150–200 DPI for cards/statements.
            pix = page.get_pixmap(matrix=fitz.Matrix(2.5, 2.5), alpha=False)
            image = Image.open(io.BytesIO(pix.tobytes("png")))
            text = _ocr_pil_image(image)
            if text:
                parts.append(text)
    except Exception as error:
        logger.warning("PDF OCR failed for %s: %s", path.name, repr(error))
        return "", "pdf_ocr_failed"
    finally:
        doc.close()

    return "\n\n".join(parts).strip(), "pdf_ocr"


def _extract_pdf(path: Path) -> tuple[str, str]:
    text, method = _extract_pdf_embedded(path)
    min_chars = local_text_min_chars()
    score, needs_vision = _score_text_quality(text, min_chars=min_chars)
    if not needs_vision and score >= 0.5:
        return text, method

    ocr_text, ocr_method = _extract_pdf_ocr(path)
    if ocr_text and len(ocr_text.strip()) > len((text or "").strip()):
        return ocr_text, ocr_method

    return text, method if text else (ocr_method if ocr_method != "ocr_unavailable" else method)


def extract_document_text(
    path: str | Path,
    mime_type: str | None,
) -> dict[str, Any]:
    """
    Extract text locally from a document file.

    Returns:
      {
        "text": str,
        "method": str,
        "quality_score": float,
        "needs_vision": bool,
        "reader": "system" | "none",
      }
    """
    file_path = Path(path)
    mime = (mime_type or "").strip().lower() or "application/octet-stream"
    min_chars = local_text_min_chars()
    suffix = file_path.suffix.lower()

    if not file_path.exists() or not file_path.is_file():
        return {
            "text": "",
            "method": "missing",
            "quality_score": 0.0,
            "needs_vision": True,
            "reader": "none",
        }

    text = ""
    method = "none"
    is_plain_text = mime == "text/plain" or suffix == ".txt"

    try:
        if is_plain_text:
            text, method = _extract_txt(file_path)
        elif mime == "application/pdf" or suffix == ".pdf":
            text, method = _extract_pdf(file_path)
        elif mime.startswith("image/") or suffix in {
            ".png",
            ".jpg",
            ".jpeg",
            ".webp",
            ".gif",
            ".tif",
            ".tiff",
            ".bmp",
        }:
            text, method = _extract_image_ocr(file_path)
            if method == "ocr_unavailable":
                return {
                    "text": "",
                    "method": method,
                    "quality_score": 0.0,
                    "needs_vision": True,
                    "reader": "none",
                }
        else:
            # Some clients send wrong MIME for .txt
            if suffix in {".txt", ".csv", ".md", ".log"}:
                text, method = _extract_txt(file_path)
                is_plain_text = True
            else:
                try:
                    text, method = _extract_txt(file_path)
                except Exception:
                    text, method = "", "unsupported"
    except Exception as error:
        logger.warning("Local extract failed for %s: %s", file_path.name, repr(error))
        return {
            "text": "",
            "method": "error",
            "quality_score": 0.0,
            "needs_vision": True,
            "reader": "none",
        }

    quality_score, needs_vision = _score_text_quality(text, min_chars=min_chars)

    # Plain text never needs vision — GPT reads the decoded string.
    if is_plain_text and (text or "").strip() and "\x00" not in (text or "")[:500]:
        needs_vision = False
        quality_score = max(quality_score, 0.7)

    if not prefer_local_text_extract() and not is_plain_text:
        needs_vision = True

    reader = "system" if (text or "").strip() and not needs_vision else "none"

    return {
        "text": text or "",
        "method": method,
        "quality_score": float(quality_score),
        "needs_vision": bool(needs_vision),
        "reader": reader,
    }


def _mime_for_path(path: Path, mime_type: str) -> str:
    mime = (mime_type or "").strip().lower()
    if mime.startswith("image/") or mime == "application/pdf" or mime == "text/plain":
        return mime
    suffix = path.suffix.lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
        ".bmp": "image/bmp",
        ".pdf": "application/pdf",
        ".txt": "text/plain",
    }.get(suffix, mime or "application/octet-stream")


def _image_part_from_bytes(data: bytes, mime_type: str) -> dict[str, Any] | None:
    if not data:
        return None
    if len(data) > MAX_VISION_IMAGE_BYTES:
        # Still try — OpenAI may reject; caller can skip.
        logger.info("Vision image large (%s bytes); sending anyway", len(data))
    b64 = base64.b64encode(data).decode("ascii")
    return {
        "type": "image",
        "mime_type": mime_type,
        "data_b64": b64,
    }


def _vision_parts_for_file(path: Path, mime_type: str) -> list[dict[str, Any]]:
    """Build OpenAI-compatible image parts for vision fallback."""
    mime = _mime_for_path(path, mime_type)
    parts: list[dict[str, Any]] = []

    if mime.startswith("image/"):
        try:
            data = path.read_bytes()
            part = _image_part_from_bytes(data, mime if mime != "image/jpg" else "image/jpeg")
            if part:
                parts.append(part)
        except Exception as error:
            logger.warning("Vision image read failed for %s: %s", path.name, repr(error))
        return parts

    if mime == "application/pdf" or path.suffix.lower() == ".pdf":
        try:
            import fitz  # PyMuPDF
        except ImportError:
            logger.info("PyMuPDF missing; cannot render PDF for vision fallback")
            return parts

        try:
            doc = fitz.open(str(path))
        except Exception as error:
            logger.warning("PDF vision open failed for %s: %s", path.name, repr(error))
            return parts

        try:
            page_count = min(len(doc), MAX_VISION_PDF_PAGES)
            for index in range(page_count):
                page = doc.load_page(index)
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                png_bytes = pix.tobytes("png")
                part = _image_part_from_bytes(png_bytes, "image/png")
                if part:
                    parts.append(part)
        except Exception as error:
            logger.warning("PDF vision render failed for %s: %s", path.name, repr(error))
        finally:
            doc.close()

    return parts


def build_llm_document_contents(
    *,
    path: str | Path,
    mime_type: str,
    prompt: str,
    local_extract: dict[str, Any] | None = None,
    force_vision: bool = False,
) -> tuple[list[Any], dict[str, Any]]:
    """
    Build contents for the fill brain.

    Always OCR/decode first. If text is empty/weak (images/PDF), attach
    file/page images for GPT-4o-mini vision multimodal fallback.
    """
    file_path = Path(path)
    meta = dict(local_extract or extract_document_text(file_path, mime_type))
    text_block = (
        str(meta.get("text") or "").strip()
        if isinstance(meta.get("text"), str)
        else ""
    )
    needs_vision = bool(meta.get("needs_vision")) or force_vision or not text_block
    mime = _mime_for_path(file_path, mime_type)
    is_plain_text = mime == "text/plain" or file_path.suffix.lower() == ".txt"

    use_vision = bool(
        vision_fallback_enabled()
        and not is_plain_text
        and (needs_vision or force_vision)
        and (mime.startswith("image/") or mime == "application/pdf")
    )

    vision_parts: list[dict[str, Any]] = []
    if use_vision:
        vision_parts = _vision_parts_for_file(file_path, mime)
        if not vision_parts:
            use_vision = False
            logger.info(
                "Vision fallback requested but no image parts for %s",
                file_path.name,
            )

    if not text_block and not use_vision:
        text_block = (
            "[Local OCR returned no usable text and vision fallback was unavailable. "
            "Cannot fill without document text.]"
        )
        logger.info("LLM: no OCR text and no vision file=%s", file_path.name)

    if use_vision:
        intro = (
            "Read the uploaded document image(s) carefully (all visible text, tables, "
            "headers, stamps). "
        )
        if text_block and not text_block.startswith("[Local OCR"):
            intro += (
                "Local OCR also produced this text — use it as a hint, but prefer "
                "what you see in the image if they conflict:\n\n"
                f"{text_block}\n\n"
            )
        else:
            intro += (
                "Local OCR found little or no usable text — extract all fillable "
                "values from the image(s).\n\n"
            )
        contents: list[Any] = [intro, *vision_parts, prompt]
        meta = {
            **meta,
            "llm_input": "vision",
            "gemini_input": "vision",
            "read_source": "llm",
            "file_bytes_blocked": False,
            "document_text": text_block,
            "vision_parts": len(vision_parts),
        }
        logger.info(
            "LLM vision fallback file=%s ocr_chars=%s parts=%s",
            file_path.name,
            len(text_block),
            len(vision_parts),
        )
        return contents, meta

    contents = [
        (
            "Document text extracted locally from the uploaded file. "
            "Use ONLY this text as the document contents:\n\n"
            f"{text_block}"
        ),
        prompt,
    ]
    meta = {
        **meta,
        "llm_input": "text",
        "gemini_input": "text",
        "read_source": "system",
        "file_bytes_blocked": True,
        "document_text": text_block,
    }
    return contents, meta


# Compatibility alias
build_gemini_document_contents = build_llm_document_contents


def extraction_result_is_empty(result: dict[str, Any] | None) -> bool:
    """True when model returned no usable patch values."""
    if not isinstance(result, dict):
        return True
    patch = result.get("patch")
    if not isinstance(patch, dict) or not patch:
        return True

    def _has_value(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, (list, dict)):
            if not value:
                return False
            if isinstance(value, list):
                return any(_has_value(item) for item in value)
            return any(_has_value(item) for item in value.values())
        return True

    return not any(_has_value(value) for value in patch.values())


def extraction_confidence(result: dict[str, Any] | None) -> float:
    """Normalize model confidence to 0..1 for smart text→vision switching."""
    if not isinstance(result, dict):
        return 0.0
    raw = result.get("confidence")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 0.0
    if value > 1.0:
        value = value / 100.0
    return max(0.0, min(1.0, value))


def should_fallback_to_vision(
    result: dict[str, Any] | None,
    extract_meta: dict[str, Any] | None,
) -> bool:
    """Retry with GPT vision when OCR text path produced empty/weak fill."""
    if not vision_fallback_enabled():
        return False
    meta = extract_meta if isinstance(extract_meta, dict) else {}
    if meta.get("llm_input") == "vision" or meta.get("gemini_input") == "vision":
        return False  # already used vision
    if meta.get("file_bytes_blocked") is False and meta.get("vision_parts"):
        return False

    if bool(meta.get("needs_vision")):
        return True
    if extraction_result_is_empty(result):
        return True
    if extraction_confidence(result) < text_result_min_confidence():
        return True
    return False


def describe_read_source(meta: dict[str, Any] | None, *, from_cache: bool = False) -> str:
    if from_cache:
        return "cache"
    if not isinstance(meta, dict):
        return "llm"
    source = str(meta.get("read_source") or "").strip().lower()
    if source in {"system", "llm", "cache", "gemini"}:
        return "system" if source == "gemini" else source
    if meta.get("llm_input") == "vision" or meta.get("gemini_input") == "vision":
        return "llm"
    if meta.get("llm_input") == "text" or meta.get("gemini_input") == "text":
        return "system"
    return "llm"
