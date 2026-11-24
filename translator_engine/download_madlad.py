"""Download the pinned, Git-ignored MADLAD INT8 bundle with resumable weights."""

from __future__ import annotations

import hashlib
import os
import re
import time
from pathlib import Path

import requests


REPO_ID = "Nextcloud-AI/madlad400-3b-mt-ct2-int8"
REVISION = "aa32bbdeba7880eff2096ec044cb155a340a9400"
MODEL_SIZE = 2_950_208_329
MODEL_SHA256 = "77b9fd9ab97c1259d07089b5f854393dad81bc5fb5647d3f9a5d101c94f40daa"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = PROJECT_ROOT / "translator_engine" / "models" / "alternatives" / "madlad400_ct2"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(16 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download_small_files() -> None:
    # Small tokenizer/config files are convenient to retrieve through the Hub.
    os.environ["HF_HUB_DISABLE_XET"] = "1"
    from huggingface_hub import snapshot_download

    snapshot_download(
        repo_id=REPO_ID,
        revision=REVISION,
        local_dir=str(MODEL_DIR),
        allow_patterns=[
            "README.md", "added_tokens.json", "config.json", "generation_config.json",
            "shared_vocabulary.json", "special_tokens_map.json", "spiece.model",
            "tokenizer.json", "tokenizer_config.json",
        ],
    )


def _download_model_weights() -> Path:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    target = MODEL_DIR / "model.bin"
    if target.is_file() and target.stat().st_size == MODEL_SIZE:
        if _sha256(target) == MODEL_SHA256:
            print("MADLAD weight already present and verified.")
            return target

    partial = MODEL_DIR / ".model.bin.partial"
    url = f"https://huggingface.co/{REPO_ID}/resolve/{REVISION}/model.bin"
    session = requests.Session()
    last_report = partial.stat().st_size if partial.exists() else 0
    failures = 0

    while not partial.exists() or partial.stat().st_size < MODEL_SIZE:
        offset = partial.stat().st_size if partial.exists() else 0
        try:
            with session.get(
                url,
                headers={"Range": f"bytes={offset}-"},
                stream=True,
                timeout=(30, 90),
            ) as response:
                if response.status_code != 206:
                    raise RuntimeError(f"Expected HTTP 206, received {response.status_code}.")
                match = re.fullmatch(
                    r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", "")
                )
                if not match or int(match.group(1)) != offset or int(match.group(3)) != MODEL_SIZE:
                    raise RuntimeError("The model host returned an unexpected byte range.")
                with partial.open("ab") as stream:
                    for chunk in response.iter_content(chunk_size=8 * 1024 * 1024):
                        if not chunk:
                            continue
                        stream.write(chunk)
                        size = partial.stat().st_size
                        if size - last_report >= 128 * 1024 * 1024:
                            print(f"Downloaded {size:,} / {MODEL_SIZE:,} bytes.", flush=True)
                            last_report = size
            failures = 0
        except (OSError, requests.RequestException, RuntimeError) as exc:
            failures += 1
            print(f"Download interrupted at byte {partial.stat().st_size if partial.exists() else 0:,}: {exc}", flush=True)
            if failures >= 12:
                raise
            time.sleep(min(5 * failures, 30))

    if partial.stat().st_size != MODEL_SIZE:
        raise RuntimeError(f"Wrong model size: {partial.stat().st_size:,} bytes.")
    actual_sha = _sha256(partial)
    if actual_sha != MODEL_SHA256:
        raise RuntimeError(f"MADLAD SHA-256 mismatch: {actual_sha}")
    partial.replace(target)
    return target


def main() -> None:
    _download_small_files()
    target = _download_model_weights()
    print(f"MADLAD model ready and checksum verified: {target}")


if __name__ == "__main__":
    main()

