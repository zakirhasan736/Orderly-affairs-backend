from __future__ import annotations

import re
from typing import Any, Iterator
from urllib.parse import urljoin, urlparse, unquote

MEDIA_FIELD_NAMES = {
    "image",
    "images",
    "image_url",
    "imageurl",
    "profile_image",
    "profileimage",
    "avatar",
    "photo",
    "photos",
    "cover",
    "cover_image",
    "coverimage",
    "banner",
    "thumbnail",
    "thumbnail_url",
    "logo",
    "gallery",
    "media",
    "media_url",
    "video",
    "video_url",
    "videourl",
    "attachment",
    "attachments",
    "document",
    "documents",
    "file",
    "file_url",
    "certificate",
    "featured_image",
    "featuredimage",
    "service_image",
    "blog_image",
    "post_image",
    "fallback_url",
    "regular_video",
    "background_media",
    "intro_video",
    "profile_media",
    "background_audio",
    "doc_name",
    "embed_url",
}

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg", ".avif", ".bmp", ".ico", ".heic", ".tif", ".tiff"}
VIDEO_EXT = {".mp4", ".mov", ".webm", ".m4v", ".avi", ".mkv", ".mpeg", ".mpg"}
AUDIO_EXT = {".mp3", ".wav", ".ogg", ".m4a", ".aac", ".flac"}
DOCUMENT_EXT = {
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".txt",
    ".csv",
    ".rtf",
    ".zip",
    ".vcf",
}

URL_RE = re.compile(r"https?://[^\s\"'<>\\]+", re.I)
EXT_RE = re.compile(r"\.([a-z0-9]{2,5})(?:$|\?|#)", re.I)

SKIP_SCHEMES = {"mailto", "tel", "javascript", "data"}
EXTERNAL_HOST_MARKERS = (
    "youtube.com",
    "youtu.be",
    "vimeo.com",
    "facebook.com",
    "fb.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "tiktok.com",
    "linkedin.com",
    "pinterest.com",
    "maps.google",
    "google.com/maps",
    "wa.me",
    "whatsapp.com",
    "rumble.com",
    "truthsocial.com",
)

# Keys that are almost always websites / socials, not files.
LINK_ONLY_KEYS = {
    "website",
    "facebook",
    "instagram",
    "twitter",
    "tiktok",
    "youtube",
    "rumble",
    "truth",
    "linkedin",
    "pinterest",
    "whatsapp",
    "review_link",
    "general_info_url",
    "profile_url",
    "mailto_url",
    "tel_url",
    "google_maps_url",
}


def _norm_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", key.lower())


def _extension(url: str) -> str:
    path = urlparse(url).path.lower()
    match = EXT_RE.search(path)
    if not match:
        return ""
    return f".{match.group(1).lower()}"


def classify_type(url: str, content_type: str | None = None) -> str:
    ctype = (content_type or "").split(";")[0].strip().lower()
    if ctype.startswith("image/"):
        return "image"
    if ctype.startswith("video/"):
        return "video"
    if ctype.startswith("audio/"):
        return "audio"
    if ctype in {"application/pdf", "application/msword"} or ctype.startswith("application/vnd"):
        return "document"
    ext = _extension(url)
    if ext in IMAGE_EXT:
        return "image"
    if ext in VIDEO_EXT:
        return "video"
    if ext in AUDIO_EXT:
        return "audio"
    if ext in DOCUMENT_EXT:
        return "document"
    if "storage/ecard" in url.lower() or "/storage/" in url.lower():
        return "other"
    return "other"


def is_external_embed(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    path = urlparse(url).path.lower()
    hay = f"{host}{path}"
    return any(marker in hay or marker in host for marker in EXTERNAL_HOST_MARKERS)


def looks_like_url(value: str) -> bool:
    text = value.strip()
    if not text:
        return False
    if text.startswith("//"):
        return True
    parsed = urlparse(text if "://" in text else f"https://{text}")
    if parsed.scheme.lower() in SKIP_SCHEMES:
        return False
    return text.startswith("http://") or text.startswith("https://") or text.startswith("//")


def normalize_url(raw: str, media_base: str) -> str:
    text = raw.strip()
    if text.startswith("//"):
        return f"https:{text}"
    if text.startswith("http://") or text.startswith("https://"):
        return text
    if text.startswith("/"):
        return urljoin(media_base + "/", text.lstrip("/"))
    return urljoin(media_base + "/", text)


def original_filename(url: str) -> str:
    path = unquote(urlparse(url).path)
    name = path.rstrip("/").split("/")[-1]
    return name or "unnamed"


def _should_consider(key: str | None, value: str) -> bool:
    if not looks_like_url(value) and not value.strip().startswith("/storage/"):
        return False
    key_n = _norm_key(key or "")
    if key_n in {_norm_key(k) for k in LINK_ONLY_KEYS} and not _extension(value):
        if "storage/ecard" not in value.lower() and not any(value.lower().endswith(ext) for ext in IMAGE_EXT | VIDEO_EXT | DOCUMENT_EXT | AUDIO_EXT):
            return False
    if key_n in {_norm_key(k) for k in MEDIA_FIELD_NAMES}:
        return True
    if _extension(value) in IMAGE_EXT | VIDEO_EXT | AUDIO_EXT | DOCUMENT_EXT:
        return True
    lowered = value.lower()
    if "storage/ecard" in lowered or "amazonaws.com" in lowered or "cloudinary.com" in lowered or "cloudfront.net" in lowered:
        return True
    if "/storage/" in lowered and not is_external_embed(value):
        return True
    return False


def _walk(node: Any, path: str) -> Iterator[tuple[str, str, Any]]:
    if isinstance(node, dict):
        for key, value in node.items():
            next_path = f"{path}.{key}" if path else str(key)
            yield from _walk(value, next_path)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            next_path = f"{path}[{index}]"
            yield from _walk(value, next_path)
    else:
        yield path, path.split(".")[-1].split("[")[0], node


def discover_media(payload: Any, *, media_base: str, section: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for json_path, key, value in _walk(payload, ""):
        candidates: list[str] = []
        if isinstance(value, str):
            if _should_consider(key, value):
                candidates.append(value)
            elif "<" in value and "http" in value:
                candidates.extend(URL_RE.findall(value))
        if not candidates:
            continue
        for raw in candidates:
            if not _should_consider(key, raw) and "storage/ecard" not in raw.lower() and not _extension(raw):
                continue
            url = normalize_url(raw, media_base)
            if urlparse(url).scheme.lower() in SKIP_SCHEMES:
                continue
            ref_path = json_path
            if ref_path in seen_paths and url == found[-1].get("original_url"):
                continue
            seen_paths.add(ref_path)
            found.append(
                {
                    "section": section,
                    "json_path": json_path or key,
                    "original_url": url,
                    "detected_type": "external" if is_external_embed(url) else classify_type(url),
                    "original_filename": original_filename(url),
                    "field_name": key,
                    "external": is_external_embed(url),
                }
            )
    # Deduplicate identical path+url pairs while keeping every distinct json_path.
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in found:
        marker = (item["json_path"], item["original_url"])
        if marker in seen:
            continue
        seen.add(marker)
        unique.append(item)
    return unique
