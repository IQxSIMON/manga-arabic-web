"""Local RT-DETR comic text-region detector using a pinned ONNX model.

The detector is an optional helper for OCR region proposals. Bubble segmentation
and the existing recognizer remain authoritative for rendering and text content.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from threading import Lock

import cv2
import numpy as np

from image_pipeline.onnx_runtime import create_inference_session, create_session_options


LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL_PATH = PROJECT_ROOT / "translator_engine" / "models" / "comic_text_detector" / "detector-v4-s_int8.onnx"
INPUT_SIZE = 640
LABELS = {0: "bubble", 1: "text_bubble", 2: "text_free"}
_INFERENCE_LOCK = Lock()


@dataclass(frozen=True)
class ComicTextRegion:
    label: str
    confidence: float
    bbox: tuple[int, int, int, int]


class ComicTextDetector:
    """Detect comic text boxes, including captions outside speech balloons."""

    def __init__(self, model_path: str | Path | None = None, confidence: float = 0.26):
        self.model_path = Path(model_path or os.getenv("MANGA_COMIC_DETECTOR_MODEL", DEFAULT_MODEL_PATH))
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be in [0, 1]")
        self.confidence = confidence

    @property
    def available(self) -> bool:
        return self.model_path.is_file()

    def detect(self, image: np.ndarray) -> list[ComicTextRegion]:
        """Return page-relative text and balloon boxes in source-image pixels."""
        if not isinstance(image, np.ndarray) or image.ndim != 3 or image.shape[2] != 3:
            raise ValueError("Comic detector expects a 3-channel BGR image.")
        if image.dtype != np.uint8:
            raise ValueError("Comic detector expects uint8 pixels.")
        if not self.available:
            return []

        height, width = image.shape[:2]
        session = _get_session(str(self.model_path.resolve()))
        input_names = {item.name for item in session.get_inputs()}
        if not {"images", "orig_target_sizes"}.issubset(input_names):
            raise RuntimeError("The comic detector ONNX inputs do not match the pinned model.")

        found: list[ComicTextRegion] = []
        for top, bottom in self._vertical_tiles(height, width):
            tile = image[top:bottom]
            tile_height, tile_width = tile.shape[:2]
            rgb = cv2.cvtColor(tile, cv2.COLOR_BGR2RGB)
            resized = cv2.resize(rgb, (INPUT_SIZE, INPUT_SIZE), interpolation=cv2.INTER_LINEAR)
            tensor = resized.transpose(2, 0, 1)[None].astype(np.float32) / 255.0
            # The released ONNX export returns coordinates in its supplied
            # target size. Keep it fixed at 640 and map coordinates ourselves;
            # its exporter interprets the dimensions differently from HF's
            # Python post-process helper.
            feed = {
                "images": np.ascontiguousarray(tensor),
                "orig_target_sizes": np.asarray([[INPUT_SIZE, INPUT_SIZE]], dtype=np.int64),
            }
            with _INFERENCE_LOCK:
                labels, boxes, scores = session.run(["labels", "boxes", "scores"], feed)
            for label, box, score in zip(labels[0], boxes[0], scores[0]):
                label = int(label)
                confidence = float(score)
                if label not in LABELS or confidence < self.confidence:
                    continue
                coords = np.asarray(box, dtype=np.float32).reshape(-1)
                if coords.size != 4 or not np.isfinite(coords).all():
                    continue
                x1, y1, x2, y2 = coords
                x1 = int(np.floor(np.clip(x1 * tile_width / INPUT_SIZE, 0, tile_width)))
                x2 = int(np.ceil(np.clip(x2 * tile_width / INPUT_SIZE, 0, tile_width)))
                y1 = int(np.floor(np.clip(y1 * tile_height / INPUT_SIZE, 0, tile_height)))
                y2 = int(np.ceil(np.clip(y2 * tile_height / INPUT_SIZE, 0, tile_height)))
                if x2 - x1 < 6 or y2 - y1 < 6:
                    continue
                found.append(ComicTextRegion(
                    label=LABELS[label], confidence=confidence,
                    bbox=(x1, y1 + top, x2, y2 + top),
                ))
        return self._deduplicate(found)

    @staticmethod
    def _vertical_tiles(height: int, width: int) -> list[tuple[int, int]]:
        """Keep tall webtoon text legible by scanning overlapping vertical crops."""
        if height <= max(1800, width * 2.1):
            return [(0, height)]
        tile_height = max(720, min(1600, int(round(width * 1.65))))
        tile_height = min(height, tile_height)
        overlap = min(192, max(96, tile_height // 7))
        step = max(1, tile_height - overlap)
        last_start = max(0, height - tile_height)
        starts = list(range(0, last_start + 1, step))
        if not starts or starts[-1] != last_start:
            starts.append(last_start)
        return [(top, min(height, top + tile_height)) for top in starts]

    @staticmethod
    def _deduplicate(regions: list[ComicTextRegion], iou_threshold: float = 0.62) -> list[ComicTextRegion]:
        """Suppress same-class duplicates from adjacent tiles, preserve class pairs."""
        kept: list[ComicTextRegion] = []
        for candidate in sorted(regions, key=lambda item: item.confidence, reverse=True):
            x1, y1, x2, y2 = candidate.bbox
            duplicate = False
            for existing in kept:
                if candidate.label != existing.label:
                    continue
                a1, b1, a2, b2 = existing.bbox
                intersection = max(0, min(x2, a2) - max(x1, a1)) * max(0, min(y2, b2) - max(y1, b1))
                union = (x2 - x1) * (y2 - y1) + (a2 - a1) * (b2 - b1) - intersection
                if union > 0 and intersection / union >= iou_threshold:
                    duplicate = True
                    break
            if not duplicate:
                kept.append(candidate)
        return sorted(kept, key=lambda item: (item.bbox[1], item.bbox[0], item.label))


@lru_cache(maxsize=2)
def _get_session(model_path: str):
    try:
        import onnxruntime as ort
    except ImportError as exc:
        raise RuntimeError("Comic text detection requires onnxruntime in the project environment.") from exc
    if not Path(model_path).is_file():
        raise RuntimeError(f"Comic detector model not found: {model_path}")
    options = create_session_options(
        ort, cpu_threads=max(1, min(4, os.cpu_count() or 1)), allow_dml=False
    )
    LOGGER.info("Loading pinned comic text detector from %s", model_path)
    return create_inference_session(
        ort, model_path, options, allow_dml=False
    )
