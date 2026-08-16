# app/ai/local_document_extract.py
"""
Local-first document text extraction.

Pipeline:
  TXT → decode (UTF-8 / UTF-16 / latin-1)
  PDF → embedded text (pypdf); if weak → OCR pages (PyMuPDF + Tesseract)
  Image → OCR (Tesseract with preprocessing)
  Quality gate → good OCR goes to Sol as text; bad pages go to Terra vision
  (faithful text only), then Sol maps fields. Sol never receives the original file.
"""

from __future__ import annotations

import base64
import copy
import io
import logging
import os
import re
from pathlib import Path
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_MIN_CHARS = 80
MAX_OCR_PDF_PAGES = 8
MAX_VISION_PDF_PAGES = 3
MAX_VISION_IMAGE_BYTES = 4_500_000
_PREPARE_CACHE_MAX = 24
_prepare_lock = Lock()
_prepare_cache: dict[str, dict[str, Any]] = {}


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


_GARBAGE_TOKEN_RE = re.compile(
    r"[^A-Za-z0-9]{4,}|[A-Za-z]{1}\d{1}[A-Za-z]{1}\d{1}|[|]{2,}|[\\/~^]{3,}"
)
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z']{1,}")
_LABELISH_RE = re.compile(
    r"\b(policy|account|member|name|date|number|address|insurance|bank|vin)\b",
    re.I,
)


def _ocr_good_min_score() -> float:
    try:
        return max(
            0.0,
            min(1.0, float(os.getenv("AI_OCR_GOOD_MIN_CONFIDENCE", "0.58"))),
        )
    except (TypeError, ValueError):
        return 0.58


