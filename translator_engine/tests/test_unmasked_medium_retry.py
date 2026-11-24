from types import SimpleNamespace

import numpy as np

from ocr_engine.local_rapidocr import LocalRapidOCR


def test_unmasked_medium_retry_recovers_mask_damaged_bubble_text(monkeypatch):
    original = [
        "COME ON, I'VE",
        "ALSO IMPROVED THIS",
        "MOVE, LET'S TEST IT",
        "IWIH NO ⊥NO",
    ]
    corrected = [
        "COME ON, I'VE",
        "ALSO IMPROVED THIS",
        "MOVE, LET'S TEST IT",
        "OUT ON HIM!",
    ]

    class MediumRetry:
        calls = 0

        def __call__(self, _image):
            self.calls += 1
            texts = original if self.calls == 1 else corrected
            boxes = np.asarray([
                [[0, index * 12], [80, index * 12],
                 [80, index * 12 + 10], [0, index * 12 + 10]]
                for index in range(len(texts))
            ], dtype=np.float32)
            return SimpleNamespace(
                boxes=boxes,
                txts=texts,
                scores=[0.99] * len(texts),
            )

    retry_engine = MediumRetry()
    ocr = LocalRapidOCR()
    monkeypatch.setattr(ocr, "_get_medium_engine", lambda: retry_engine)
    lines = [
        {
            "text": text,
            "confidence": 0.99,
            "left": 0,
            "top": index * 12,
            "width": 80,
            "height": 10,
        }
        for index, text in enumerate(original)
    ]

    assert LocalRapidOCR._needs_unmasked_medium_bubble_retry(lines)
    assert not LocalRapidOCR._needs_unmasked_medium_bubble_retry([
        {"text": "I will talk with you tomorrow.", "confidence": 0.99},
    ])

    result = ocr._rescue_bubble_lines(
        np.full((60, 100, 3), 255, dtype=np.uint8),
        lines,
        np.zeros((80, 120, 3), dtype=np.uint8),
    )

    assert retry_engine.calls == 2
    assert [line["text"] for line in result] == corrected
    assert result[-1]["ocr_recovery_method"] == "unmasked_medium_bubble_crop"


