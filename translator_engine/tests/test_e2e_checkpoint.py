import json
from pathlib import Path

import pytest

from tools.resume_vertical_chapter_e2e import (
    apply_completion_metadata,
    failed_page_item,
    page_matches_current_recipe,
    save_checkpoint,
    saved_page_progress,
    saved_page_state,
)


def test_checkpoint_update_is_readable_and_replaces_prior_state(tmp_path: Path):
    checkpoint = tmp_path / "checkpoint.json"
    checkpoint.write_text('{"status":"old"}', encoding="utf-8")

    save_checkpoint(checkpoint, {"status": "in_progress", "pages": [1, 2]})

    assert json.loads(checkpoint.read_text(encoding="utf-8")) == {
        "status": "in_progress", "pages": [1, 2]
    }
    assert list(tmp_path.iterdir()) == [checkpoint]


def test_checkpoint_serialization_failure_preserves_previous_checkpoint(tmp_path: Path):
    checkpoint = tmp_path / "checkpoint.json"
    original = '{"status":"keep"}'
    checkpoint.write_text(original, encoding="utf-8")

    with pytest.raises(TypeError):
        save_checkpoint(checkpoint, {"not_json": object()})

    assert checkpoint.read_text(encoding="utf-8") == original
    assert list(tmp_path.iterdir()) == [checkpoint]


def test_saved_page_progress_reads_region_checkpoint_without_database(tmp_path: Path):
    result = tmp_path / "page-0004.json"
    result.write_text(json.dumps({
        "processing_status": "translation_in_progress",
        "translation_elapsed_seconds": 321.5,
        "progress": {"stage": "translation", "completed_regions": 2,
                     "total_regions": 11, "current_region": 3},
    }), encoding="utf-8")

    assert saved_page_progress(result) == {
        "stage": "translation_in_progress", "completed_regions": 2,
        "total_regions": 11, "current_region": 3,
        "translation_elapsed_seconds": 321.5,
    }


def test_saved_page_progress_ignores_missing_or_partially_written_files(tmp_path: Path):
    assert saved_page_progress(tmp_path / "missing.json") is None
    partial = tmp_path / "partial.json"
    partial.write_text('{"progress":', encoding="utf-8")
    assert saved_page_progress(partial) is None


def test_saved_page_state_exposes_translation_and_render_staleness(tmp_path: Path):
    result = tmp_path / "page-0009.json"
    result.write_text(json.dumps({
        "processing_status": "complete", "pipeline_version": 21,
        "translation_inputs_stale": True, "render_stale": True,
    }), encoding="utf-8")

    assert saved_page_state(result) == {
        "processing_status": "complete", "pipeline_version": 21,
        "translation_inputs_stale": True, "render_stale": True,
    }


def test_resume_requires_every_current_pipeline_and_translation_version():
    current = {
        "processing_status": "complete", "translation_http": 200, "render_http": 200,
        "pipeline_version": 19, "prompt_version": "index-v21",
        "context_version": 21, "generation_policy_version": "integrity-v8",
    }
    assert page_matches_current_recipe(current, 19, "index-v21", 21, "integrity-v8")
    for field in ("pipeline_version", "prompt_version", "context_version",
                  "generation_policy_version"):
        stale = {**current, field: "stale"}
        assert not page_matches_current_recipe(stale, 19, "index-v21", 21, "integrity-v8")

    failed_translation = {**current, "translation_http": 500}
    assert not page_matches_current_recipe(
        failed_translation, 19, "index-v21", 21, "integrity-v8"
    )


def test_failed_page_attempt_does_not_inherit_old_complete_or_render_status():
    previous = {
        "processing_status": "complete", "render_http": 200,
        "rendered_regions": 8, "skipped_regions": 0,
    }
    durable = {
        "processing_status": "translation_in_progress", "pipeline_version": 23,
        "translated_lines": [{"text": "CH 033", "translated": ""}],
        "progress": {"completed_regions": 0}, "translation_elapsed_seconds": 95.0,
    }

    result = failed_page_item(
        previous, 3, 500, 99.0, durable, {"error": "invalid model output"},
        23, "index-v24", 21, "integrity-v10",
    )

    assert result["processing_status"] == "translation_in_progress"
    assert result["translation_http"] == 500
    assert result["completed_regions"] == 0
    assert result["translation_seconds"] == 95.0
    assert result["prompt_version"] == "index-v24"
    assert "render_http" not in result
    assert "rendered_regions" not in result


def test_completion_records_current_recipe_even_when_a_page_needs_review():
    from app import PAGE_PIPELINE_VERSION, PAGE_TRANSLATION_CONTEXT_VERSION
    from translator_engine.adapters import GENERATION_POLICY_VERSION, INDEX_PROMPT_VERSION

    page = {
        "processing_status": "complete", "pipeline_version": PAGE_PIPELINE_VERSION,
        "prompt_version": INDEX_PROMPT_VERSION,
        "context_version": PAGE_TRANSLATION_CONTEXT_VERSION,
        "generation_policy_version": GENERATION_POLICY_VERSION,
        "render_http": 200, "skipped_regions": 1,
    }
    summary = {"prompt_version": "stale-old-version"}

    apply_completion_metadata(
        summary, {9: page}, 1, {"image_count": 1, "comic_info": True},
        PAGE_PIPELINE_VERSION, INDEX_PROMPT_VERSION,
        PAGE_TRANSLATION_CONTEXT_VERSION, GENERATION_POLICY_VERSION,
    )

    assert summary["status"] == "complete_with_warnings"
    assert summary["prompt_version"] == INDEX_PROMPT_VERSION
    assert summary["generation_policy_version"] == GENERATION_POLICY_VERSION
    assert summary["pages_verified"] is False
    assert summary["export_verified"] is True
    assert summary["review_pages"] == [{"page": 9, "skipped_regions": 1}]
