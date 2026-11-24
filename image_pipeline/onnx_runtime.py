from __future__ import annotations

import logging
import os
from pathlib import Path


LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _setting(name: str, default: str) -> str:
    value = os.getenv(name)
    if value is not None:
        return value
    # Read only the requested nonsecret runtime setting from the local .env.
    env_path = PROJECT_ROOT / ".env"
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            key, separator, raw_value = line.partition("=")
            if separator and key.strip() == name:
                return raw_value.strip().strip("\"'")
    except OSError:
        pass
    return default


def create_session_options(
    ort, cpu_threads: int | None = None, allow_dml: bool = True,
):
    """Create ONNX Runtime options compatible with CPU and optional DirectML."""
    options = ort.SessionOptions()
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.inter_op_num_threads = 1
    available = set(ort.get_available_providers())
    requested = _setting("MANGA_ONNX_PROVIDER", "auto").strip().lower()
    use_dml = allow_dml and requested != "cpu" and "DmlExecutionProvider" in available
    if use_dml:
        options.enable_mem_pattern = False
        options.intra_op_num_threads = 1
    elif cpu_threads is not None:
        options.intra_op_num_threads = max(1, int(cpu_threads))
    return options


def session_providers(ort, allow_dml: bool = True):
    """Prefer DirectML when installed; keep CPU as the reliable fallback."""
    available = set(ort.get_available_providers())
    requested = _setting("MANGA_ONNX_PROVIDER", "auto").strip().lower()
    if allow_dml and requested != "cpu" and "DmlExecutionProvider" in available:
        try:
            device_id = int(_setting("MANGA_ONNX_DML_DEVICE_ID", "0"))
        except ValueError:
            device_id = 0
        return [("DmlExecutionProvider", {"device_id": device_id}), "CPUExecutionProvider"]
    return ["CPUExecutionProvider"]


def create_inference_session(
    ort, model_path, options, allow_dml: bool = True,
):
    """Create a session on DirectML when available, then retry safely on CPU."""
    providers = session_providers(ort, allow_dml=allow_dml)
    try:
        return ort.InferenceSession(
            str(model_path), sess_options=options, providers=providers
        )
    except Exception:
        if not providers or not isinstance(providers[0], tuple):
            raise
        LOGGER.warning(
            "DirectML could not initialize model %s; falling back to CPU.",
            Path(model_path).name,
        )
        fallback = ort.SessionOptions()
        fallback.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        fallback.intra_op_num_threads = max(1, int(os.getenv("MANGA_ORT_CPU_THREADS", "2")))
        fallback.inter_op_num_threads = 1
        return ort.InferenceSession(
            str(model_path), sess_options=fallback, providers=["CPUExecutionProvider"]
        )
