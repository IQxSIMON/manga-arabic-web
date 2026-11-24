"""Local Qwen GGUF adapter backed by the CPU-only llama.cpp server."""

from __future__ import annotations

import os
import secrets
import subprocess
import threading
import time
from pathlib import Path

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = PROJECT_ROOT / "translator_engine" / "models" / "alternatives" / "qwen3" / "Qwen3-1.7B-Q8_0.gguf"
DEFAULT_PROOFREADER_MODEL = PROJECT_ROOT / "translator_engine" / "models" / "alternatives" / "qwen3-4b" / "Qwen3-4B-Q4_K_M.gguf"
DEFAULT_SERVER = PROJECT_ROOT / "runtime" / "llama.cpp" / "bin" / "llama-server.exe"


class LlamaCppTranslator:
    """Start one loopback-only llama.cpp process lazily and reuse it."""

    def __init__(self, model_path=None, server_path=None, port=None, startup_timeout=180):
        self.model_path = Path(model_path or os.getenv("MANGA_QWEN_MODEL", DEFAULT_MODEL))
        self.server_path = Path(server_path or os.getenv("MANGA_LLAMA_SERVER", DEFAULT_SERVER))
        self.port = int(port or os.getenv("MANGA_QWEN_PORT", "9931"))
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.startup_timeout = startup_timeout
        self._process = None
        self._lock = threading.Lock()
        self._api_key_path = self.server_path.parent / "qwen-api-key.txt"
        self._api_key = None

    def _get_api_key(self):
        if self._api_key:
            return self._api_key
        try:
            self._api_key = self._api_key_path.read_text(encoding="ascii").strip()
        except OSError:
            self._api_key = secrets.token_urlsafe(32)
            self._api_key_path.parent.mkdir(parents=True, exist_ok=True)
            self._api_key_path.write_text(self._api_key, encoding="ascii")
        if not self._api_key:
            self._api_key = secrets.token_urlsafe(32)
            self._api_key_path.write_text(self._api_key, encoding="ascii")
        return self._api_key

    def _headers(self):
        return {"Authorization": f"Bearer {self._get_api_key()}"}

    @staticmethod
    def available(model_path=None, server_path=None):
        model = Path(model_path or os.getenv("MANGA_QWEN_MODEL", DEFAULT_MODEL))
        server = Path(server_path or os.getenv("MANGA_LLAMA_SERVER", DEFAULT_SERVER))
        return model.is_file() and server.is_file()

    def _ensure_server(self):
        with self._lock:
            try:
                if requests.get(f"{self.base_url}/health", headers=self._headers(), timeout=1).ok:
                    return
            except requests.RequestException:
                pass
            if not self.server_path.is_file():
                raise RuntimeError(f"لم أعثر على llama-server.exe: {self.server_path}")
            if not self.model_path.is_file():
                raise RuntimeError(f"نموذج Qwen المحلي غير موجود: {self.model_path}")

            log_path = self.server_path.parent / "qwen-server.log"
            log_file = log_path.open("ab")
            args = [
                str(self.server_path), "--model", str(self.model_path),
                "--host", "127.0.0.1", "--port", str(self.port),
                "--ctx-size", "4096", "--threads", str(max(1, (os.cpu_count() or 4) - 1)),
                "--gpu-layers", "0", "--reasoning", "off", "--no-webui",
                "--api-key", self._get_api_key(),
            ]
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                self._process = subprocess.Popen(
                    args, cwd=str(self.server_path.parent), stdin=subprocess.DEVNULL,
                    stdout=log_file, stderr=subprocess.STDOUT, creationflags=flags,
                )
            finally:
                log_file.close()

            deadline = time.monotonic() + self.startup_timeout
            last_error = None
            while time.monotonic() < deadline:
                if self._process.poll() is not None:
                    tail = ""
                    try:
                        tail = log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
                    except OSError:
                        pass
                    raise RuntimeError(f"توقف llama.cpp أثناء تحميل Qwen: {tail}")
                try:
                    if requests.get(f"{self.base_url}/health", headers=self._headers(), timeout=1).ok:
                        return
                except requests.RequestException as exc:
                    last_error = exc
                time.sleep(0.5)
            raise RuntimeError(f"انتهت مهلة تحميل نموذج Qwen: {last_error}")

    @staticmethod
    def build_messages(text: str, context: str = "") -> list[dict[str, str]]:
        system = (
            "Translate English comic dialogue into concise, natural Modern Standard Arabic. "
            "Preserve the speaker's intent, emotion, idioms, and names. Use context only to resolve meaning. "
            "Do not add facts. Return only the Arabic translation, with no explanation or quotation marks."
        )
        user = ""
        if context.strip():
            user += "Previous dialogue for context (do not translate it again):\n" + context.strip()[-1200:] + "\n\n"
        user += "Translate this dialogue:\n" + text.strip() + "\n/no_think"
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def translate(self, text: str, context: str = "") -> str:
        text = " ".join(text.split()).strip()
        if not text:
            return ""
        self._ensure_server()
        response = requests.post(
            f"{self.base_url}/v1/chat/completions",
            json={
                "model": self.model_path.stem,
                "messages": self.build_messages(text, context),
                "temperature": 0.2,
                "top_p": 0.85,
                "max_tokens": min(192, max(64, len(text) * 2)),
                "stream": False,
            }, headers=self._headers(), timeout=180,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        content = content.replace("<think>", "").replace("</think>", "").strip()
        return " ".join(content.split())

    @staticmethod
    def build_proofread_messages(source: str, draft: str, context: str = "") -> list[dict[str, str]]:
        system = (
            "You are an English-Arabic comic dialogue proofreader. The English source is authoritative. "
            "Compare every meaning and idiom with the Arabic draft. Correct mistranslations and awkward Arabic, "
            "but preserve a draft that is already accurate and natural. Use concise Modern Standard Arabic "
            "that fits a speech bubble. Preserve names, emotion, and facts; never invent details. "
            "Return only corrected Arabic, without notes or quotation marks.\n"
            "Examples:\n"
            "You're dead meat. -> ستكون نهايتك!\n"
            "You have some nerve! -> يا لك من جريء!\n"
            "That's not what I meant! -> لم أقصد ذلك!\n"
            "Keep up the good work and continue progressing! -> أحسنت، واصل التقدم!"
        )
        user = "English source:\n" + source.strip() + "\n\nOPUS Arabic draft:\n" + draft.strip()
        if context.strip():
            user += "\n\nNearby dialogue for context (reference only):\n" + context.strip()[-900:]
        user += "\nCorrect the draft against the source. Return only Arabic. /no_think"
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def proofread(self, source: str, draft: str, context: str = "") -> str:
        """Post-edit an OPUS draft with the separately configured local Qwen model."""
        source = " ".join(source.split()).strip()
        draft = " ".join(draft.split()).strip()
        if not source or not draft:
            return draft
        self._ensure_server()
        response = requests.post(
            f"{self.base_url}/v1/chat/completions",
            json={
                "model": self.model_path.stem,
                "messages": self.build_proofread_messages(source, draft, context),
                "temperature": 0.05,
                "top_p": 0.75,
                "max_tokens": min(160, max(64, len(draft) * 2)),
                "stream": False,
            }, headers=self._headers(), timeout=240,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        content = content.replace("<think>", "").replace("</think>", "").strip()
        return " ".join(content.split()) or draft

    def shutdown(self):
        with self._lock:
            if self._process and self._process.poll() is None:
                self._process.terminate()
                try:
                    self._process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._process.kill()
            self._process = None
