"""Create a contact sheet for masks beyond manually counted dialogue bubbles."""

from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "translator_engine/data/benchmark_runs/magic_emperor_920_sample"
EXTRAS = {
    "page1_extra": (1, (0, 8418, 208, 8504)),
    "page2_extra": (2, (276, 4480, 435, 4583)),
}


def main():
    cards = []
    for label, (page_index, box) in EXTRAS.items():
        image = cv2.imread(str(BASE / f"chapter920_page{page_index}.webp"))
        x1, y1, x2, y2 = box
        crop = image[max(0, y1 - 180):min(image.shape[0], y2 + 180),
                     max(0, x1 - 180):min(image.shape[1], x2 + 180)].copy()
        cv2.rectangle(crop, (max(0, x1 - max(0, x1 - 180)), max(0, y1 - max(0, y1 - 180))),
                      (min(crop.shape[1] - 1, x2 - max(0, x1 - 180)),
                       min(crop.shape[0] - 1, y2 - max(0, y1 - 180))), (0, 0, 255), 4)
        cv2.putText(crop, label, (12, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
        cards.append(crop)
    height = max(card.shape[0] for card in cards)
    width = max(card.shape[1] for card in cards)
    resized = [cv2.resize(card, (width, height)) for card in cards]
    cv2.imwrite(str(BASE / "chapter920_seam_extra_masks_review.jpg"), np.hstack(resized))


if __name__ == "__main__":
    main()
