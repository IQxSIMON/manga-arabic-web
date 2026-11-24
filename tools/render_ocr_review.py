"""Rebuild OCR review overlays from saved per-page OCR JSON, without re-running OCR."""
import json
import zipfile
from pathlib import Path

import cv2
import numpy as np


def render_chapter(archive: Path, result_json: Path) -> int:
    data = json.loads(result_json.read_text(encoding="utf-8"))
    out = result_json.parent / "overlays"
    out.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        for page in data["pages"]:
            image = cv2.imdecode(np.frombuffer(zf.read(page["source_member"]), dtype=np.uint8), cv2.IMREAD_COLOR)
            for row in page.get("regions", []):
                box = row.get("bubble_bbox")
                if not box:
                    x, y = int(row.get("left", 0)), int(row.get("top", 0))
                    box = [x, y, x + int(row.get("width", 0)), y + int(row.get("height", 0))]
                x1, y1, x2, y2 = map(int, box)
                color = (30, 220, 30) if row.get("region_type") == "speech_bubble" else (0, 150, 255)
                cv2.rectangle(image, (x1, y1), (x2, y2), color, max(2, image.shape[1] // 500))
                cv2.putText(image, str(row.get("reading_order", "?")), (x1, max(20, y1 - 5)),
                            cv2.FONT_HERSHEY_SIMPLEX, max(.6, image.shape[1] / 1800), color, 2, cv2.LINE_AA)
            cv2.imwrite(str(out / f"page_{page['page']:03}.jpg"), image,
                        [cv2.IMWRITE_JPEG_QUALITY, 90])
    return len(data["pages"])


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("result_json", type=Path)
    args = parser.parse_args()
    print(f"rendered_pages={render_chapter(args.archive, args.result_json)}")
