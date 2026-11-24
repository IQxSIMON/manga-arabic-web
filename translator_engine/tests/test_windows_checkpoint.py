import json
import os
import subprocess
import sys
from pathlib import Path

from tools.windows_checkpoint import write_checkpoint


def test_checkpoint_update_is_atomic_and_retains_previous_state(tmp_path):
    benchmark = tmp_path / "AI_Benchmark"
    first = {"schema_version": 1, "current_phase": "first", "completed": ["test 1"],
             "git": {"status": [" M app.py"]}, "last_test_result": "1 passed"}
    write_checkpoint(benchmark, first)
    state_path = benchmark / "state.json"
    progress_path = benchmark / "PROGRESS.md"
    assert json.loads(state_path.read_text(encoding="utf-8")) == first
    progress_text = progress_path.read_text(encoding="utf-8")
    assert "` M app.py`" in progress_text
    assert "[' M app.py']" not in progress_text

    second = {"schema_version": 1, "current_phase": "second", "completed": ["test 2"]}
    write_checkpoint(benchmark, second)
    backups = list((benchmark / "checkpoints" / "history").iterdir())
    assert len(backups) == 2
    assert any(path.name.startswith("state-") for path in backups)
    assert any(path.name.startswith("PROGRESS-") for path in backups)
    assert json.loads(state_path.read_text(encoding="utf-8")) == second


def test_page_checkpoint_survives_application_process_restart(tmp_path):
    project_root = Path(__file__).resolve().parents[2]
    library_dir = tmp_path / "restart-library"
    environment = os.environ.copy()
    environment["MANGA_LIBRARY_DIR"] = str(library_dir)
    checkpoint = {
        "processing_status": "translation_in_progress",
        "progress": {"stage": "translation", "completed_regions": 2, "total_regions": 4},
        "translated_lines": [
            {"region_id": "region-stable-1", "text": "First bubble", "translated": "\u0623\u0648\u0644 \u0641\u0642\u0627\u0639\u0629"},
            {"region_id": "region-stable-2", "text": "Second bubble", "translated": "\u062b\u0627\u0646\u064a \u0641\u0642\u0627\u0639\u0629"},
        ],
    }
    payload = json.dumps(checkpoint, ensure_ascii=True)
    writer = (
        "import json,sys,library_store; "
        "library_store.save_page_result('restart-chapter',1,json.loads(sys.argv[1]))"
    )
    subprocess.run(
        [sys.executable, "-c", writer, payload], cwd=project_root, env=environment,
        capture_output=True, text=True, check=True, timeout=15,
    )

    reader = (
        "import json,library_store; "
        "print(json.dumps(library_store.read_page_result('restart-chapter',1), "
        "ensure_ascii=True,sort_keys=True))"
    )
    restarted = subprocess.run(
        [sys.executable, "-c", reader], cwd=project_root, env=environment,
        capture_output=True, text=True, check=True, timeout=15,
    )

    assert json.loads(restarted.stdout) == checkpoint


