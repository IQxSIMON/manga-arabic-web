"""Probe speech-balloon proposal thresholds on a single chapter page."""
import argparse, sys, zipfile
from pathlib import Path
import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ocr_engine.speech_bubbles import detect_speech_bubbles

parser = argparse.ArgumentParser()
parser.add_argument("cbz", type=Path)
parser.add_argument("--member", default="page_006.webp")
args = parser.parse_args()
with zipfile.ZipFile(args.cbz) as archive:
    image = cv2.imdecode(np.frombuffer(archive.read(args.member), np.uint8), cv2.IMREAD_COLOR)
for threshold in (0.25, 0.2, 0.15, 0.1):
    bubbles = detect_speech_bubbles(image, threshold)
    print(f"threshold={threshold} accepted={len(bubbles)}")
    for bubble in bubbles:
        x1, y1, x2, y2 = bubble.bbox
        if 7400 < y1 < 11500:
            fill = np.count_nonzero(bubble.safe_mask) / bubble.safe_mask.size
            print(f"  y={y1}:{y2} x={x1}:{x2} conf={bubble.confidence:.3f} fill={fill:.2f}")
