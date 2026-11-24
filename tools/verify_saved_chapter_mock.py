"""Verify completeness, layer dimensions, hashes, and source-preservation of a mock render run."""
from __future__ import annotations
import argparse
import hashlib
import io
import json
import zipfile
from pathlib import Path
from PIL import Image, ImageChops

ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--archive",type=Path,default=Path(r"C:\Users\BMW1975\Downloads\11.cbz"))
    parser.add_argument("--ocr-json",type=Path,default=ROOT/"ocr_test_results/post_tiny_lowercase_filter/11/ocr_results.json")
    parser.add_argument("--render-dir",type=Path,default=ROOT/"ocr_test_results/mock_render/chapter11_20261113_final")
    args=parser.parse_args()
    ocr=json.loads(args.ocr_json.read_text(encoding="utf-8"))
    metrics=json.loads((args.render_dir/"metrics.json").read_text(encoding="utf-8"))
    if metrics.get("pages_completed")!=ocr.get("pages_expected") or len(metrics.get("pages",[]))!=ocr.get("pages_expected"):
        raise SystemExit("Metrics do not show a complete chapter.")
    expected={f"page_{n:03d}.png" for n in range(1,ocr["pages_expected"]+1)}
    actual={p.name for p in (args.render_dir/"pages").glob("page_*.png")}
    if actual!=expected:
        raise SystemExit(f"Rendered page set mismatch: missing={sorted(expected-actual)}, extra={sorted(actual-expected)}")
    page_map={p["page"]:p for p in metrics["pages"]}
    no_ocr=[p["page"] for p in metrics["pages"] if p.get("status")=="source_preserved_no_ocr"]
    source_preserved=[]
    dimensions_checked=0
    layers_checked=0
    with zipfile.ZipFile(args.archive) as archive:
        for source_page in ocr["pages"]:
            page_no=source_page["page"]
            member=source_page["source_member"]
            source_bytes=archive.read(member)
            digest=hashlib.sha256(source_bytes).hexdigest()
            if digest!=source_page["source_sha256"] or page_map[page_no].get("source_sha256")!=digest:
                raise SystemExit(f"Archive/OCR/output source SHA-256 mismatch on page {page_no}.")
            with Image.open(io.BytesIO(source_bytes)) as im:
                source=im.convert("RGB")
            out_path=args.render_dir/"pages"/f"page_{page_no:03d}.png"
            with Image.open(out_path) as im:
                output=im.convert("RGB")
            if source.size!=output.size:
                raise SystemExit(f"Dimensions changed on page {page_no}: {source.size} -> {output.size}")
            dimensions_checked+=1
            layer_dir=args.render_dir/"layers"/f"page_{page_no:03d}"
            names={"original.png","text_removal_mask.png","repaired_underlay.png","translated_text_overlay.png"}
            if {p.name for p in layer_dir.glob("*.png")}!=names:
                raise SystemExit(f"Incomplete layer set on page {page_no}.")
            for name in names:
                with Image.open(layer_dir/name) as layer:
                    if layer.size!=source.size:
                        raise SystemExit(f"Layer dimensions mismatch: page {page_no} {name}")
            layers_checked+=4
            if page_no in no_ocr:
                if ImageChops.difference(source,output).getbbox() is not None:
                    raise SystemExit(f"No-OCR source page {page_no} was altered.")
                source_preserved.append(page_no)
    summary={
        "status":"passed","pages_expected":ocr["pages_expected"],
        "page_images_verified":dimensions_checked,"layer_images_verified":layers_checked,
        "source_preserved_no_ocr_pages":source_preserved,
        "source_archive_hashes_verified":dimensions_checked,
        "rendered_regions":metrics["rendered_regions"],
        "skipped_regions":metrics["skipped_regions"],
        "elapsed_seconds":metrics["elapsed_seconds"],"peak_rss_mib":metrics["peak_rss_mib"],
        "warning":"Mock Arabic fixtures only; not a translation quality evaluation."
    }
    out=args.render_dir/"validation.json"
    out.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=True))
if __name__=="__main__":main()
