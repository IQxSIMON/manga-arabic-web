"""Split saved originals and detection overlays into comparable full-width strips."""

from __future__ import annotations

import argparse
import zipfile
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARCHIVE = ROOT / "QUALITY_REVIEW_SAMPLES.zip"
DEFAULT_OUTPUT = ROOT / "ocr_coverage_review_temp"


def create_strips(archive_path: Path, output_dir: Path, pages: list[str], strip_height: int) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    created = []
    with zipfile.ZipFile(archive_path) as archive:
        for page_id in pages:
            original = Image.open(BytesIO(archive.read(f"{page_id}/original_english.webp"))).convert("RGB")
            overlay = Image.open(BytesIO(archive.read(f"{page_id}/detection_regions_numbered.png"))).convert("RGB")
            if original.size != overlay.size:
                raise ValueError(f"Original/detection dimensions differ for {page_id}")
            for strip_index, top in enumerate(range(0, original.height, strip_height), 1):
                bottom = min(original.height, top + strip_height)
                source_crop = original.crop((0, top, original.width, bottom))
                overlay_crop = overlay.crop((0, top, overlay.width, bottom))
                label_h = 40
                sheet = Image.new("RGB", (original.width * 2, source_crop.height + label_h), "#20242c")
                draw = ImageDraw.Draw(sheet)
                font = ImageFont.load_default()
                draw.text((12, 12), f"{page_id} segment {strip_index} original", fill="white", font=font)
                draw.text((original.width + 12, 12), f"{page_id} segment {strip_index} detections", fill="white", font=font)
                sheet.paste(source_crop, (0, label_h))
                sheet.paste(overlay_crop, (original.width, label_h))
                destination = output_dir / f"{page_id}_segment_{strip_index:02d}.png"
                sheet.save(destination)
                created.append(destination)
    return created


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--pages", default="page_03,page_05,page_14")
    parser.add_argument("--strip-height", type=int, default=3000)
    args = parser.parse_args()
    if args.strip_height < 500:
        parser.error("--strip-height must be at least 500 pixels")
    for path in create_strips(
        args.archive, args.output,
        [page.strip() for page in args.pages.split(",") if page.strip()],
        args.strip_height,
    ):
        print(path)


if __name__ == "__main__":
    main()