def _score_text_quality(
    text: str,
    *,
    min_chars: int,
    ocr_engine_confidence: float | None = None,
    page_count: int | None = None,
) -> tuple[float, bool, str]:
    """
    Returns (quality_score 0..1, needs_vision, quality_label good|bad).
    Uses several signals — length, alphabet ratio, words, labels, OCR engine conf.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return 0.0, True, "bad"

    length = len(cleaned)
    if length < min_chars:
        return min(0.4, length / float(min_chars)), True, "bad"

    alnum = sum(1 for ch in cleaned if ch.isalnum() or ch.isspace())
    alnum_ratio = alnum / float(length) if length else 0.0
    words = _WORD_RE.findall(cleaned)
    word_count = len(words)
    word_density = word_count / max(1.0, length / 6.0)
    replacement_ratio = cleaned.count("\ufffd") / float(length)
    nul_ratio = cleaned.count("\x00") / float(length)
    garbage_hits = len(_GARBAGE_TOKEN_RE.findall(cleaned[:4000]))
    label_hits = len(_LABELISH_RE.findall(cleaned[:8000]))
    short_page = bool(page_count and page_count >= 2 and length < min_chars * page_count * 0.35)

    if nul_ratio > 0.02 or replacement_ratio > 0.04:
        return 0.05, True, "bad"
    if alnum_ratio < 0.42 or word_density < 0.12 or word_count < 8:
        return max(0.1, alnum_ratio * 0.5), True, "bad"
    if garbage_hits > 12 and word_count < 40:
        return 0.25, True, "bad"
    if short_page:
        return 0.35, True, "bad"

    score = 0.50
    score += min(0.18, (length / 2500.0) * 0.18)
    score += min(0.12, word_density * 0.08)
    score += min(0.08, label_hits * 0.015)
    if ocr_engine_confidence is not None:
        score = (score * 0.65) + (max(0.0, min(1.0, ocr_engine_confidence)) * 0.35)
    score = min(1.0, score)

    good_floor = _ocr_good_min_score()
    if score < good_floor:
        return score, True, "bad"
    return score, False, "good"


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


def _tesseract_mean_confidence(image, config: str) -> float | None:
    try:
        import pytesseract
    except ImportError:
        return None
    try:
        kwargs: dict[str, Any] = {"output_type": pytesseract.Output.DICT}
        if config:
            kwargs["config"] = config
        data = pytesseract.image_to_data(image, **kwargs)
    except Exception:
        return None
    confs: list[int] = []
    for raw in data.get("conf") or []:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value >= 0:
            confs.append(value)
    if not confs:
        return None
    return sum(confs) / (len(confs) * 100.0)


def _ocr_pil_image(image) -> tuple[str, float | None]:
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
    best_config = ""
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
                best_config = config
            # Good enough — stop early.
            if len(best) >= local_text_min_chars():
                break
        except Exception:
            continue

    engine_conf = _tesseract_mean_confidence(image, best_config) if best else None
    return best, engine_conf


def _page_result(
    *,
    page: int,
    text: str,
    method: str,
    min_chars: int,
    ocr_engine_confidence: float | None = None,
    page_count: int | None = 1,
) -> dict[str, Any]:
    score, needs_vision, quality = _score_text_quality(
        text,
        min_chars=max(20, min_chars // 2),
        ocr_engine_confidence=ocr_engine_confidence,
        page_count=page_count,
    )
    return {
        "page": page,
        "text": text or "",
        "method": method,
        "quality": quality,
        "quality_score": float(score),
        "confidence": float(score),
        "needs_vision": bool(needs_vision),
        "ocr_engine_confidence": ocr_engine_confidence,
    }


def _extract_image_ocr(path: Path) -> tuple[str, str, list[dict[str, Any]]]:
    try:
        from PIL import Image
    except ImportError:
        return "", "ocr_unavailable", []

    if not _configure_tesseract():
        return "", "ocr_unavailable", []

    try:
        image = Image.open(path)
        text, engine_conf = _ocr_pil_image(image)
        page = _page_result(
            page=1,
            text=text,
            method="pytesseract",
            min_chars=local_text_min_chars(),
            ocr_engine_confidence=engine_conf,
        )
        return text, "pytesseract", [page]
    except Exception as error:
        logger.warning("OCR extract failed for %s: %s", path.name, repr(error))
        return "", "ocr_failed", []


def _extract_pdf_ocr(path: Path) -> tuple[str, str, list[dict[str, Any]]]:
    """Render PDF pages and OCR them (for scans with little/no text layer)."""
    if not _configure_tesseract():
        return "", "ocr_unavailable", []

    try:
        import fitz  # PyMuPDF
        from PIL import Image
    except ImportError:
        return "", "ocr_unavailable", []

    try:
        doc = fitz.open(str(path))
    except Exception as error:
        logger.warning("PyMuPDF open failed for %s: %s", path.name, repr(error))
        return "", "pdf_ocr_failed", []

    pages: list[dict[str, Any]] = []
    try:
        page_count = min(len(doc), MAX_OCR_PDF_PAGES)
        min_chars = local_text_min_chars()
        for index in range(page_count):
            page = doc.load_page(index)
            # ~150–200 DPI for cards/statements.
            pix = page.get_pixmap(matrix=fitz.Matrix(2.5, 2.5), alpha=False)
            image = Image.open(io.BytesIO(pix.tobytes("png")))
            text, engine_conf = _ocr_pil_image(image)
            pages.append(
                _page_result(
                    page=index + 1,
                    text=text,
                    method="pdf_ocr",
                    min_chars=min_chars,
                    ocr_engine_confidence=engine_conf,
                    page_count=1,
                )
            )
    except Exception as error:
        logger.warning("PDF OCR failed for %s: %s", path.name, repr(error))
        return "", "pdf_ocr_failed", pages
    finally:
        doc.close()

    joined = "\n\n".join(
        f"--- Page {item['page']} ---\n{item['text']}".strip()
        for item in pages
        if str(item.get("text") or "").strip()
    ).strip()
    return joined, "pdf_ocr", pages


def _pages_from_embedded(path: Path) -> tuple[str, str, list[dict[str, Any]]]:
    try:
        from pypdf import PdfReader
    except ImportError:
        logger.info("pypdf not installed; PDF local extract unavailable")
        return "", "none", []

    try:
        reader = PdfReader(str(path))
        pages: list[dict[str, Any]] = []
        min_chars = local_text_min_chars()
        for index, page in enumerate(reader.pages[:MAX_OCR_PDF_PAGES]):
            try:
                page_text = page.extract_text() or ""
            except Exception:
                page_text = ""
            pages.append(
                _page_result(
                    page=index + 1,
                    text=page_text,
                    method="pypdf",
                    min_chars=min_chars,
                    page_count=1,
                )
            )
        joined = "\n\n".join(
            f"--- Page {item['page']} ---\n{item['text']}".strip()
            for item in pages
            if str(item.get("text") or "").strip()
        ).strip()
        return joined, "pypdf", pages
    except Exception as error:
        logger.warning("pypdf extract failed for %s: %s", path.name, repr(error))
        return "", "pypdf_failed", []


def _extract_pdf(path: Path) -> tuple[str, str, list[dict[str, Any]]]:
    text, method, pages = _pages_from_embedded(path)
    min_chars = local_text_min_chars()
    score, needs_vision, _quality = _score_text_quality(
        text,
        min_chars=min_chars,
        page_count=len(pages) or None,
    )
    if not needs_vision and score >= 0.5:
        return text, method, pages

    ocr_text, ocr_method, ocr_pages = _extract_pdf_ocr(path)
    if not ocr_pages:
        if ocr_text and len(ocr_text.strip()) > len((text or "").strip()):
            return ocr_text, ocr_method, pages
        return text, method if text else ocr_method, pages

    merged: list[dict[str, Any]] = []
    count = max(len(pages), len(ocr_pages))
    for index in range(count):
        embedded = pages[index] if index < len(pages) else None
        ocr_page = ocr_pages[index] if index < len(ocr_pages) else None
        if embedded and not embedded.get("needs_vision") and (embedded.get("text") or "").strip():
            merged.append(embedded)
            continue
        if ocr_page and not ocr_page.get("needs_vision") and (ocr_page.get("text") or "").strip():
            merged.append(ocr_page)
            continue
        if ocr_page and len(str(ocr_page.get("text") or "")) >= len(
            str((embedded or {}).get("text") or "")
        ):
            merged.append(ocr_page)
        elif embedded:
            merged.append(embedded)
        elif ocr_page:
            merged.append(ocr_page)

    joined = "\n\n".join(
        f"--- Page {item['page']} ---\n{item['text']}".strip()
        for item in merged
        if str(item.get("text") or "").strip()
    ).strip()
    used_ocr = any(item.get("method") == "pdf_ocr" for item in merged)
    return joined, (ocr_method if used_ocr else method), merged


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
        "quality": "good" | "bad",
        "source": "ocr",
        "pages": [...],
      }
    """
    file_path = Path(path)
    mime = (mime_type or "").strip().lower() or "application/octet-stream"
    min_chars = local_text_min_chars()
    suffix = file_path.suffix.lower()

    empty = {
        "text": "",
        "method": "missing",
        "quality_score": 0.0,
        "needs_vision": True,
        "reader": "none",
        "quality": "bad",
        "source": "ocr",
        "pages": [],
        "terra_invoked": False,
    }

    if not file_path.exists() or not file_path.is_file():
        return empty

    text = ""
    method = "none"
    pages: list[dict[str, Any]] = []
    is_plain_text = mime == "text/plain" or suffix == ".txt"
    engine_conf: float | None = None

    try:
        if is_plain_text:
            text, method = _extract_txt(file_path)
        elif mime == "application/pdf" or suffix == ".pdf":
            text, method, pages = _extract_pdf(file_path)
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
            text, method, pages = _extract_image_ocr(file_path)
            if pages:
                engine_conf = pages[0].get("ocr_engine_confidence")
            if method == "ocr_unavailable":
                return {
                    **empty,
                    "method": method,
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
        return {**empty, "method": "error"}

    quality_score, needs_vision, quality = _score_text_quality(
        text,
        min_chars=min_chars,
        ocr_engine_confidence=engine_conf,
        page_count=len(pages) or None,
    )

    # Plain text never needs vision — GPT reads the decoded string.
    if is_plain_text and (text or "").strip() and "\x00" not in (text or "")[:500]:
        needs_vision = False
        quality_score = max(quality_score, 0.7)
        quality = "good"

    if not prefer_local_text_extract() and not is_plain_text:
        needs_vision = True
        quality = "bad"

    reader = "system" if (text or "").strip() and not needs_vision else "none"

    return {
        "text": text or "",
        "method": method,
        "quality_score": float(quality_score),
        "needs_vision": bool(needs_vision),
        "reader": reader,
        "quality": quality,
        "source": "ocr",
        "pages": pages,
        "terra_invoked": False,
        "page_count": len(pages) or (1 if (text or "").strip() else 0),
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


def max_terra_pages() -> int:
    try:
        return max(1, min(MAX_OCR_PDF_PAGES, int(os.getenv("AI_MAX_TERRA_PAGES", "4"))))
    except (TypeError, ValueError):
        return 4


def _vision_parts_for_file(
    path: Path,
    mime_type: str,
    *,
    page_indexes: list[int] | None = None,
) -> list[dict[str, Any]]:
    """Build OpenAI-compatible image parts for Terra vision fallback."""
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
            if page_indexes:
                indexes = [index for index in page_indexes if 0 <= index < len(doc)]
            else:
                indexes = list(range(min(len(doc), MAX_VISION_PDF_PAGES)))
            for index in indexes:
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


def _light_clean_ocr(text: str) -> str:
    """Preserve labels, lines, and tables; only strip NULs and extra blank lines."""
    cleaned = (text or "").replace("\x00", "").replace("\ufffd", "")
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


TERRA_READ_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "uncertain_spans": {"type": "array"},
        "notes": {"type": "string"},
    },
    "required": ["text"],
}


