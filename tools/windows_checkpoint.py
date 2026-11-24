"""Windows project checkpoint utility; preserves prior state before updates."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_ROOT = Path(os.getenv("MANGA_BENCHMARK_DIR", str(PROJECT_ROOT.parent / "AI_Benchmark")))
STATE_FILE = "state.json"
PROGRESS_FILE = "PROGRESS.md"


def _git_value(*args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args], cwd=PROJECT_ROOT, check=True,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        return result.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unavailable"


def collect_state(previous: dict | None = None) -> dict:
    previous = previous or {}
    status_lines = [line for line in _git_value("status", "--short").splitlines() if line]
    tracked = _git_value("diff", "--name-only").splitlines()
    untracked = [line[3:] for line in status_lines if line.startswith("?? ")]
    partials = []
    models_root = BENCHMARK_ROOT / "models"
    if models_root.is_dir():
        for path in sorted(models_root.rglob("*")):
            if path.is_file() and (path.name.endswith(".part") or ".range" in path.name):
                stat = path.stat()
                partials.append({
                    "path": str(path.relative_to(BENCHMARK_ROOT)),
                    "size_bytes": stat.st_size,
                    "modified_utc": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                })
    state = dict(previous)
    state.update({
        "schema_version": 1,
        "updated_utc": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "project_root": str(PROJECT_ROOT),
        "benchmark_root": str(BENCHMARK_ROOT),
        "current_phase": previous.get("current_phase", "Windows development and resumability"),
        "git": {
            "branch": _git_value("branch", "--show-current"),
            "status": status_lines,
            "tracked_modified": sorted(set(tracked)),
            "untracked": sorted(untracked),
        },
        "benchmark_partial_files": partials,
    })
    return state


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_checkpoint(benchmark_root: Path, state: dict, progress_text: str | None = None) -> None:
    """Atomically update the live checkpoint while retaining timestamped copies."""
    benchmark_root = Path(benchmark_root)
    state_path = benchmark_root / STATE_FILE
    progress_path = benchmark_root / PROGRESS_FILE
    previous_exists = state_path.exists() or progress_path.exists()
    if previous_exists:
        history = benchmark_root / "checkpoints" / "history"
        history.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        if state_path.is_file():
            shutil.copy2(state_path, history / f"state-{stamp}.json")
        if progress_path.is_file():
            shutil.copy2(progress_path, history / f"PROGRESS-{stamp}.md")
    text = progress_text if progress_text is not None else render_progress(state)
    _atomic_write(state_path, json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    _atomic_write(progress_path, text.rstrip() + "\n")


def render_progress(state: dict) -> str:
    partials = state.get("benchmark_partial_files", [])
    partial_text = "\n".join(
        f"- `{item['path']}` — {item['size_bytes']} بايت (محفوظ؛ لم يُتحقق من اكتماله)."
        for item in partials
    ) or "- لا توجد ملفات تنزيل جزئية مرصودة."
    modified = state.get("git", {}).get("status", [])
    modified_text = "\n".join(f"- `{item}`" for item in modified) or "- شجرة العمل نظيفة."
    completed = "\n".join(f"- {item}" for item in state.get("completed", [])) or "- لا توجد عناصر مسجلة بعد."
    pending = "\n".join(f"- {item}" for item in state.get("pending", [])) or "- لا توجد عناصر معلقة مسجلة."
    return f"""# نقطة استئناف Windows

- آخر تحديث UTC: {state.get('updated_utc', 'غير متوفر')}
- المرحلة: {state.get('current_phase', 'غير محددة')}
- الفرع: `{state.get('git', {}).get('branch', 'غير متوفر')}`
- بيئة Python: {state.get('python', 'غير متوفرة')} — {state.get('platform', 'غير متوفر')}
- نتيجة آخر اختبار: {state.get('last_test_result', 'لم تُسجل')}
- آخر ملخص: {state.get('last_summary', 'لم يُسجل')}

## مكتمل
{completed}

## متبقٍ
{pending}

## حالة Git الحالية
{modified_text}

## ملفات النماذج الجزئية
{partial_text}

## طريقة الاستئناف
اقرأ `state.json` ثم افحص أحجام الملفات الجزئية. لا تستأنف تنزيل نموذج ولا تعِد تجميع ملف إلا بعد التحقق من نقطة الاستئناف وبصمة SHA-256 المسجلة. لا تستخدم عمليات Git مدمرة.
"""


def _load_state(path: Path) -> dict:
    if not path.is_file():
        raise SystemExit(f"Checkpoint not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="عرض نقطة الاستئناف دون تعديل الملفات")
    init = commands.add_parser("init", help="إنشاء أول checkpoint مع رفض الكتابة فوق الموجود")
    init.add_argument("--summary", default="تهيئة نقطة استئناف Windows")
    init.add_argument("--test-result", default="لم تُسجل")
    init.add_argument("--phase", default="Windows development and resumability")
    init.add_argument("--completed", action="append", default=[])
    init.add_argument("--pending", action="append", default=[])
    update = commands.add_parser("update", help="تحديث checkpoint مع حفظ نسخة تاريخية أولاً")
    update.add_argument("--summary", required=True)
    update.add_argument("--test-result", default=None)
    update.add_argument("--phase", default=None)
    update.add_argument("--completed", action="append", default=[])
    update.add_argument("--pending", action="append", default=[])
    update.add_argument("--reset-completed", action="store_true")
    update.add_argument("--reset-pending", action="store_true")
    args = parser.parse_args(argv)
    state_path = BENCHMARK_ROOT / STATE_FILE
    progress_path = BENCHMARK_ROOT / PROGRESS_FILE

    if args.command == "status":
        print(json.dumps(_load_state(state_path), ensure_ascii=False, indent=2))
        return 0
    if args.command == "init" and (state_path.exists() or progress_path.exists()):
        raise SystemExit("Checkpoint already exists; use 'update' to preserve a history copy first.")
    previous = {} if args.command == "init" else _load_state(state_path)
    state = collect_state(previous)
    state["last_summary"] = args.summary
    if args.command == "init":
        state["last_test_result"] = args.test_result
        state["current_phase"] = args.phase
        state["completed"] = list(args.completed)
        state["pending"] = list(args.pending)
        state["android_phase"] = "paused; preserve ARM64 compatibility"
        state["models"] = {
            "opus_onnx": "present in project; runtime tests determine current usability",
            "qwen3_5_9b": "partial download files present; checksum not verified; do not overwrite",
            "gemma_4_e4b": "not downloaded",
            "index_translate_9b": "not downloaded",
            "translategemma_4b": "not downloaded",
        }
    else:
        if args.test_result is not None:
            state["last_test_result"] = args.test_result
        if args.phase:
            state["current_phase"] = args.phase
        if args.reset_completed:
            state["completed"] = []
        if args.reset_pending:
            state["pending"] = []
        if args.completed:
            state["completed"] = list(dict.fromkeys([*state.get("completed", []), *args.completed]))
        if args.pending:
            state["pending"] = args.pending
    write_checkpoint(BENCHMARK_ROOT, state)
    print(PROGRESS_FILE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
