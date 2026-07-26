# app/ai/local_document_extract.py
"""
Local-first document text extraction to reduce Gemini vision/file tokens.

Pipeline:
  TXT → read
  PDF → embedded text (pypdf); if weak → OCR pages (PyMuPDF + Tesseract)
  Image → OCR (Tesseract)
  Quality gate → Gemini text-only vs Gemini vision fallback
"""

from __future__ import annotations

import io
import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_MIN_CHARS = 80
MAX_OCR_PDF_PAGES = 8


def prefer_local_text_extract() -> bool:
    raw = os.getenv("AI_PREFER_LOCAL_TEXT_EXTRACT", "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def local_text_min_chars() -> int:
    try:
        return max(1, int(os.getenv("AI_LOCAL_TEXT_MIN_CHARS", str(DEFAULT_MIN_CHARS))))
    except (TypeError, ValueError):
        return DEFAULT_MIN_CHARS


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

    if alnum_ratio < 0.45 or word_density < 0.15:
        return max(0.1, alnum_ratio * 0.5), True

    score = min(1.0, 0.55 + (length / 2000.0) * 0.35 + min(0.1, word_density * 0.05))
    return score, False


def _extract_txt(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return raw.decode(encoding), "txt"
        except UnicodeDecodeError:
            continue
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

    # Light preprocess: grayscale helps many phone photos.
    try:
        image = image.convert("L")
    except Exception:
        pass

    return (pytesseract.image_to_string(image) or "").strip()


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
            # ~150 DPI — enough for cards/statements without huge memory.
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
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

    # Keep whatever embedded text we had; caller may still need vision.
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

    try:
        if mime == "text/plain" or file_path.suffix.lower() == ".txt":
            text, method = _extract_txt(file_path)
        elif mime == "application/pdf" or file_path.suffix.lower() == ".pdf":
            text, method = _extract_pdf(file_path)
        elif mime.startswith("image/") or file_path.suffix.lower() in {
            ".png",
            ".jpg",
            ".jpeg",
            ".webp",
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

    if not prefer_local_text_extract():
        needs_vision = True

    reader = "system" if (text or "").strip() and not needs_vision else "none"

    return {
        "text": text or "",
        "method": method,
        "quality_score": float(quality_score),
        "needs_vision": bool(needs_vision),
        "reader": reader,
    }


def build_gemini_document_contents(
    *,
    path: str | Path,
    mime_type: str,
    prompt: str,
    local_extract: dict[str, Any] | None = None,
    force_vision: bool = False,
) -> tuple[list[Any], dict[str, Any]]:
    """
    Build Gemini `contents` preferring local text when quality is good.

    Returns (contents, extract_meta).
    Set force_vision=True to always send file bytes (vision/file path).
    """
    from google.genai import types

    file_path = Path(path)
    meta = local_extract or extract_document_text(file_path, mime_type)

    use_text = (
        not force_vision
        and prefer_local_text_extract()
        and not meta.get("needs_vision")
        and isinstance(meta.get("text"), str)
        and str(meta.get("text") or "").strip()
    )

    if use_text:
        text_block = str(meta["text"]).strip()
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
            "gemini_input": "text",
            "read_source": "system",
        }
        return contents, meta

    file_bytes = file_path.read_bytes()
    contents = [
        types.Part.from_bytes(data=file_bytes, mime_type=mime_type),
        prompt,
    ]
    meta = {
        **meta,
        "gemini_input": "file_bytes",
        "forced_vision": bool(force_vision),
        "read_source": "gemini",
    }
    return contents, meta


def extraction_result_is_empty(result: dict[str, Any] | None) -> bool:
    """True when Gemini returned no usable patch values."""
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
        # Some prompts return 0–100.
        value = value / 100.0
    return max(0.0, min(1.0, value))


def should_fallback_to_vision(
    result: dict[str, Any] | None,
    extract_meta: dict[str, Any] | None,
) -> bool:
    """
    Smart switch: keep text path when quality + fill confidence are solid;
    otherwise one Gemini vision pass.
    """
    meta = extract_meta or {}
    if meta.get("gemini_input") != "text":
        return False
    if extraction_result_is_empty(result):
        return True

    min_conf = 0.45
    try:
        min_conf = float(os.getenv("AI_TEXT_RESULT_MIN_CONFIDENCE", "0.45"))
    except (TypeError, ValueError):
        min_conf = 0.45

    conf = extraction_confidence(result)
    quality = float(meta.get("quality_score") or 0.0)

    # Low model confidence or borderline OCR/text quality → vision.
    if conf > 0 and conf < min_conf:
        return True
    if quality < 0.55 and conf < 0.7:
        return True
    return False


def describe_read_source(meta: dict[str, Any] | None, *, from_cache: bool = False) -> str:
    if from_cache:
        return "cache"
    if not isinstance(meta, dict):
        return "gemini"
    source = str(meta.get("read_source") or "").strip().lower()
    if source in {"system", "gemini", "cache"}:
        return source
    if meta.get("gemini_input") == "text":
        return "system"
    return "gemini"
