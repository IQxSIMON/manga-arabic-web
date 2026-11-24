"""Image decoding helpers shared by OCR and the renderer."""

from __future__ import annotations

from io import BytesIO

import cv2
import numpy as np
from PIL import Image


MAX_DECODED_PIXELS = 60_000_000


def decode_color_image(image_bytes: bytes):
    """Decode common formats with OpenCV, falling back to Pillow (including AVIF)."""
    encoded = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    if image is not None:
        return image
    try:
        with Image.open(BytesIO(image_bytes)) as decoded:
            if decoded.width <= 0 or decoded.height <= 0 or decoded.width * decoded.height > MAX_DECODED_PIXELS:
                return None
            rgb = np.asarray(decoded.convert("RGB"))
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    except (OSError, ValueError, cv2.error):
        return None