def terra_read_vision_parts(
    vision_parts: list[dict[str, Any]],
    *,
    file_name: str,
    page_hint: str = "",
) -> dict[str, Any]:
    """Terra reconstructs clean text only — no section/field mapping."""
    from app.ai.json_utils import parse_llm_json
    from app.ai.llm_generate import generate_llm_content

    if not vision_parts:
        return {"text": "", "uncertain": True, "usage": None}

    prompt = (
        "Faithfully reconstruct all readable text from the attached page image(s). "
        "Preserve headings, labels, values, line breaks, tables, page markers, "
        "and checkbox states. "
        "If a character is visually ambiguous (O/0, I/1, S/5), keep it and mark "
        "uncertainty inline like [O/0]. "
        "Do not classify the document, pick an app section, map fields, or invent "
        "missing information. Return JSON only."
    )
    if page_hint:
        prompt = f"{page_hint}\n\n{prompt}"

    try:
        response = generate_llm_content(
            contents=[prompt, *vision_parts],
            response_mime_type="application/json",
            response_json_schema=TERRA_READ_SCHEMA,
            temperature=0,
            max_output_tokens=4096,
            operation="terra_vision_read",
            llm_input="vision",
            file_name=file_name,
            role="terra",
        )
    except Exception as error:
        logger.warning("Terra vision read failed for %s: %s", file_name, repr(error))
        return {"text": "", "uncertain": True, "usage": None, "error": str(error)[:300]}

    raw_text = getattr(response, "text", None) or ""
    parsed: dict[str, Any] = {}
    try:
        maybe = parse_llm_json(raw_text)
        if isinstance(maybe, dict):
            parsed = maybe
    except RuntimeError:
        parsed = {}

    text = str(parsed.get("text") or "").strip()
    if not text and raw_text.strip() and not raw_text.strip().startswith("{"):
        text = raw_text.strip()
    usage = getattr(response, "_orderly_usage", None)
    uncertain = bool(parsed.get("uncertain_spans")) or not text
    return {
        "text": text,
        "uncertain": uncertain,
        "usage": usage if isinstance(usage, dict) else None,
        "notes": str(parsed.get("notes") or ""),
    }


