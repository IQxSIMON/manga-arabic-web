"""Evaluate ellipse fit for the dark component under the page-10 top text."""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "translator_engine/data/benchmark_runs/page10_manual_extension_20261112/original_english.webp"
OUTPUT = SAMPLE.parent / "top_ellipse_fit_probe.json"
IMAGE_OUTPUT = SAMPLE.parent / "top_ellipse_fit_probe.jpg"


def main():
    if OUTPUT.exists() or IMAGE_OUTPUT.exists():
        raise FileExistsError("Refusing to overwrite saved top-ellipse probe")
    image = cv2.imread(str(SAMPLE), cv2.IMREAD_COLOR)
    gray = cv2.cvtColor(image[:500], cv2.COLOR_BGR2GRAY)
    binary = (gray < 30).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, 8)
    target_label = int(labels[229, 310])
    component = (labels == target_label).astype(np.uint8)
    contours, _ = cv2.findContours(component, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    contour = max(contours, key=cv2.contourArea)
    if len(contour) < 5:
        raise RuntimeError("Target dark component has too few points for ellipse fit")
    ellipse = cv2.fitEllipse(contour)
    fitted_mask = np.zeros(component.shape, dtype=np.uint8)
    cv2.ellipse(fitted_mask, ellipse, 1, -1)
    component_area = int(np.count_nonzero(component))
    fitted_area = int(np.count_nonzero(fitted_mask))
    intersection = int(np.count_nonzero(component & fitted_mask))
    x, y, width, height, area = map(int, stats[target_label])
    result = {
        "component_bbox": [x, y, x + width, y + height],
        "component_area": component_area,
        "component_contour_area": round(cv2.contourArea(contour), 2),
        "ellipse_center": [round(ellipse[0][0], 2), round(ellipse[0][1], 2)],
        "ellipse_axes": [round(ellipse[1][0], 2), round(ellipse[1][1], 2)],
        "ellipse_angle": round(ellipse[2], 2),
        "fitted_ellipse_area": fitted_area,
        "component_coverage_by_ellipse": round(intersection / max(1, component_area), 4),
        "ellipse_coverage_by_component": round(intersection / max(1, fitted_area), 4),
        "note": "fit is exploratory on one manually reviewed top panel, not a validated general detector",
    }
    canvas = cv2.cvtColor(image[:500], cv2.COLOR_BGR2RGB)
    cv2.ellipse(canvas, ellipse, (0, 255, 0), 4)
    cv2.imwrite(str(IMAGE_OUTPUT), cv2.cvtColor(canvas, cv2.COLOR_RGB2BGR))
    OUTPUT.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
