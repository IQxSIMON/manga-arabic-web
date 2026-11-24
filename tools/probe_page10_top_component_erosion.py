"""Check whether erosion separates the top black balloon from dark artwork."""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
IMAGE = ROOT / "translator_engine/data/benchmark_runs/page10_manual_extension_20261112/original_english.webp"
OUTPUT = IMAGE.parent / "top_component_erosion_probe.json"


def main():
    if OUTPUT.exists():
        raise FileExistsError(OUTPUT)
    image = cv2.imread(str(IMAGE), cv2.IMREAD_COLOR)
    gray = cv2.cvtColor(image[:500], cv2.COLOR_BGR2GRAY)
    target = (310, 229)  # center of the saved high-confidence OCR box
    rows = []
    for threshold in (30, 50, 70):
        raw = (gray < threshold).astype(np.uint8)
        for kernel_size in (1, 3, 5, 7, 9, 11):
            kernel = np.ones((kernel_size, kernel_size), dtype=np.uint8)
            mask = raw if kernel_size == 1 else cv2.erode(raw, kernel)
            count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
            label = int(labels[target[1], target[0]])
            if label == 0:
                candidate = None
            else:
                x, y, width, height, area = map(int, stats[label])
                candidate = {
                    "bbox": [x, y, x + width, y + height],
                    "area": area,
                    "fill": round(area / max(1, width * height), 4),
                    "component_count": count - 1,
                }
            rows.append({"threshold": threshold, "erosion_kernel": kernel_size, "target_component": candidate})
    OUTPUT.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
