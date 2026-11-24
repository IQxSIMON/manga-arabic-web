"""Compare existing and relaxed speech-bubble thresholds on a CBZ without OCR."""
import json
import sys
import zipfile
from pathlib import Path
import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ocr_engine.speech_bubbles import detect_speech_bubbles


def iou(a, b):
    x1, y1, x2, y2 = a
    u1, v1, u2, v2 = b
    iw, ih = max(0, min(x2, u2)-max(x1, u1)), max(0, min(y2, v2)-max(y1, v1))
    inter = iw * ih
    return inter / max(1, (x2-x1)*(y2-y1) + (u2-u1)*(v2-v1) - inter)


def main():
    if len(sys.argv) != 3:
        raise SystemExit("usage: probe_bubble_recall_chapter.py input.cbz output.json")
    cbz, output = map(Path, sys.argv[1:])
    pages = []
    with zipfile.ZipFile(cbz) as archive:
        members = sorted((m for m in archive.namelist()
                          if Path(m).suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}),
                         key=lambda name: name.lower())
        for page_no, member in enumerate(members, 1):
            image = cv2.imdecode(np.frombuffer(archive.read(member), np.uint8), cv2.IMREAD_COLOR)
            base = detect_speech_bubbles(image, 0.25)
            relaxed = detect_speech_bubbles(image, 0.20)
            added = []
            for bubble in relaxed:
                if not any(iou(bubble.bbox, old.bbox) >= 0.5 for old in base):
                    added.append({"bbox": list(bubble.bbox), "confidence": round(bubble.confidence, 4),
                                  "mask_fill": round(np.count_nonzero(bubble.safe_mask) /
                                                     max(1, bubble.safe_mask.size), 3)})
            print(f"page={page_no} size={image.shape[1]}x{image.shape[0]} "
                  f"base={len(base)} relaxed={len(relaxed)} added={len(added)} {added}")
            pages.append({"page": page_no, "member": member, "base_count": len(base),
                          "relaxed_count": len(relaxed), "added": added})
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"thresholds": [0.25, 0.20], "pages": pages}, indent=2),
                      encoding="utf-8")
    print(f"saved={output}")


if __name__ == "__main__":
    main()
