from types import SimpleNamespace

import numpy as np

import ocr_engine.comic_text_detector as detector_module


class FakeSession:
    def __init__(self):
        self.feed = None

    def get_inputs(self):
        return [SimpleNamespace(name="images"), SimpleNamespace(name="orig_target_sizes")]

    def run(self, _outputs, feed):
        self.feed = feed
        return (
            np.array([[2, 1, 0]], dtype=np.int64),
            np.array([[[64, 128, 320, 512], [20, 40, 100, 120], [1, 1, 4, 4]]], dtype=np.float32),
            np.array([[0.95, 0.8, 0.99]], dtype=np.float32),
        )


def test_detector_scales_boxes_to_source_pixels_and_normalizes_input(tmp_path, monkeypatch):
    model = tmp_path / "detector.onnx"
    model.write_bytes(b"mock model placeholder")
    session = FakeSession()
    monkeypatch.setattr(detector_module, "_get_session", lambda _path: session)
    detector = detector_module.ComicTextDetector(model_path=model)

    regions = detector.detect(np.full((640, 320, 3), 255, dtype=np.uint8))

    assert {item.label: item.bbox for item in regions} == {
        "text_free": (32, 128, 160, 512),
        "text_bubble": (10, 40, 50, 120),
    }
    assert session.feed["images"].shape == (1, 3, 640, 640)
    assert session.feed["images"].dtype == np.float32
    assert np.allclose(session.feed["images"], 1.0)
    assert session.feed["orig_target_sizes"].tolist() == [[640, 640]]


def test_tall_webtoon_tiles_overlap_and_cover_last_pixel():
    tiles = detector_module.ComicTextDetector._vertical_tiles(6000, 800)

    assert tiles[0][0] == 0
    assert tiles[-1][1] == 6000
    assert all(0 < bottom - top <= 1600 for top, bottom in tiles)
    assert all(next_top < bottom for (_, bottom), (next_top, _) in zip(tiles, tiles[1:]))


def test_same_class_tile_duplicates_are_removed_but_class_pairs_are_kept():
    regions = [
        detector_module.ComicTextRegion("text_free", 0.8, (10, 10, 50, 50)),
        detector_module.ComicTextRegion("text_free", 0.9, (11, 11, 51, 51)),
        detector_module.ComicTextRegion("text_bubble", 0.7, (10, 10, 50, 50)),
    ]

    result = detector_module.ComicTextDetector._deduplicate(regions)

    assert len(result) == 2
    assert any(item.label == "text_bubble" for item in result)
    assert next(item.confidence for item in result if item.label == "text_free") == 0.9
