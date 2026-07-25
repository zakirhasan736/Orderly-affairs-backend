"""Upload brand-logo.png to Cloudinary and print the secure URL."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

import cloudinary
import cloudinary.uploader

SRC = Path(
    r"E:\2026-07-07\Orderly-affairs-frontend-1\public\images\brand-logo.png"
)


def main() -> None:
    cloudinary.config(
        cloud_name=os.environ["CLOUDINARY_CLOUD_NAME"],
        api_key=os.environ["CLOUDINARY_API_KEY"],
        api_secret=os.environ["CLOUDINARY_API_SECRET"],
        secure=True,
    )
    if not SRC.exists():
        raise SystemExit(f"missing {SRC}")

    res = cloudinary.uploader.upload(
        str(SRC),
        public_id="orderly-affairs/brand-logo",
        overwrite=True,
        resource_type="image",
        quality="auto:good",
    )
    print(res["secure_url"])


if __name__ == "__main__":
    main()
