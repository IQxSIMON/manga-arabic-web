import json

from translator_engine.benchmark_matrix import (
    CHALLENGE_CORPUS, _arabic_letter_ratio, build_report,
)


def test_challenge_corpus_covers_dialogue_and_external_narration():
    samples = json.loads(CHALLENGE_CORPUS.read_text(encoding="utf-8"))

    assert len(samples) >= 22
    assert {
        "Would Mom and Dad worry about me as much as he did?",
        "OBVIOUSLY.",
        "I SHOULD HEAD TO THE SHADOW DUNGEON NOW.",
    } <= {sample["source"] for sample in samples}
    assert all(sample.get("source") and sample.get("target") for sample in samples)
    assert all(sample["reference_status"] == "draft_needs_human_review" for sample in samples)
    assert {sample["region_type"] for sample in samples} == {
        "speech_bubble", "external_text",
    }
    assert {sample["category"] for sample in samples} >= {
        "idiom", "emotion", "long_dialogue", "fantasy_terminology", "narration",
    }


def test_benchmark_records_raw_outputs_context_latency_and_shutdown():
    seen = []

    class FakeEngine:
        model_name = "fixture model"

        def translate(self, source, context=""):
            seen.append((source, context))
            return "أحسنت، واصل التقدم!"

        def shutdown(self):
            seen.append(("shutdown", ""))

    report = build_report(
        [{"source": "Keep up the good work!", "target": "أحسنت، واصل التقدم!", "context": "Well done."}],
        runners=[("fixture", FakeEngine)],
    )
    engine = report["engines"][0]
    assert engine["status"] == "complete"
    assert engine["samples"][0]["translation"] == "أحسنت، واصل التقدم!"
    assert engine["samples"][0]["exact_reference_match"] is True
    assert engine["summary"]["arabic_output_count"] == 1
    assert engine["summary"]["first_request_seconds_including_startup"] == engine["samples"][0]["seconds"]
    assert seen[0][0] == "Keep up the good work!"
    assert "Well done." in seen[0][1]
    assert "Current region is dialogue inside a speech bubble" in seen[0][1]
    assert seen[1] == ("shutdown", "")


def test_benchmark_reports_progress_after_each_completed_sample():
    checkpoints = []

    class FastEngine:
        model_name = "fast fixture"

        def translate(self, source, context=""):
            return "مرحباً بك."

    report = build_report(
        [{"source": "Hello."}, {"source": "Welcome."}],
        runners=[("fixture", FastEngine)],
        on_sample=lambda engine_id, result, done, total, item: checkpoints.append(
            (engine_id, done, total, len(result["samples"]), item["source"])
        ),
    )

    assert report["engines"][0]["status"] == "complete"
    assert checkpoints == [
        ("fixture", 1, 2, 1, "Hello."),
        ("fixture", 2, 2, 2, "Welcome."),
    ]


def test_benchmark_keeps_unavailable_engines_visible():
    def unavailable():
        raise FileNotFoundError("model not installed")

    report = build_report([{"source": "Hello", "target": "مرحباً"}], [("new_engine", unavailable)])
    assert report["engines"][0]["status"] == "unavailable"
    assert "model not installed" in report["engines"][0]["error"]


def test_arabic_letter_ratio_is_only_a_script_sanity_check():
    assert _arabic_letter_ratio("مرحباً Hello") >= 0.5
    assert _arabic_letter_ratio("Hello there") == 0.0


def test_benchmark_preserves_inference_stage_timings_when_engine_exposes_them():
    class TimedEngine:
        model_name = "timed fixture"

        def __init__(self):
            self.last_inference_timings = None

        def translate(self, source, context=""):
            self.last_inference_timings = {
                "prompt_n": 120, "prompt_ms": 4000,
                "predicted_n": 32, "predicted_ms": 8000,
            }
            return "أحسنت، واصل التقدم!"

        def shutdown(self):
            pass

    report = build_report(
        [{"source": "Keep up the good work!", "target": "أحسنت، واصل التقدم!",
          "category": "idiom", "reference_status": "draft_needs_human_review"}],
        runners=[("timed_fixture", TimedEngine)],
    )

    sample = report["engines"][0]["samples"][0]
    assert sample["reference_status"] == "draft_needs_human_review"
    assert sample["category"] == "idiom"
    assert sample["inference_timings"]["prompt_tokens_per_second"] == 30.0
    assert sample["inference_timings"]["generated_tokens_per_second"] == 4.0
    assert report["engines"][0]["summary"]["mean_generated_tokens_per_second"] == 4.0
    assert report["engines"][0]["summary"]["mean_warm_request_seconds"] is None


def test_benchmark_warm_requests_exclude_the_first_attempt_even_if_it_failed():
    class FirstAttemptFails:
        model_name = "retry fixture"

        def __init__(self):
            self.calls = 0

        def translate(self, source, context=""):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("simulated cold-start failure")
            return "مرحباً بك."

        def shutdown(self):
            pass

    report = build_report(
        [{"source": "Hello."}, {"source": "Welcome."}],
        runners=[("retry_fixture", FirstAttemptFails)],
    )

    samples = report["engines"][0]["samples"]
    summary = report["engines"][0]["summary"]
    assert samples[0]["error"] == "RuntimeError: simulated cold-start failure"
    assert summary["first_request_seconds_including_startup"] == samples[0]["seconds"]
    assert summary["mean_warm_request_seconds"] == samples[1]["seconds"]
