"""Inspect dark connected components in page-10's clipped top balloon area."""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "translator_engine/data/benchmark_runs/page10_manual_extension_20261112/original_english.webp"
OUT_DIR = SAMPLE.parent


def main():
    image = cv2.imread(str(SAMPLE), cv2.IMREAD_COLOR)
    if image is None:
        raise FileNotFoundError(SAMPLE)
    roi = image[:500].copy()
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    rows = []
    for threshold in (30, 50, 70, 90):
        binary = (gray < threshold).astype(np.uint8)
        binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        count, labels, stats, _centroids = cv2.connectedComponentsWithStats(binary, 8)
        comps = []
        for label in range(1, count):
            x, y, width, height, area = map(int, stats[label])
            if area >= 5000 and width >= 80 and height >= 50:
                comps.append({
                    "bbox": [x, y, x + width, y + height],
                    "area": area,
                    "fill": round(area / max(1, width * height), 4),
                    "touches_top": y == 0,
                })
        rows.append({"threshold": threshold, "components": comps})
    canvas = cv2.cvtColor(roi, cv2.COLOR_BGR2RGB)
    colors = ((255, 0, 0), (0, 180, 0), (0, 0, 255), (220, 0, 220))
    for item, color in zip(rows, colors):
        for component in item["components"]:
            x1, y1, x2, y2 = component["bbox"]
            cv2.rectangle(canvas, (x1, y1), (x2, y2), color, 2)
    cv2.imwrite(str(OUT_DIR / "top_dark_components_probe.jpg"), cv2.cvtColor(canvas, cv2.COLOR_RGB2BGR))
    output = OUT_DIR / "top_dark_components_probe.json"
    output.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
