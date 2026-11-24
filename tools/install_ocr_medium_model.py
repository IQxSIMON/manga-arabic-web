"""Install the optional RapidOCR PP-OCRv6 medium recognizer into its model cache."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from urllib.request import urlopen


MODEL_URL = (
    "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.10.0/"
    "onnx/PP-OCRv6/rec/PP-OCRv6_rec_medium.onnx"
)
EXPECTED_SHA256 = "eef444829dbbe18d7fea59a3f6eb75647518d2b3a9568d27c92e42940204894b"
MAX_BYTES = 100 * 1024 * 1024


def install() -> Path:
    import rapidocr

    target = Path(rapidocr.__file__).resolve().parent / "models" / "PP-OCRv6_rec_medium.onnx"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        if digest == EXPECTED_SHA256:
            return target
        raise RuntimeError(f"Existing model has unexpected SHA-256; refusing to replace: {target}")

    digest = hashlib.sha256()
    size = 0
    temporary = None
    try:
        with urlopen(MODEL_URL, timeout=60) as response, tempfile.NamedTemporaryFile(
            prefix="ocr_medium_", suffix=".download", dir=target.parent, delete=False
        ) as output:
            temporary = Path(output.name)
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise RuntimeError("Downloaded OCR model exceeded the 100 MB safety limit")
                digest.update(chunk)
                output.write(chunk)
        if digest.hexdigest() != EXPECTED_SHA256:
            raise RuntimeError("Downloaded OCR model SHA-256 did not match RapidOCR metadata")
        os.replace(temporary, target)
        return target
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


if __name__ == "__main__":
    print(install())
