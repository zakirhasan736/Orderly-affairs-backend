from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

MIGRATION_DIR = Path(__file__).resolve().parent
REPO_ROOT = MIGRATION_DIR.parent
EXPORT_ROOT = Path(os.environ.get("VBIZME_EXPORT_ROOT", str(REPO_ROOT / "migration_export")))
SLUGS_FILE = Path(os.environ.get("VBIZME_SLUGS_FILE", str(MIGRATION_DIR / "slugs.txt")))

# Live public JSON is served by the Node API. Laravel https://app.vbizme.com/api/v/{slug}
# currently 404s for migrated cards; HTML still lives at https://app.vbizme.com/vCard/{slug}.
DEFAULT_SOURCE_API_BASE = os.environ.get(
    "VBIZME_SOURCE_API_BASE",
    "https://api.vbizme.com/api/v1/public",
).rstrip("/")
FALLBACK_SOURCE_API_BASE = os.environ.get(
    "VBIZME_SOURCE_API_FALLBACK",
    "https://app.vbizme.com/api",
).rstrip("/")
MEDIA_BASE_URL = os.environ.get("VBIZME_MEDIA_BASE_URL", "https://app.vbizme.com").rstrip("/")
PUBLIC_PROFILE_URL_TEMPLATE = os.environ.get(
    "VBIZME_PUBLIC_PROFILE_URL",
    "https://app.vbizme.com/vCard/{slug}",
)

API_FETCH_CONCURRENCY = max(1, int(os.environ.get("API_FETCH_CONCURRENCY", "2")))
EXPORT_DOWNLOAD_CONCURRENCY = max(1, int(os.environ.get("EXPORT_DOWNLOAD_CONCURRENCY", "4")))
IMPORT_UPLOAD_CONCURRENCY = max(1, int(os.environ.get("IMPORT_UPLOAD_CONCURRENCY", "4")))
MAX_REQUESTS_PER_MINUTE = max(10, int(os.environ.get("VBIZME_MAX_REQUESTS_PER_MINUTE", "80")))
REQUEST_TIMEOUT_SECONDS = float(os.environ.get("VBIZME_REQUEST_TIMEOUT", "45"))
MAX_DOWNLOAD_BYTES = int(os.environ.get("VBIZME_MAX_DOWNLOAD_BYTES", str(1024 * 1024 * 512)))
USER_AGENT = os.environ.get("VBIZME_USER_AGENT", "vbizme-migration-export/1.0 (read-only)")

# Dedicated public endpoints the frontend always knows how to call.
# Unknown navbar tabs still come from /post-types (exact names, including typos/spaces).
DEDICATED_SECTION_NAMES = (
    "About Me",
    "services",
    "gallery",
    "reviews",
    "video",
    "2D Video Explainer",
    "clients",
)

# Used only with --probe-all. Empty dynamic-section responses are 200s, not 404s.
PROBE_SECTION_NAMES = (
    "About Me",
    "services",
    "gallery",
    "reviews",
    "video",
    "videos",
    "2D Video Explainer",
    "2D Explainer",
    "clients",
    "blog",
    "post",
    "Resume",
    "Work Experience",
    "skills",
    "Faq",
    "Certifications/Licenses",
    "Licensing",
    "Insurance License",
    "Mission Statement",
    "Company Mission Statement",
    "Meet Our Team",
    "Join My Team",
    "Video Links",
    "Additional Services",
    "Calendar",
    "Calender",
    "Events",
    "Booking",
    "Menu",
    "BBB",
    "DCP",
    "Breakfast",
    "Lunch",
    "Dinner",
    "Inventory",
    "Home Solar",
    "Resiliency Products",
    "Property Listing",
    "Press/Media",
    "Announcement",
    "Why Choose Us",
    "See Products",
    "24/h SalesPerson",
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_run_id() -> str:
    env_id = os.environ.get("VBIZME_MIGRATION_RUN_ID")
    if env_id:
        return env_id
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


@dataclass(frozen=True)
class ExportConfig:
    source_api_base: str = DEFAULT_SOURCE_API_BASE
    fallback_api_base: str = FALLBACK_SOURCE_API_BASE
    media_base_url: str = MEDIA_BASE_URL
    export_root: Path = EXPORT_ROOT
    slugs_file: Path = SLUGS_FILE
    api_fetch_concurrency: int = API_FETCH_CONCURRENCY
    download_concurrency: int = EXPORT_DOWNLOAD_CONCURRENCY
    max_requests_per_minute: int = MAX_REQUESTS_PER_MINUTE
    timeout_seconds: float = REQUEST_TIMEOUT_SECONDS
    max_download_bytes: int = MAX_DOWNLOAD_BYTES
    user_agent: str = USER_AGENT
    discover_only: bool = False
    resume: bool = False
    retry_failed: bool = False
    verify_only: bool = False
    batch_size: int = 0
    start_from: int = 1
    slug_filter: str | None = None
    probe_all: bool = False
    run_id: str = ""
