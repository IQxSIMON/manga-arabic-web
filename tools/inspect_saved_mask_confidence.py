"""Inspect confidence and mask fill of saved chapter-920 speech masks."""

from pathlib import Path
import sys

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ocr_engine.speech_bubbles import detect_speech_bubbles

BASE = ROOT / "translator_engine/data/benchmark_runs/magic_emperor_920_sample"


def main():
    for index in (1, 2):
        image = cv2.imread(str(BASE / f"chapter920_page{index}.webp"))
        rows = []
        for bubble in detect_speech_bubbles(image):
            x1, y1, x2, y2 = bubble.bbox
            rows.append({
                "bbox": list(bubble.bbox),
                "confidence": round(bubble.confidence, 4),
                "mask_fill": round(float(np.count_nonzero(bubble.safe_mask) / bubble.safe_mask.size), 4),
            })
        print({"page": index, "masks": rows})


if __name__ == "__main__":
    main()
