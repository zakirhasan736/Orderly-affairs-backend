"""Build a light, transparent email logo from the portal brand mark."""

from __future__ import annotations

import os
from pathlib import Path

from PIL import Image

SRC = Path(
    r"E:\2026-07-07\Orderly-affairs-frontend-1\public\images\brand-logo.png"
)
OUT = Path(
    r"E:\2026-07-07\Orderly-affairs-frontend-1\public\images\email-logo.png"
)


def main() -> None:
    im = Image.open(SRC).convert("RGBA")
    pixels = im.load()
    assert pixels is not None
    w, h = im.size

    # Dark mark on near-black: drop black bg, render ink as paper white for navy headers.
    for y in range(h):
        for x in range(w):
            r, g, b, a = pixels[x, y]
            brightness = (r + g + b) / 3
            if brightness < 28:
                pixels[x, y] = (0, 0, 0, 0)
            else:
                pixels[x, y] = (247, 246, 242, a)

    bbox = im.getbbox()
    if bbox:
        im = im.crop(bbox)
        pad = 24
        padded = Image.new(
            "RGBA", (im.width + pad * 2, im.height + pad * 2), (0, 0, 0, 0)
        )
        padded.paste(im, (pad, pad), im)
        im = padded

    im = im.resize((240, 240), Image.Resampling.LANCZOS)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    im.save(OUT, format="PNG", optimize=True)
    print(f"wrote {OUT} size={im.size} bytes={os.path.getsize(OUT)}")


if __name__ == "__main__":
    main()
