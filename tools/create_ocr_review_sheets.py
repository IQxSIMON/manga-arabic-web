"""Build visual contact sheets from saved OCR JSON and an existing sample ZIP.

This is an offline review aid: it does not run text detection or OCR.
"""

from __future__ import annotations

import argparse
import json
import zipfile
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARCHIVE = ROOT / "QUALITY_REVIEW_SAMPLES.zip"
DEFAULT_RESULTS = (
    ROOT / "translator_engine" / "data" / "benchmark_runs"
    / "ocr_saved_samples_20261111_targeted_rescue.json"
)
DEFAULT_OUTPUT = ROOT / "ocr_review_sheets"


def make_sheets(archive_path: Path, results_path: Path, output_dir: Path) -> list[Path]:
    report = json.loads(results_path.read_text(encoding="utf-8"))
    output_dir.mkdir(parents=True, exist_ok=True)
    created = []
    with zipfile.ZipFile(archive_path) as archive:
        for page in report.get("results", []):
            page_id = page["page"]
            original_name = f"{page_id}/original_english.webp"
            try:
                source = Image.open(BytesIO(archive.read(original_name))).convert("RGB")
            except KeyError as exc:
                raise ValueError(f"Missing original page in archive: {original_name}") from exc

            entries = []
            for index, region in enumerate(page.get("regions", []), 1):
                try:
                    left = float(region["left"])
                    top = float(region["top"])
                    right = left + float(region["width"])
                    bottom = top + float(region["height"])
                except (KeyError, TypeError, ValueError):
                    continue
                pad_x = max(12, int((right - left) * 0.08))
                pad_y = max(12, int((bottom - top) * 0.12))
                crop = source.crop((
                    max(0, int(left) - pad_x), max(0, int(top) - pad_y),
                    min(source.width, int(right) + pad_x),
                    min(source.height, int(bottom) + pad_y),
                ))
                scale = min(1.0, 680 / max(1, crop.width), 300 / max(1, crop.height))
                if scale < 1:
                    crop = crop.resize((max(1, round(crop.width * scale)),
                                        max(1, round(crop.height * scale))), Image.Resampling.LANCZOS)
                entries.append((index, region, crop))

            margin, header_h, gap = 16, 36, 12
            panel_heights = [header_h + crop.height + gap for _, _, crop in entries]
            sheet = Image.new("RGB", (720, max(1, margin * 2 + sum(panel_heights))), "#20242c")
            draw = ImageDraw.Draw(sheet)
            font = ImageFont.load_default()
            y = margin
            for index, region, crop in entries:
                label = f"#{index} [{region.get('region_type', 'unknown')}] "
                label += str(region.get("text", "")).replace("\n", " ")[:110]
                draw.text((margin, y + 7), label, fill="white", font=font)
                y += header_h
                sheet.paste(crop, (margin, y))
                y += crop.height + gap
            destination = output_dir / f"{page_id}_ocr_review.png"
            sheet.save(destination)
            created.append(destination)
    return created


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    for path in make_sheets(args.archive, args.results, args.output):
        print(path)


if __name__ == "__main__":
    main()