def test_flask_page_resume_after_process_restart_reuses_saved_ocr_and_regions(tmp_path):
    project_root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment["MANGA_LIBRARY_DIR"] = str(tmp_path / "flask-restart-library")
    environment["MANGA_TRANSLATION_WORKERS"] = "1"
    first_process = r'''import io, json
from PIL import Image
import app as web_app

image = io.BytesIO()
Image.new("RGB", (160, 120), "white").save(image, format="PNG")
image.seek(0)
client = web_app.app.test_client()
created = client.post("/api/library/import", data={
    "title": "Restart recovery fixture", "reading_layout": "vertical",
    "pages": (image, "page.png"),
}, content_type="multipart/form-data")
assert created.status_code == 201, created.get_json()
chapter_id = created.get_json()["chapter"]["id"]
web_app.CURRENT_TRANSLATION_ENGINE = "index_translate_9b"
web_app.active_translation_recipe_signature = lambda: "restart-recovery-fixture"
web_app._page_translation_workers = lambda: 1
web_app._index_batch_size = lambda: 1
regions = [
    {"text": "FIRST SOURCE", "left": 8, "top": 8, "width": 60, "height": 20,
     "region_type": "speech_bubble"},
    {"text": "SECOND SOURCE", "left": 8, "top": 45, "width": 60, "height": 20,
     "region_type": "speech_bubble"},
]
web_app.run_local_ocr = lambda *_args, **_kwargs: {
    "text": "FIRST SOURCE\\nSECOND SOURCE", "lines": regions, "regions": regions,
    "provider": "restart-fixture", "elapsed_seconds": 0.01,
}
calls = []
def translate(text, **_kwargs):
    calls.append(text)
    if text == "SECOND SOURCE":
        raise RuntimeError("fixture interruption")
    return chr(0x0627) + chr(0x0644) + chr(0x0623) + chr(0x0648) + chr(0x0644)
web_app.translate_text = translate
response = client.post(f"/api/library/{chapter_id}/pages/1/translate")
saved = web_app.library_store.read_page_result(chapter_id, 1)
assert saved["processing_status"] == "translation_in_progress", saved
assert saved["translated_lines"][0]["translated"] == chr(0x0627) + chr(0x0644) + chr(0x0623) + chr(0x0648) + chr(0x0644), saved
assert not saved["translated_lines"][1]["translated"], saved
print(json.dumps({"chapter_id": chapter_id,
                  "region_id": saved["translated_lines"][0]["region_id"],
                  "status": saved["processing_status"]}, ensure_ascii=True))
'''
    first = subprocess.run(
        [sys.executable, "-c", first_process], cwd=project_root, env=environment,
        capture_output=True, text=True, timeout=30,
    )
    assert first.returncode == 0, first.stderr
    partial = json.loads(first.stdout.strip().splitlines()[-1])

    second_process = r'''import json,sys
import app as web_app

chapter_id, expected_region_id = sys.argv[1:]
web_app.CURRENT_TRANSLATION_ENGINE = "index_translate_9b"
web_app.active_translation_recipe_signature = lambda: "restart-recovery-fixture"
web_app._page_translation_workers = lambda: 1
web_app._index_batch_size = lambda: 1
def unexpected_ocr(*_args, **_kwargs):
    raise AssertionError("OCR must be reused from the saved checkpoint")
web_app.run_local_ocr = unexpected_ocr
calls = []
def translate(text, **_kwargs):
    calls.append(text)
    return chr(0x0627) + chr(0x0644) + chr(0x062b) + chr(0x0627) + chr(0x0646) + chr(0x064a)
web_app.translate_text = translate
response = web_app.app.test_client().post(
    f"/api/library/{chapter_id}/pages/1/translate")
assert response.status_code == 200, response.get_json()
result = response.get_json()
assert result["processing_status"] == "complete", result
lines = result["translated_lines"]
assert lines[0]["region_id"] == expected_region_id, lines
assert lines[0]["translated"] == chr(0x0627) + chr(0x0644) + chr(0x0623) + chr(0x0648) + chr(0x0644), lines
assert lines[1]["translated"] == chr(0x0627) + chr(0x0644) + chr(0x062b) + chr(0x0627) + chr(0x0646) + chr(0x064a), lines
assert calls == ["SECOND SOURCE"], calls
print(json.dumps({"status": result["processing_status"],
                  "completed": result["progress"]["completed_regions"],
                  "retried_texts": calls}, ensure_ascii=True))
'''
    resumed = subprocess.run(
        [sys.executable, "-c", second_process,
         partial["chapter_id"], partial["region_id"]],
        cwd=project_root, env=environment, capture_output=True,
        text=True, timeout=30,
    )
    assert resumed.returncode == 0, resumed.stderr
    evidence = json.loads(resumed.stdout.strip().splitlines()[-1])

    assert evidence == {
        "status": "complete", "completed": 2, "retried_texts": ["SECOND SOURCE"]
    }