def _prepare_cache_key(path: Path, force_terra: bool) -> str:
    try:
        stat = path.stat()
        return f"{path.resolve()}::{stat.st_mtime_ns}::{stat.st_size}::{int(force_terra)}"
    except OSError:
        return f"{path}::{int(force_terra)}"


def _cache_prepared(key: str, meta: dict[str, Any]) -> dict[str, Any]:
    stored = copy.deepcopy(meta)
    with _prepare_lock:
        if len(_prepare_cache) >= _PREPARE_CACHE_MAX:
            oldest = next(iter(_prepare_cache))
            _prepare_cache.pop(oldest, None)
        _prepare_cache[key] = stored
    return copy.deepcopy(stored)


def clear_prepare_cache() -> None:
    with _prepare_lock:
        _prepare_cache.clear()


def prepare_document_for_sol(
    path: str | Path,
    mime_type: str,
    *,
    force_terra: bool = False,
    local_extract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    OCR first. Good text → Sol. Bad pages → Terra vision text → Sol.
    Sol never receives original PDF/image bytes.
    """
    file_path = Path(path)
    cache_key = _prepare_cache_key(file_path, force_terra)
    with _prepare_lock:
        cached = _prepare_cache.get(cache_key)
    if cached:
        return copy.deepcopy(cached)

    mime = _mime_for_path(file_path, mime_type)
    is_plain_text = mime == "text/plain" or file_path.suffix.lower() == ".txt"
    ocr = dict(local_extract or extract_document_text(file_path, mime_type))
    ocr_text = _light_clean_ocr(str(ocr.get("text") or ""))
    quality = str(ocr.get("quality") or ("bad" if ocr.get("needs_vision") else "good"))
    pages = list(ocr.get("pages") or []) if isinstance(ocr.get("pages"), list) else []
    terra_pages: list[int] = []
    terra_usages: list[dict[str, Any]] = []
    terra_uncertain = False

    can_terra = bool(
        vision_fallback_enabled()
        and not is_plain_text
        and (mime.startswith("image/") or mime == "application/pdf")
    )
    need_terra = bool(force_terra or quality == "bad" or ocr.get("needs_vision") or not ocr_text)

    if is_plain_text or not can_terra or not need_terra:
        path_name = "ocr_sol"
        prepared_text = ocr_text or (
            "[Local OCR returned no usable text and vision fallback was unavailable. "
            "Cannot fill without document text.]"
            if not ocr_text
            else ocr_text
        )
        if not ocr_text and not is_plain_text:
            logger.info("LLM: no OCR text and no vision file=%s", file_path.name)
        meta = {
            **ocr,
            "text": prepared_text,
            "document_text": prepared_text,
            "quality": "good" if (is_plain_text and ocr_text) else quality,
            "terra_invoked": False,
            "terra_pages": [],
            "source_method": "ocr",
            "pipeline_path": path_name,
            "llm_input": "text",
            "gemini_input": "text",
            "read_source": "system" if ocr_text else "none",
            "file_bytes_blocked": True,
        }
        logger.info(
            "AI PIPELINE file=%s ocr_quality=%s path=%s terra=0 pages=%s ocr_chars=%s",
            file_path.name,
            meta.get("quality"),
            path_name,
            meta.get("page_count") or len(pages),
            len(ocr_text),
        )
        return _cache_prepared(cache_key, meta)

    merged_parts: list[str] = []
    if pages and mime == "application/pdf":
        bad_indexes = [
            int(page.get("page") or 0) - 1
            for page in pages
            if force_terra or page.get("needs_vision") or not str(page.get("text") or "").strip()
        ]
        bad_indexes = [index for index in bad_indexes if index >= 0]
        cap = max_terra_pages()
        if len(bad_indexes) > cap:
            # Prefer the worst OCR pages (lowest score) when we must cap cost.
            ranked = sorted(
                pages,
                key=lambda item: float(item.get("quality_score") or 0.0),
            )
            bad_indexes = [
                int(item.get("page") or 0) - 1
                for item in ranked
                if (force_terra or item.get("needs_vision") or not str(item.get("text") or "").strip())
                and int(item.get("page") or 0) > 0
            ][:cap]

        terra_by_page: dict[int, str] = {}
        if bad_indexes:
            parts = _vision_parts_for_file(file_path, mime, page_indexes=bad_indexes)
            # One Terra call for the selected bad pages (cheaper than per-page).
            terra = terra_read_vision_parts(
                parts,
                file_name=file_path.name,
                page_hint=(
                    f"These images are PDF pages {[i + 1 for i in bad_indexes]} "
                    "in order. Reconstruct each page's text."
                ),
            )
            if terra.get("usage"):
                terra_usages.append(terra["usage"])
            terra_uncertain = bool(terra.get("uncertain"))
            reconstructed = _light_clean_ocr(str(terra.get("text") or ""))
            if reconstructed:
                # If Terra returned one blob, attach it to the first bad page
                # and leave a marker so Sol still sees page order.
                terra_by_page[bad_indexes[0]] = reconstructed
                terra_pages = [index + 1 for index in bad_indexes]
            else:
                terra_uncertain = True

        for page in pages:
            page_no = int(page.get("page") or 0)
            index = page_no - 1
            if index in terra_by_page:
                merged_parts.append(
                    f"--- Page {page_no} (terra_vision) ---\n{terra_by_page[index]}"
                )
            else:
                cleaned = _light_clean_ocr(str(page.get("text") or ""))
                if cleaned:
                    merged_parts.append(f"--- Page {page_no} ---\n{cleaned}")
                elif index in bad_indexes:
                    merged_parts.append(
                        f"--- Page {page_no} ---\n[Page could not be read confidently]"
                    )
        prepared_text = "\n\n".join(merged_parts).strip()
        if terra_by_page and len(terra_by_page) == 1 and len(bad_indexes) > 1:
            # Terra returned a combined blob — keep it once with page list.
            prepared_text = (
                f"--- Pages {[i + 1 for i in bad_indexes]} (terra_vision) ---\n"
                + next(iter(terra_by_page.values()))
            )
            good_pages = [
                f"--- Page {page.get('page')} ---\n{_light_clean_ocr(str(page.get('text') or ''))}"
                for page in pages
                if int(page.get("page") or 0) - 1 not in bad_indexes
                and _light_clean_ocr(str(page.get("text") or ""))
            ]
            prepared_text = "\n\n".join([*good_pages, prepared_text]).strip()
    else:
        parts = _vision_parts_for_file(file_path, mime)
        terra = terra_read_vision_parts(parts, file_name=file_path.name)
        if terra.get("usage"):
            terra_usages.append(terra["usage"])
        reconstructed = _light_clean_ocr(str(terra.get("text") or ""))
        terra_uncertain = bool(terra.get("uncertain")) or not reconstructed
        if reconstructed:
            prepared_text = reconstructed
            terra_pages = [1]
        else:
            prepared_text = ocr_text or (
                "[Document could not be read confidently from OCR or vision.]"
            )

    terra_invoked = bool(terra_pages)
    if not prepared_text:
        prepared_text = (
            "[Local OCR returned no usable text and vision fallback was unavailable. "
            "Cannot fill without document text.]"
        )

    meta = {
        **ocr,
        "text": prepared_text,
        "document_text": prepared_text,
        "quality": quality,
        "terra_invoked": terra_invoked,
        "terra_pages": terra_pages,
        "terra_uncertain": terra_uncertain,
        "terra_usage": terra_usages,
        "source_method": "terra_vision" if terra_invoked else "ocr",
        "pipeline_path": "ocr_terra_sol" if terra_invoked else "ocr_sol",
        "llm_input": "text",
        "gemini_input": "text",
        "read_source": "llm" if terra_invoked else "system",
        "file_bytes_blocked": True,
        "vision_parts": 0,
    }
    logger.info(
        "AI PIPELINE file=%s ocr_quality=%s path=%s terra=%s terra_pages=%s ocr_chars=%s prepared_chars=%s",
        file_path.name,
        quality,
        meta["pipeline_path"],
        int(terra_invoked),
        terra_pages,
        len(ocr_text),
        len(prepared_text),
    )
    return _cache_prepared(cache_key, meta)


def build_llm_document_contents(
    *,
    path: str | Path,
    mime_type: str,
    prompt: str,
    local_extract: dict[str, Any] | None = None,
    force_vision: bool = False,
) -> tuple[list[Any], dict[str, Any]]:
    """
    Build text-only contents for Sol.

    OCR always runs first. Terra may reconstruct text for bad pages.
    Images are never attached to the Sol mapping prompt.
    """
    file_path = Path(path)
    meta = prepare_document_for_sol(
        file_path,
        mime_type,
        force_terra=force_vision,
        local_extract=local_extract,
    )
    text_block = str(meta.get("document_text") or meta.get("text") or "").strip()
    source = str(meta.get("source_method") or "ocr")
    intro = (
        "Prepared document text. Use ONLY this text as the document contents. "
        f"Text source: {source}. "
        "Understand misspelled labels; never invent values.\n\n"
        f"{text_block}"
    )
    return [intro, prompt], meta


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
    """Retry with Terra then Sol when OCR text path produced empty/weak fill."""
    if not vision_fallback_enabled():
        return False
    meta = extract_meta if isinstance(extract_meta, dict) else {}
    if meta.get("terra_invoked"):
        return False
    if meta.get("llm_input") == "vision" or meta.get("gemini_input") == "vision":
        return False
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
