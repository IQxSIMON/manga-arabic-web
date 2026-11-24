"""Diagnose OCR external-region overlap with detected speech-bubble masks."""
import argparse
import json
import sys
import zipfile
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ocr_engine.speech_bubbles import detect_speech_bubbles


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("cbz", type=Path)
    parser.add_argument("results", type=Path)
    parser.add_argument("--page", type=int, required=True)
    args = parser.parse_args()
    data = json.loads(args.results.read_text(encoding="utf-8"))
    page = next(item for item in data["pages"] if item["page"] == args.page)
    with zipfile.ZipFile(args.cbz) as archive:
        raw = archive.read(page["source_member"])
    image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    bubbles = detect_speech_bubbles(image)
    print(f"page={args.page} dimensions={image.shape[1]}x{image.shape[0]} bubbles={len(bubbles)}")
    for i, bubble in enumerate(bubbles):
        x1, y1, x2, y2 = bubble.bbox
        print(f"BUBBLE {i+1}: bbox={bubble.bbox} confidence={bubble.confidence:.3f} mask={bubble.safe_mask.shape}")
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (33, 33))
    for region in page.get("regions", []):
        if region.get("region_type") != "external_text":
            continue
        x1 = max(0, int(region["left"]))
        y1 = max(0, int(region["top"]))
        x2 = min(image.shape[1], int(region["left"] + region["width"]))
        y2 = min(image.shape[0], int(region["top"] + region["height"]))
        if x2 <= x1 or y2 <= y1:
            continue
        polygon = np.zeros((y2-y1, x2-x1), np.uint8)
        for points in region.get("ocr_polygons", []):
            points = np.array(points, dtype=np.float32).reshape(-1, 2)
            points[:, 0] -= x1
            points[:, 1] -= y1
            cv2.fillConvexPoly(polygon, np.rint(points).astype(np.int32), 255)
        area = max(1, int(np.count_nonzero(polygon)))
        overlaps = []
        for i, bubble in enumerate(bubbles):
            bx1, by1, bx2, by2 = bubble.bbox
            ix1, iy1, ix2, iy2 = max(x1,bx1), max(y1,by1), min(x2,bx2), min(y2,by2)
            if ix2 <= ix1 or iy2 <= iy1:
                continue
            mask = cv2.dilate(bubble.safe_mask, kernel, iterations=1)
            overlap = np.count_nonzero(
                (mask[iy1-by1:iy2-by1, ix1-bx1:ix2-bx1] > 0) &
                (polygon[iy1-y1:iy2-y1, ix1-x1:ix2-x1] > 0)
            ) / area
            if overlap:
                overlaps.append((i+1, round(float(overlap), 3)))
        print(f"EXTERNAL {region.get('text','')!r}: bbox=({x1},{y1},{x2},{y2}) overlap={overlaps}")


if __name__ == "__main__":
    main()
