"""Inspect existing recognizer outputs on one saved chapter bubble crop."""
from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ocr_engine import LocalRapidOCR  # noqa: E402
from ocr_engine.speech_bubbles import detect_speech_bubbles  # noqa: E402


def describe(engine, crop, label):
    result = engine(crop)
    texts = getattr(result, "txts", None)
    scores = getattr(result, "scores", None)
    texts = list(texts) if texts is not None else []
    scores = list(scores) if scores is not None else []
    print(label, "line_count=", len(texts), "texts=", repr(list(zip(texts, scores))))


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("results", type=Path)
    parser.add_argument("--page", type=int, required=True)
    parser.add_argument("--text-contains", required=True)
    args = parser.parse_args()
    report = json.loads(args.results.read_text(encoding="utf-8"))
    page = next(page for page in report["pages"] if page["page"] == args.page)
    region = next(row for row in page["regions"] if args.text_contains.casefold() in row["text"].casefold())
    bubble_id = int(region["bubble_id"])
    with zipfile.ZipFile(args.archive) as archive:
        encoded = np.frombuffer(archive.read(page["source_member"]), dtype=np.uint8)
    image = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    bubbles = detect_speech_bubbles(image)
    bubble = bubbles[bubble_id - 1]
    x1, y1, x2, y2 = bubble.bbox
    crop = image[y1:y2, x1:x2].copy()
    crop[bubble.safe_mask == 0] = 255
    print("page=", args.page, "bubble_id=", bubble_id, "bbox=", bubble.bbox, "crop_shape=", crop.shape)
    engine = LocalRapidOCR()
    describe(engine._get_engine(), crop, "primary_source")
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    describe(engine._get_engine(), cv2.cvtColor(clahe, cv2.COLOR_GRAY2BGR), "primary_clahe")
    doubled = cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    describe(engine._get_engine(), doubled, "primary_2x")
    medium = engine._get_medium_engine()
    if medium is not None:
        describe(medium, crop, "medium_source")
        describe(medium, cv2.cvtColor(clahe, cv2.COLOR_GRAY2BGR), "medium_clahe")
        describe(medium, doubled, "medium_2x")


if __name__ == "__main__":
    main()
