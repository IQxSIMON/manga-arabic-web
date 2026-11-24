"""PP-OCRv6_manga v0.3 ONNX adapter used by the application OCR pipeline."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from types import SimpleNamespace

import cv2
import numpy as np
import onnxruntime as ort

from image_pipeline.onnx_runtime import create_inference_session, create_session_options


MODEL_DIR = (
    Path(__file__).resolve().parents[1]
    / "translator_engine" / "models" / "ppocrv6_manga_v03"
)


@dataclass
class OCRResult:
    boxes: list
    txts: list
    scores: list


def _polygon_score(probability_map: np.ndarray, contour: np.ndarray) -> float:
    height, width = probability_map.shape[:2]
    points = contour.reshape(-1, 2).astype(np.int32)
    x1 = int(np.clip(points[:, 0].min(), 0, width - 1))
    x2 = int(np.clip(points[:, 0].max(), 0, width - 1))
    y1 = int(np.clip(points[:, 1].min(), 0, height - 1))
    y2 = int(np.clip(points[:, 1].max(), 0, height - 1))
    if x2 <= x1 or y2 <= y1:
        return 0.0
    mask = np.zeros((y2 - y1 + 1, x2 - x1 + 1), dtype=np.uint8)
    local = points.copy()
    local[:, 0] -= x1
    local[:, 1] -= y1
    cv2.fillPoly(mask, [local], 1)
    return float(cv2.mean(probability_map[y1:y2 + 1, x1:x2 + 1], mask)[0])


def _unclip(contour: np.ndarray, ratio: float) -> np.ndarray | None:
    points = contour.reshape(-1, 2).astype(np.float32)
    x, y = points[:, 0], points[:, 1]
    area = 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))
    perimeter = float(np.linalg.norm(np.roll(points, -1, axis=0) - points, axis=1).sum())
    if area <= 0 or perimeter <= 0:
        return None
    try:
        import pyclipper
        offset = pyclipper.PyclipperOffset()
        offset.AddPath(points.astype(np.int32).tolist(), pyclipper.JT_ROUND, pyclipper.ET_CLOSEDPOLYGON)
        paths = offset.Execute(area * ratio / perimeter)
        return np.asarray(paths[0], dtype=np.float32) if len(paths) == 1 else None
    except (ImportError, ValueError, RuntimeError):
        return None


class MangaOCRv03:
    """CPU ONNX text detector/recognizer with a RapidOCR-compatible result."""

    def __init__(self, model_dir: Path | str = MODEL_DIR):
        model_dir = Path(model_dir)
        detector = model_dir / "det" / "manga_det.onnx"
        recognizer = model_dir / "rec" / "manga_rec.onnx"
        dictionary = model_dir / "ppocrv6_dict.txt"
        missing = [str(path) for path in (detector, recognizer, dictionary) if not path.is_file()]
        if missing:
            raise RuntimeError("PP-OCRv6_manga v0.3 files are missing: " + ", ".join(missing))
        options = create_session_options(ort, cpu_threads=2, allow_dml=False)
        self.det = create_inference_session(
            ort, detector, options, allow_dml=False
        )
        self.rec = create_inference_session(
            ort, recognizer, options, allow_dml=False
        )
        self.det_input = self.det.get_inputs()[0].name
        self.rec_input = self.rec.get_inputs()[0].name
        with dictionary.open(encoding="utf-8") as stream:
            self.vocab = [""] + [line.rstrip("\r\n") for line in stream] + [" "]

    @staticmethod
    def _prepare_crop(image: np.ndarray) -> np.ndarray:
        crop = image
        if crop.shape[0] > crop.shape[1]:
            crop = cv2.rotate(crop, cv2.ROTATE_90_COUNTERCLOCKWISE)
        height, width = crop.shape[:2]
        target_width = max(16, int(round(48.0 * width / max(1, height))))
        resized = cv2.resize(crop, (target_width, 48)).astype(np.float32) / 255.0
        return ((resized - 0.5) / 0.5).transpose((2, 0, 1))[None, ...]

    def _recognize(self, crop: np.ndarray) -> tuple[str, float]:
        logits = np.asarray(self.rec.run(None, {self.rec_input: self._prepare_crop(crop)})[0], dtype=np.float32)[0]
        indices = np.argmax(logits, axis=-1)
        # This model's ONNX export already returns per-position probabilities
        # (each row sums to ~1). Re-softmaxing them flattens the distribution
        # and incorrectly drives every confidence below the app's OCR threshold.
        row_sums = logits.sum(axis=-1, keepdims=True)
        if float(logits.min()) >= 0.0 and np.allclose(row_sums, 1.0, atol=2e-3):
            probabilities = np.clip(logits, 0.0, 1.0)
        else:
            shifted = logits - logits.max(axis=-1, keepdims=True)
            probabilities = np.exp(shifted)
            probabilities /= np.maximum(probabilities.sum(axis=-1, keepdims=True), 1e-12)
        chars = []
        token_confidences = []
        previous = 0
        for position, index in enumerate(indices):
            index = int(index)
            if index != 0 and index != previous and index < len(self.vocab):
                chars.append(self.vocab[index])
                token_confidences.append(float(probabilities[position, index]))
            previous = index
        text = "".join(chars).strip()
        confidence = float(np.mean(token_confidences)) if token_confidences else 0.0
        return text, confidence

    def recognize_txt(self, crops: list[np.ndarray]) -> SimpleNamespace:
        texts, scores = [], []
        for crop in crops:
            text, score = self._recognize(crop)
            texts.append(text)
            scores.append(score)
        return SimpleNamespace(txts=texts, scores=scores)

    def __call__(self, image: np.ndarray) -> OCRResult:
        if image is None or image.ndim != 3 or image.shape[2] != 3:
            raise ValueError("OCR expects a three-channel BGR image.")
        height, width = image.shape[:2]
        if height <= 0 or width <= 0:
            return OCRResult([], [], [])
        if max(height, width) / min(height, width) > 2:
            scale = min(1.0, (0.75 * 960.0 * 960.0 / (height * width)) ** 0.5)
        else:
            scale = min(1.0, 960.0 / max(height, width))
        resized_h = max(int(round(height * scale / 32) * 32), 32)
        resized_w = max(int(round(width * scale / 32) * 32), 32)
        resized = cv2.resize(image, (resized_w, resized_h)).astype(np.float32) / 255.0
        mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
        std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
        # Match the preprocessing used in the measured v0.3 comparison pipeline.
        normalized = (resized - mean) / std
        prediction = np.asarray(
            self.det.run(None, {self.det_input: normalized.transpose((2, 0, 1))[None, ...]})[0],
            dtype=np.float32,
        )[0, 0]
        binary = (prediction > 0.15).astype(np.uint8) * 255
        contours, _ = cv2.findContours(binary, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        candidates = []
        for contour in contours:
            if contour.ndim != 3 or contour.shape[0] < 4 or _polygon_score(prediction, contour) < 0.25:
                continue
            points = _unclip(contour, 1.5)
            if points is None or not len(points):
                continue
            _, (box_w, box_h), _ = cv2.minAreaRect(points)
            if min(box_w, box_h) < 3:
                continue
            points[:, 0] = np.clip(np.round(points[:, 0] / resized_w * width), 0, width - 1)
            points[:, 1] = np.clip(np.round(points[:, 1] / resized_h * height), 0, height - 1)
            candidates.append(points)
        candidates.sort(key=lambda item: (float(item[:, 1].min()), float(item[:, 0].min())))
        boxes, texts, scores = [], [], []
        for points in candidates:
            x1 = max(0, int(np.floor(points[:, 0].min())))
            y1 = max(0, int(np.floor(points[:, 1].min())))
            x2 = min(width, int(np.ceil(points[:, 0].max())))
            y2 = min(height, int(np.ceil(points[:, 1].max())))
            crop = image[y1:y2, x1:x2]
            if crop.size == 0:
                continue
            text, score = self._recognize(crop)
            boxes.append(points.astype(np.float32))
            texts.append(text)
            scores.append(score)
        return OCRResult(boxes, texts, scores)


_ENGINE_LOCK = Lock()
_ENGINE = None


def get_manga_ocr_engine() -> MangaOCRv03:
    global _ENGINE
    if _ENGINE is None:
        with _ENGINE_LOCK:
            if _ENGINE is None:
                _ENGINE = MangaOCRv03()
    return _ENGINE
