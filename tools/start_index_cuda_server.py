"""Start the official CUDA llama.cpp build for the existing Index model.

This keeps the CPU runtime untouched, reuses its local API key so an already
running Flask process can reconnect, and exits if port 9941 is occupied.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parents[1]
CUDA_SERVER = ROOT / "runtime" / "llama-cuda-b12199-test" / "bin" / "llama-server.exe"
MODEL = ROOT / "translator_engine" / "models" / "alternatives" / "index-translate" / "Index-Translate-9B.Q4_K_M.gguf"
CPU_KEY = ROOT / "runtime" / "llama.cpp" / "bin" / "index_translate_9b-api-key.txt"
CUDA_LOG = CUDA_SERVER.parent / "logs" / "index-translate-cuda-server.log"
PORT = 9941


def bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, ""))
    except (TypeError, ValueError):
        value = default
    return min(maximum, max(minimum, value))


# The 8-layer setting was benchmarked against 4 layers with the same Index
# weights and decoding policy; it was modestly faster while staying within the
# tested VRAM budget. Keep the environment override for other GPUs.
GPU_LAYERS = bounded_env_int("MANGA_LLAMA_GPU_LAYERS", 6, 0, 99)
PARALLEL_SLOTS = bounded_env_int("MANGA_LLAMA_PARALLEL", 2, 1, 4)
CONTEXT_SIZE = bounded_env_int("MANGA_LLAMA_CTX_SIZE", 3072, 512, 16384)


def main() -> int:
    if not CUDA_SERVER.is_file() or not MODEL.is_file() or not CPU_KEY.is_file():
        print("CUDA server, Index model, or existing local API key is missing.", file=sys.stderr)
        return 2

    api_key = CPU_KEY.read_text(encoding="ascii").strip()
    if not api_key:
        print("The local API key is empty.", file=sys.stderr)
        return 2

    device_probe = subprocess.run(
        [str(CUDA_SERVER), "--list-devices"], capture_output=True, text=True,
        timeout=30, check=False,
    )
    devices = device_probe.stdout + device_probe.stderr
    if device_probe.returncode != 0 or "CUDA0: NVIDIA GeForce 930MX" not in devices:
        print("The CUDA build did not detect the expected NVIDIA device:\n" + devices,
              file=sys.stderr)
        return 3

    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        response = requests.get(f"http://127.0.0.1:{PORT}/health", headers=headers, timeout=2)
    except requests.RequestException:
        response = None
    if response is not None and response.ok:
        print("A compatible llama.cpp server is already healthy on port 9941; leaving it untouched.")
        return 0
    if response is not None and response.status_code != 401:
        print("Port 9941 is occupied by a server that is not using the saved local API key.",
              file=sys.stderr)
        return 4

    CUDA_LOG.parent.mkdir(parents=True, exist_ok=True)
    # Use every detected core for this dedicated inference process. The
    # optional override keeps the setting easy to benchmark on other devices.
    threads = bounded_env_int(
        "MANGA_LLAMA_THREADS", min(8, max(1, os.cpu_count() or 4)), 1, 64
    )
    args = [
        str(CUDA_SERVER), "--model", str(MODEL),
        "--host", "127.0.0.1", "--port", str(PORT),
        "--ctx-size", str(CONTEXT_SIZE), "--threads", str(threads),
        "--threads-batch", str(threads), "--n-gpu-layers", str(GPU_LAYERS),
        "--device", "CUDA0", "--parallel", str(PARALLEL_SLOTS), "--jinja",
        "--api-key", api_key, "--no-webui",
    ]
    log = CUDA_LOG.open("ab")
    try:
        process = subprocess.Popen(
            args, cwd=str(CUDA_SERVER.parent), stdin=subprocess.DEVNULL,
            stdout=log, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    finally:
        log.close()

    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        try:
            response = requests.get(
                f"http://127.0.0.1:{PORT}/health", headers=headers, timeout=2,
            )
            if response.ok:
                print(f"Index CUDA server is healthy (PID {process.pid}, {GPU_LAYERS} GPU layers, "
                      f"{PARALLEL_SLOTS} parallel slots).")
                return 0
        except requests.RequestException:
            pass
        time.sleep(2)

    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    tail = CUDA_LOG.read_text(encoding="utf-8", errors="replace")[-6000:]
    print("CUDA Index server failed to become healthy. See log:\n" + tail, file=sys.stderr)
    return 5


if __name__ == "__main__":
    raise SystemExit(main())
