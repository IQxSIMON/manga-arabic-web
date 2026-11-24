"""Scan saved top-page crops for dark oval components before considering fallback."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "translator_engine/data/benchmark_runs/page10_manual_extension_20261112"
OUTPUT = RUN / "top_dark_ellipse_cross_page_probe_v2.json"
CONTACT = RUN / "top_dark_ellipse_cross_page_probe_v2.jpg"
SAMPLE = ROOT / "translator_engine/data/benchmark_runs/magic_emperor_920_sample/chapter920_page0.webp"
ARCHIVE = ROOT / "QUALITY_REVIEW_SAMPLES.zip"


def candidates(image):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    binary = (gray < 30).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, 8)
    found = []
    for label in range(1, count):
        x, y, width, height, area = map(int, stats[label])
        if area < 20000 or width < 120 or height < 100:
            continue
        component = (labels[y:y + height, x:x + width] == label).astype(np.uint8)
        contours, _ = cv2.findContours(component, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        if not contours or len(max(contours, key=cv2.contourArea)) < 5:
            continue
        contour = max(contours, key=cv2.contourArea)
        ellipse = cv2.fitEllipse(contour)
        fitted = np.zeros_like(component)
        # fitEllipse receives the bbox-local contour, so its center is already
        # local to `component`; do not subtract the page-space bbox again.
        cv2.ellipse(fitted, ellipse, 1, -1)
        intersect = int(np.count_nonzero(component & fitted))
        ellipse_area = int(np.count_nonzero(fitted))
        found.append({
            "bbox": [x, y, x + width, y + height],
            "area": area,
            "aspect_ratio": round(width / max(1, height), 4),
            "ellipse_component_coverage": round(intersect / max(1, area), 4),
            "component_ellipse_fill": round(intersect / max(1, ellipse_area), 4),
            "ellipse_center_local": [round(ellipse[0][0], 2), round(ellipse[0][1], 2)],
            "ellipse_axes": [round(ellipse[1][0], 2), round(ellipse[1][1], 2)],
        })
    return found


def main():
    if OUTPUT.exists() or CONTACT.exists():
        raise FileExistsError("Refusing to overwrite previous ellipse scan")
    images = [("page10_target", cv2.imread(str(RUN / "original_english.webp"))[:500])]
    images.append(("chapter920_page0", cv2.imread(str(SAMPLE))[:500]))
    with zipfile.ZipFile(ARCHIVE) as archive:
        for page in (2, 3, 5, 14):
            raw = archive.read(f"page_{page:02d}/original_english.webp")
            decoded = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
            images.append((f"visual_gold_page_{page:02d}", decoded[:500]))
    report = {"threshold": "grayscale < 30", "pages": []}
    tiles = []
    for name, crop in images:
        found = candidates(crop)
        report["pages"].append({"page": name, "candidates": found})
        canvas = crop.copy()
        for index, item in enumerate(found, 1):
            x1, y1, x2, y2 = item["bbox"]
            cv2.rectangle(canvas, (x1, y1), (x2, y2), (0, 255, 0), 3)
            cv2.putText(canvas, str(index), (x1 + 4, y1 + 25), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
        canvas = cv2.resize(canvas, (400, 250))
        cv2.putText(canvas, name, (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 0, 0), 1)
        tiles.append(canvas)
    sheet = np.vstack([np.hstack(tiles[:3]), np.hstack(tiles[3:])])
    cv2.imwrite(str(CONTACT), sheet)
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
