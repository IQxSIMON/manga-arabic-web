"""Download and verify the pinned official Qwen3-4B Q4_K_M GGUF weights."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parents[1]
REVISION = "bc640142c66e1fdd12af0bd68f40445458f3869b"
FILENAME = "Qwen3-4B-Q4_K_M.gguf"
EXPECTED_SIZE = 2_497_280_256
EXPECTED_SHA256 = "7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5"
TARGET = ROOT / "translator_engine" / "models" / "alternatives" / "qwen3-4b" / FILENAME
URL = f"https://huggingface.co/Qwen/Qwen3-4B-GGUF/resolve/{REVISION}/{FILENAME}"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download() -> Path:
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    if TARGET.is_file():
        if TARGET.stat().st_size == EXPECTED_SIZE and sha256_file(TARGET) == EXPECTED_SHA256:
            print(f"Verified existing model: {TARGET}")
            return TARGET
        raise RuntimeError(f"Existing model failed size or SHA-256 verification: {TARGET}")

    partial = TARGET.with_suffix(TARGET.suffix + ".part")
    offset = partial.stat().st_size if partial.exists() else 0
    if offset > EXPECTED_SIZE:
        raise RuntimeError(f"Partial file exceeds expected size: {partial}")

    headers = {"Range": f"bytes={offset}-"} if offset else {}
    response = requests.get(URL, headers=headers, stream=True, timeout=(30, 180))
    response.raise_for_status()
    if offset and response.status_code != 206:
        response.close()
        partial.unlink(missing_ok=True)
        offset = 0
        response = requests.get(URL, stream=True, timeout=(30, 180))
        response.raise_for_status()

    digest = hashlib.sha256()
    if offset:
        with partial.open("rb") as existing:
            for chunk in iter(lambda: existing.read(8 * 1024 * 1024), b""):
                digest.update(chunk)

    written = offset
    mode = "ab" if offset else "wb"
    try:
        with partial.open(mode) as output:
            for chunk in response.iter_content(1024 * 1024):
                if not chunk:
                    continue
                output.write(chunk)
                digest.update(chunk)
                written += len(chunk)
                if written == EXPECTED_SIZE or written % (128 * 1024 * 1024) < len(chunk):
                    print(f"Downloaded {written:,} / {EXPECTED_SIZE:,} bytes ({written / EXPECTED_SIZE:.0%})", flush=True)
    finally:
        response.close()

    if written != EXPECTED_SIZE:
        raise RuntimeError(f"Incomplete download ({written}/{EXPECTED_SIZE} bytes); run again to resume")
    actual = digest.hexdigest()
    if actual != EXPECTED_SHA256:
        raise RuntimeError(f"SHA-256 mismatch: expected {EXPECTED_SHA256}, got {actual}")
    os.replace(partial, TARGET)
    print(f"Verified SHA-256: {actual}")
    print(f"Model saved to: {TARGET}")
    return TARGET


if __name__ == "__main__":
    download()
