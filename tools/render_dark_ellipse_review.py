"""Render high-shape dark components from a saved probe for manual review."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "translator_engine/data/benchmark_runs/page10_manual_extension_20261112"
PROBE = RUN / "top_dark_ellipse_fullpage_probe_v2.json"
OUTPUT = RUN / "top_dark_ellipse_review_v2.jpg"
SAMPLE_DIR = ROOT / "translator_engine/data/benchmark_runs/magic_emperor_920_sample"
ARCHIVE = ROOT / "QUALITY_REVIEW_SAMPLES.zip"


def main() -> None:
    if OUTPUT.exists():
        raise FileExistsError(f"Refusing to overwrite {OUTPUT}")
    data = json.loads(PROBE.read_text(encoding="utf-8"))
    crops = []
    with zipfile.ZipFile(ARCHIVE) as archive:
        for page in data["pages"]:
            name = page["page"]
            if name.startswith("chapter920_"):
                source = cv2.imread(str(SAMPLE_DIR / f"{name}.webp"))
            else:
                number = int(name.rsplit("_", 1)[-1])
                raw = archive.read(f"page_{number:02d}/original_english.webp")
                source = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
            for candidate in page["candidates"]:
                if (candidate["ellipse_component_coverage"] < 0.95
                        or candidate["component_ellipse_fill"] < 0.75):
                    continue
                x1, y1, x2, y2 = candidate["bbox"]
                pad = 18
                crop = source[max(0, y1 - pad):min(source.shape[0], y2 + pad),
                              max(0, x1 - pad):min(source.shape[1], x2 + pad)].copy()
                crop = cv2.resize(crop, (300, 210), interpolation=cv2.INTER_AREA)
                label = f"{name} {candidate['bbox']} cov={candidate['ellipse_component_coverage']} fill={candidate['component_ellipse_fill']}"
                cv2.putText(crop, label[:48], (4, 18), cv2.FONT_HERSHEY_SIMPLEX,
                            0.36, (0, 0, 255), 1, cv2.LINE_AA)
                crops.append(crop)
    if not crops:
        raise ValueError("No candidates passed the review filter")
    columns = 3
    rows = (len(crops) + columns - 1) // columns
    sheet = np.full((rows * 210, columns * 300, 3), 245, dtype=np.uint8)
    for index, crop in enumerate(crops):
        y, x = divmod(index, columns)
        sheet[y * 210:(y + 1) * 210, x * 300:(x + 1) * 300] = crop
    cv2.imwrite(str(OUTPUT), sheet)
    print(f"Rendered {len(crops)} candidate crops to {OUTPUT}")


if __name__ == "__main__":
    main()
