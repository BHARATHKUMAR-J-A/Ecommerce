"""Product image uploads.

Uploaded files are never trusted and never stored as sent. Each one is decoded
with Pillow, re-encoded to WebP under a generated name, and written outside the
static tree. That drops EXIF (including GPS), discards anything hidden after the
image data, and means a file claiming to be a .png but containing script is
simply rejected when it fails to decode.
"""

from __future__ import annotations

import io
import re
import secrets
from pathlib import Path

from flask import current_app
from PIL import Image, UnidentifiedImageError

# Formats we are willing to decode. SVG is deliberately absent: it is XML and can
# carry script, and browsers render it as a document.
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "GIF"}

MAX_BYTES = 5 * 1024 * 1024
MAX_EDGE = 1400
MAX_SOURCE_PIXELS = 40_000_000  # guards against decompression-bomb images
STORED_NAME = re.compile(r"^[0-9a-f]{32}\.webp$")


def uploads_dir() -> Path:
    path = Path(current_app.instance_path) / "uploads" / "products"
    path.mkdir(parents=True, exist_ok=True)
    return path


def is_stored_name(filename: str) -> bool:
    """Whitelist the names we generate, so a request can never escape the folder."""
    return bool(STORED_NAME.match(filename))


def save_product_image(storage) -> str:
    """Validate, re-encode and store one upload. Returns the stored filename.

    Raises ValueError with a message suitable for showing to the uploader.
    """
    if storage is None or not storage.filename:
        raise ValueError("No file was chosen.")

    raw = storage.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError(f"Images must be under {MAX_BYTES // (1024 * 1024)} MB.")
    if not raw:
        raise ValueError("That file was empty.")

    try:
        probe = Image.open(io.BytesIO(raw))
        image_format = probe.format
        width, height = probe.size
        probe.verify()  # structural check; consumes the image, so reopen below
    except (UnidentifiedImageError, OSError, ValueError):
        raise ValueError("That file is not a readable image.") from None

    if image_format not in ALLOWED_FORMATS:
        raise ValueError("Please upload a JPEG, PNG, WebP or GIF.")
    if width * height > MAX_SOURCE_PIXELS:
        raise ValueError("That image has too many pixels to process.")

    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
        # Honour the orientation tag, then drop the metadata with it.
        from PIL import ImageOps

        image = ImageOps.exif_transpose(image)
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
        image.thumbnail((MAX_EDGE, MAX_EDGE), Image.LANCZOS)

        filename = f"{secrets.token_hex(16)}.webp"
        image.save(uploads_dir() / filename, format="WEBP", quality=82, method=4)
    except (OSError, ValueError):
        raise ValueError("That image could not be processed.") from None

    return filename


def delete_product_image(filename: str) -> None:
    if not is_stored_name(filename):
        return
    (uploads_dir() / filename).unlink(missing_ok=True)
