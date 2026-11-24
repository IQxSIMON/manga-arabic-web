"""Print saved OCR text regions for one page in a results JSON file."""
import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument("page", type=int, help="One-based page number")
    args = parser.parse_args()
    data = json.loads(args.results.read_text(encoding="utf-8"))
    page = next((item for item in data["pages"] if item["page"] == args.page), None)
    if page is None:
        parser.error(f"Page {args.page} is not present in {args.results}")
    for index, region in enumerate(page.get("regions", []), start=1):
        print(f"r{index}\t{region.get('region_type', '')}\t{region.get('text', '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
