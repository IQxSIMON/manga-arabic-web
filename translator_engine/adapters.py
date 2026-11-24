"""Local translation adapters and the balanced automatic engine router."""

from __future__ import annotations

import os
import logging
import re
import secrets
import hashlib
import math
import subprocess
import threading
import time
from difflib import SequenceMatcher
from collections import OrderedDict
from pathlib import Path

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOGGER = logging.getLogger(__name__)
LLAMA_SERVER = Path(os.getenv(
    "MANGA_LLAMA_SERVER",
    str(PROJECT_ROOT / "runtime" / "llama.cpp" / "bin" / "llama-server.exe"),
))
INDEX_MODEL = Path(os.getenv(
    "MANGA_INDEX_MODEL",
    str(PROJECT_ROOT / "translator_engine" / "models" / "alternatives" / "index-translate" / "Index-Translate-9B.Q4_K_M.gguf"),
))
TRANSLATEGEMMA_MODEL = Path(os.getenv(
    "MANGA_TRANSLATEGEMMA_MODEL",
    str(PROJECT_ROOT / "translator_engine" / "models" / "alternatives" / "translategemma" / "translategemma-4b-it.Q4_K_M.gguf"),
))
INDEX_PROMPT_VERSION = "index-vertical-comics-en-ar-context-glossary-v25-natural-gender-safe"
INDEX_BATCH_PROMPT_VERSION = "index-numbered-webtoon-batch-v2-natural-gender-safe"
TRANSLATEGEMMA_PROMPT_VERSION = "translategemma-llama-template-en-ar-v2"
GENERATION_POLICY_VERSION = "dialogue-token-budget-arabic-output-integrity-v11-script-guard"
_CHUNK_TRANSLATION_CACHE = OrderedDict()
_CHUNK_TRANSLATION_CACHE_MAX_BYTES = 8 * 1024 * 1024
_CHUNK_TRANSLATION_CACHE_MAX_ENTRIES = 512
_CHUNK_TRANSLATION_CACHE_BYTES = 0
_CHUNK_TRANSLATION_CACHE_LOCK = threading.RLock()


def _positive_int_env(name: str, default: int, minimum: int = 1, maximum: int = 65536) -> int:
    """Read a bounded positive integer setting without making startup fragile."""
    try:
        value = int(os.getenv(name, "").strip())
    except (TypeError, ValueError):
        return default
    return min(maximum, max(minimum, value))


def _configured_llama_device_args() -> list[str]:
    """Return an optional, argument-safe llama.cpp backend device selection."""
    device = os.getenv("MANGA_LLAMA_DEVICE", "").strip()
    if not device:
        return []
    if not re.fullmatch(r"[A-Za-z0-9_,:-]{1,128}", device):
        LOGGER.warning("Ignoring invalid MANGA_LLAMA_DEVICE value")
        return []
    return ["--device", device]


def _configured_llama_thread_counts() -> tuple[int, int]:
    """Use all reported CPU cores by default, bounded to keep the app responsive."""
    default_threads = min(8, max(1, os.cpu_count() or 4))
    threads = _positive_int_env("MANGA_LLAMA_THREADS", default_threads, maximum=64)
    batch_threads = _positive_int_env("MANGA_LLAMA_THREADS_BATCH", threads, maximum=64)
    return threads, batch_threads


class TranslationOutputError(RuntimeError):
    """A request produced incomplete or invalid text while the model stayed healthy."""


def has_arabic_letters(text: str) -> bool:
    """Return true only when output contains at least one Arabic-script letter."""
    return any("\u0600" <= character <= "\u06ff" and character.isalpha()
               for character in str(text))


_FOREIGN_TRANSLATION_SCRIPT_RANGES = (
    (0x0370, 0x03FF),  # Greek
    (0x0400, 0x052F),  # Cyrillic
    (0x0590, 0x05FF),  # Hebrew
    (0x0900, 0x097F),  # Devanagari
    (0x10A0, 0x10FF),  # Georgian
    (0x3040, 0x30FF),  # Japanese kana
    (0x3400, 0x9FFF),  # CJK
)
_NON_ARABIC_VARIANT_LETTERS = frozenset("پچژگکی")


def _has_foreign_script_contamination(text: str) -> bool:
    """Detect non-Arabic letters that can leak into an Arabic translation."""
    for character in str(text or ""):
        if not character.isalpha():
            continue
        codepoint = ord(character)
        if character in _NON_ARABIC_VARIANT_LETTERS or any(
            start <= codepoint <= end for start, end in _FOREIGN_TRANSLATION_SCRIPT_RANGES
        ):
            return True
    return False


def _is_usable_arabic_translation(text: str) -> bool:
    """Reject corrupted output and words leaked from other writing systems."""
    value = str(text or "")
    return (
        has_arabic_letters(value)
        and "\ufffd" not in value
        and not _has_foreign_script_contamination(value)
    )


def _is_implausibly_expanded_translation(source: str, translated: str) -> bool:
    """Flag context leakage where OCR text receives unrelated extra dialogue."""
    source_length = len(" ".join(str(source or "").split()))
    translated_length = len(" ".join(str(translated or "").split()))
    return translated_length > max(80, source_length * 2.5)


def _has_latin_residue(text: str) -> bool:
    """Reject untranslated Latin fragments embedded in Arabic output."""
    return bool(re.search(r"[A-Za-z]{2,}", str(text or "")))


def _repair_latinized_source_names(source: str, translated: str) -> str | None:
    """Phonetically Arabicize a mangled Latin name only when it matches the source."""
    source_names = [
        token for token in re.findall(r"\b[A-Z][A-Z'-]{2,}\b", str(source or ""))
        if re.search(r"[AEIOUY]", token) and token not in {
            "THE", "AND", "BUT", "FOR", "NOT", "YOU", "ARE", "WAS", "HAS", "HIS",
            "HER", "THIS", "THAT", "WITH", "FROM", "HAVE", "WILL", "WHAT", "WHEN",
            "JUST", "THEN", "YOUR", "THEY", "SHE", "HIM", "HERS", "CAN", "DID",
        }
    ]
    if not source_names:
        return None
    digraphs = {"sh": "ش", "ch": "تش", "th": "ث", "kh": "خ", "gh": "غ",
                "ph": "ف", "oo": "و", "ee": "ي", "ai": "اي", "ay": "اي",
                "ou": "و", "ow": "او", "ea": "ي"}
    letters = {"a": "ا", "b": "ب", "c": "ك", "d": "د", "e": "ي", "f": "ف",
               "g": "غ", "h": "ه", "i": "ي", "j": "ج", "k": "ك", "l": "ل",
               "m": "م", "n": "ن", "o": "و", "p": "ب", "q": "ق", "r": "ر",
               "s": "س", "t": "ت", "u": "و", "v": "ف", "w": "و", "x": "كس",
               "y": "ي", "z": "ز"}

    def transliterate(token: str) -> str:
        value = token.casefold()
        output = []
        index = 0
        while index < len(value):
            pair = value[index:index + 2]
            if pair in digraphs:
                output.append(digraphs[pair])
                index += 2
            else:
                output.append(letters.get(value[index], ""))
                index += 1
        return "".join(output)

    value = str(translated or "")
    # Do not require word boundaries: a model can glue an English token to a
    # neighboring Arabic word, which still counts as Latin residue.
    residues = list(re.finditer(r"[A-Za-z]{2,}", value))
    if not residues:
        return None
    replacements = []
    for residue in residues:
        residue_text = residue.group().casefold()
        candidate = next(
            (name for name in source_names if name.casefold().endswith(residue_text)),
            None,
        ) or max(source_names, key=lambda name: SequenceMatcher(
            None, residue_text, name.casefold()
        ).ratio())
        similarity = SequenceMatcher(None, residue.group().casefold(), candidate.casefold()).ratio()
        if similarity < 0.55 and not candidate.casefold().endswith(residue.group().casefold()):
            return None
        replacements.append((residue.span(), transliterate(candidate)))
    for (start, end), replacement in reversed(replacements):
        value = value[:start] + replacement + value[end:]
    return value


def clean_translation_output(text: str) -> str:
    """Remove common model formatting wrappers without rewriting its translation."""
    value = str(text or "").strip()
    if value.startswith("```"):
        value = value[3:].strip()
        if value.endswith("```"):
            value = value[:-3].strip()
        value = re.sub(r"^(?:arabic|text|markdown)\s*\n", "", value, flags=re.IGNORECASE)
    value = " ".join(value.split()).strip()
    value = re.sub(
        r"^(?:(?:arabic(?:\s+(?:translation|rendering))?|"
        r"الترجمة(?:\s+العربية)?(?:\s+هي)?|阿拉伯语翻译)\s*[:：\-]\s*)+",
        "", value, flags=re.IGNORECASE,
    )
    return " ".join(value.split()).strip()


def split_long_translation_text(text: str, max_chars: int = 180) -> list[str]:
    """Split only oversized dialogue, preserving sentence punctuation and word order."""
    sentences = re.split(r"(?<=[.!?])\s+", " ".join(str(text).split()))
    units: list[str] = []
    for sentence in sentences:
        if len(sentence) <= max_chars:
            if sentence:
                units.append(sentence)
            continue
        current = ""
        for word in sentence.split():
            if current and len(current) + len(word) + 1 > max_chars:
                units.append(current)
                current = word
            else:
                current = f"{current} {word}".strip()
        if current:
            units.append(current)

    chunks: list[str] = []
    current = ""
    for unit in units:
        if current and len(current) + len(unit) + 1 > max_chars:
            chunks.append(current)
            current = unit
        else:
            current = f"{current} {unit}".strip()
    if current:
        chunks.append(current)
    return chunks


def _chunk_cache_key(engine_id: str, model_path: Path, text: str, context: str) -> str:
    recipe = (INDEX_PROMPT_VERSION if engine_id == "index_translate_9b" else
              TRANSLATEGEMMA_PROMPT_VERSION if engine_id == "translategemma_4b" else "legacy")
    try:
        model_stat = model_path.stat()
        model_identity = f"{model_path}|{model_stat.st_size}|{model_stat.st_mtime_ns}"
    except OSError:
        model_identity = str(model_path)
    material = "\0".join((engine_id, model_identity, recipe, GENERATION_POLICY_VERSION,
                           text, context))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _get_chunk_translation(key: str) -> str | None:
    with _CHUNK_TRANSLATION_CACHE_LOCK:
        result = _CHUNK_TRANSLATION_CACHE.get(key)
        if result is not None:
            _CHUNK_TRANSLATION_CACHE.move_to_end(key)
        return result


def _cache_chunk_translation(key: str, translated: str) -> None:
    global _CHUNK_TRANSLATION_CACHE_BYTES
    result_size = len(translated.encode("utf-8"))
    if not translated or result_size > _CHUNK_TRANSLATION_CACHE_MAX_BYTES:
        return
    with _CHUNK_TRANSLATION_CACHE_LOCK:
        previous = _CHUNK_TRANSLATION_CACHE.pop(key, None)
        if previous is not None:
            _CHUNK_TRANSLATION_CACHE_BYTES -= len(previous.encode("utf-8"))
        _CHUNK_TRANSLATION_CACHE[key] = translated
        _CHUNK_TRANSLATION_CACHE_BYTES += result_size
        while (_CHUNK_TRANSLATION_CACHE_BYTES > _CHUNK_TRANSLATION_CACHE_MAX_BYTES
               or len(_CHUNK_TRANSLATION_CACHE) > _CHUNK_TRANSLATION_CACHE_MAX_ENTRIES):
            _old_key, old_value = _CHUNK_TRANSLATION_CACHE.popitem(last=False)
            _CHUNK_TRANSLATION_CACHE_BYTES -= len(old_value.encode("utf-8"))


class LocalLlamaTranslationAdapter:
    """One GGUF model over a private loopback llama.cpp server."""

    engine_id = "local_llama"
    model_path = Path()
    port = 9941

    def __init__(self, model_path: Path | None = None, server_path: Path | None = None,
                 port: int | None = None, startup_timeout: int = 300):
        if model_path is not None:
            self.model_path = Path(model_path)
        else:
            self.model_path = Path(type(self).model_path)
        self.server_path = Path(server_path or LLAMA_SERVER)
        self.port = int(port or self.port)
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.startup_timeout = startup_timeout
        self._process: subprocess.Popen | None = None
        self._loaded_model_identity: tuple[str, int, int] | None = None
        self._api_key_path = self.server_path.parent / f"{self.engine_id}-api-key.txt"
        self._api_key: str | None = None
        self._log_handle = None
        self._lock = threading.RLock()
        # Reuse loopback HTTP keep-alive connections across bubble requests on
        # the same worker thread. Each thread gets its own Session because
        # requests.Session is not a shared concurrent client.
        self._http_local = threading.local()
        # llama.cpp includes prompt-evaluation and token-generation timings in
        # each response. Keep the latest values for diagnosis without changing
        # the prompt, decoding policy, or returned translation.
        self.last_inference_timings: dict[str, float | int] | None = None
        # The Flask layer may inject persistent-cache callbacks. The adapter
        # remains usable independently when no library database is configured.
        self.chunk_cache_load = None
        self.chunk_cache_save = None

    def _http_session(self) -> requests.Session:
        session = getattr(self._http_local, "session", None)
        if session is None:
            session = requests.Session()
            self._http_local.session = session
        return session

    @classmethod
    def available(cls, model_path: Path | None = None, server_path: Path | None = None) -> bool:
        model = Path(model_path) if model_path is not None else Path(cls.model_path)
        server = Path(server_path) if server_path is not None else LLAMA_SERVER
        return model.is_file() and server.is_file()

    @staticmethod
    def build_messages(text: str, context: str = "") -> list[dict]:
        raise NotImplementedError

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._get_api_key()}"}

    def _get_api_key(self) -> str:
        """Reuse one private key across Flask restarts and same-project workers."""
        if self._api_key:
            return self._api_key
        self._api_key_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._api_key_path.open("x", encoding="ascii") as key_file:
                self._api_key = secrets.token_urlsafe(32)
                key_file.write(self._api_key)
        except FileExistsError:
            # Another worker may have created the file moments ago; allow its
            # short write to finish rather than generating a competing key.
            for _ in range(20):
                try:
                    self._api_key = self._api_key_path.read_text(encoding="ascii").strip()
                except OSError:
                    self._api_key = ""
                if self._api_key:
                    break
                time.sleep(0.05)
        if not self._api_key or not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", self._api_key):
            raise RuntimeError(f"تعذر قراءة مفتاح خادم Index المحلي من {self._api_key_path}.")
        return self._api_key

    def _server_accepts_api_key(self) -> bool:
        """Detect a compatible already-running local server without trusting health alone."""
        try:
            response = self._http_session().get(
                f"{self.base_url}/v1/models", headers=self._headers(), timeout=1,
            )
        except requests.RequestException:
            return False
        if getattr(response, "status_code", None) in {401, 403}:
            raise RuntimeError(
                f"خادم llama.cpp الموجود على المنفذ {self.port} يستخدم مفتاحاً قديماً. "
                "أوقف نسخة الخادم القديمة ثم أعد تشغيل التطبيق مرة واحدة؛ سيُعاد تشغيل Index بمفتاح محلي ثابت."
            )
        if response.status_code == 404:
            return False
        response.raise_for_status()
        return True

    def diagnose_server(self) -> dict:
        """Probe the loopback OpenAI-compatible endpoint without starting a model."""
        headers = {}
        try:
            key = self._api_key
            if not key and self._api_key_path.is_file():
                key = self._api_key_path.read_text(encoding="ascii").strip()
            if key:
                if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", key):
                    return {
                        "status": "invalid_local_key",
                        "message": "ملف مفتاح الخادم المحلي غير صالح؛ أعد تشغيل التطبيق لإصلاحه.",
                    }
                headers = {"Authorization": f"Bearer {key}"}
        except OSError as exc:
            return {
                "status": "local_key_unreadable",
                "message": f"تعذرت قراءة إعداد المصادقة المحلي: {exc}",
            }

        try:
            response = self._http_session().get(
                f"{self.base_url}/v1/models", headers=headers, timeout=2,
            )
        except requests.ConnectionError:
            return {
                "status": "not_running",
                "message": "خادم Index المحلي غير مشغّل الآن؛ سيبدأ عند أول ترجمة.",
            }
        except requests.Timeout:
            return {
                "status": "timeout",
                "message": "خادم Index المحلي لم يستجب خلال ثانيتين.",
            }
        except requests.RequestException as exc:
            return {
                "status": "request_error",
                "message": f"تعذر فحص خادم Index المحلي: {type(exc).__name__}.",
            }

        if response.status_code in {401, 403}:
            return {
                "status": "authentication_error",
                "message": (
                    f"رفض خادم المنفذ {self.port} مفتاح المصادقة المحلي. "
                    "أوقف نسخة llama-server القديمة ثم أعد تشغيل التطبيق."
                ),
            }
        if response.status_code == 404:
            return {
                "status": "incompatible_endpoint",
                "message": f"الخدمة على المنفذ {self.port} لا توفر واجهة /v1/models المتوافقة.",
            }
        if not 200 <= response.status_code < 300:
            return {
                "status": "server_error",
                "message": f"أعاد خادم المنفذ {self.port} الحالة HTTP {response.status_code}.",
            }
        try:
            payload = response.json()
        except (ValueError, AttributeError):
            payload = None
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            return {
                "status": "incompatible_response",
                "message": "استجاب المنفذ المحلي، لكن صيغة قائمة النماذج غير متوافقة مع llama.cpp.",
            }
        return {
            "status": "ready",
            "message": "خادم Index المحلي متصل وواجهة النماذج تستجيب.",
            "model_count": len(payload["data"]),
        }

    def _post_chat_completion(self, payload: dict, timeout: int = 1800):
        response = self._http_session().post(
            f"{self.base_url}/v1/chat/completions", json=payload,
            headers=self._headers(), timeout=timeout,
        )
        if response.status_code in {401, 403}:
            raise RuntimeError(
                f"رفض خادم Index المحلي المصادقة على المنفذ {self.port}. "
                "أوقف نسخة الخادم القديمة ثم أعد تشغيل التطبيق."
            )
        response.raise_for_status()
        return response

    def _record_inference_timings(self, result: dict) -> None:
        """Log llama.cpp's own timing counters to locate slow translation stages."""
        raw = result.get("timings") if isinstance(result, dict) else None
        if not isinstance(raw, dict):
            self.last_inference_timings = None
            return
        metrics: dict[str, float | int] = {}
        for key in ("prompt_n", "prompt_ms", "predicted_n", "predicted_ms", "cache_n"):
            value = raw.get(key)
            if (isinstance(value, (int, float)) and not isinstance(value, bool)
                    and math.isfinite(value) and value >= 0):
                metrics[key] = value
        usage = result.get("usage", {}) if isinstance(result, dict) else {}
        token_details = usage.get("prompt_tokens_details", {}) if isinstance(usage, dict) else {}
        cached_tokens = token_details.get("cached_tokens") if isinstance(token_details, dict) else None
        if isinstance(cached_tokens, (int, float)) and not isinstance(cached_tokens, bool):
            metrics["cached_tokens"] = max(0, int(cached_tokens))
        if not metrics:
            self.last_inference_timings = None
            return
        self.last_inference_timings = metrics
        prompt_rate = (
            metrics["prompt_n"] * 1000 / metrics["prompt_ms"]
            if metrics.get("prompt_n") and metrics.get("prompt_ms") else None
        )
        generation_rate = (
            metrics["predicted_n"] * 1000 / metrics["predicted_ms"]
            if metrics.get("predicted_n") and metrics.get("predicted_ms") else None
        )
        LOGGER.info(
            "llama.cpp inference engine=%s prompt_tokens=%s cached_prompt_tokens=%s cache_n=%s "
            "prompt_ms=%s prompt_tokens_per_sec=%s "
            "generated_tokens=%s generation_ms=%s generated_tokens_per_sec=%s",
            self.engine_id, metrics.get("prompt_n"), metrics.get("cached_tokens"),
            metrics.get("cache_n"), metrics.get("prompt_ms"),
            round(prompt_rate, 2) if prompt_rate is not None else None,
            metrics.get("predicted_n"), metrics.get("predicted_ms"),
            round(generation_rate, 2) if generation_rate is not None else None,
        )

    def _model_file_identity(self) -> tuple[str, int, int]:
        stat = self.model_path.stat()
        return str(self.model_path.resolve()), stat.st_size, stat.st_mtime_ns

    def _ensure_server(self) -> None:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                try:
                    current_identity = self._model_file_identity()
                except OSError:
                    current_identity = None
                if current_identity == self._loaded_model_identity:
                    return
                LOGGER.info("Restarting %s after its model file changed", self.engine_id)
                self.shutdown()
            if not self.server_path.is_file():
                raise RuntimeError(f"تعذر العثور على llama-server: {self.server_path}")
            if not self.model_path.is_file():
                raise RuntimeError(f"ملف النموذج غير موجود: {self.model_path}")
            model_identity = self._model_file_identity()

            # Health endpoints are often intentionally unauthenticated. Check
            # the authenticated OpenAI endpoint before reusing an existing
            # process or attempting to bind a second server to the same port.
            if self._server_accepts_api_key():
                self._loaded_model_identity = model_identity
                return

            log_dir = self.server_path.parent / "logs"
            log_dir.mkdir(exist_ok=True)
            log_path = log_dir / f"{self.engine_id}-server.log"
            self._log_handle = log_path.open("ab")
            threads, batch_threads = _configured_llama_thread_counts()
            context_size = _positive_int_env(
                "MANGA_LLAMA_CTX_SIZE", 4096, minimum=512, maximum=16384
            )
            parallel = _positive_int_env(
                "MANGA_LLAMA_PARALLEL", 2, minimum=1, maximum=4
            )
            # Keep CPU as the stable default. A GPU-capable llama.cpp build
            # may opt into layer offload for later measured comparisons.
            gpu_layers = _positive_int_env(
                "MANGA_LLAMA_GPU_LAYERS", 0, minimum=0, maximum=99
            )
            args = [
                str(self.server_path), "--model", str(self.model_path),
                "--host", "127.0.0.1", "--port", str(self.port),
                "--ctx-size", str(context_size), "--threads", str(threads),
                "--threads-batch", str(batch_threads),
                "--n-gpu-layers", str(gpu_layers), "--parallel", str(parallel), "--jinja",
                "--api-key", self._api_key, "--no-webui",
            ]
            args.extend(_configured_llama_device_args())
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                self._process = subprocess.Popen(
                    args, cwd=str(self.server_path.parent), stdin=subprocess.DEVNULL,
                    stdout=self._log_handle, stderr=subprocess.STDOUT, creationflags=flags,
                )
            finally:
                self._log_handle.close()
                self._log_handle = None

            deadline = time.monotonic() + self.startup_timeout
            last_error = None
            while time.monotonic() < deadline:
                if self._process.poll() is not None:
                    tail = ""
                    try:
                        tail = log_path.read_text(encoding="utf-8", errors="replace")[-1500:]
                    except OSError:
                        pass
                    raise RuntimeError(f"توقف llama.cpp أثناء تحميل {self.engine_id}: {tail}")
                try:
                    if self._http_session().get(f"{self.base_url}/health", timeout=2).ok:
                        if self._server_accepts_api_key():
                            self._loaded_model_identity = model_identity
                            return
                except requests.RequestException as exc:
                    last_error = exc
                time.sleep(0.5)
            raise RuntimeError(f"انتهت مهلة تحميل {self.engine_id}: {last_error}")

    def translate(self, text: str, context: str = "") -> str:
        text = " ".join(text.split()).strip()
        if not text:
            return ""
        self._ensure_server()
        output_budget = min(512, max(64, len(text.split()) * 4 + 24))
        def translate_chunks():
            chunks = split_long_translation_text(text)
            if len(chunks) <= 1:
                return None
            outputs = []
            prior_source = []
            for chunk in chunks:
                chunk_context = context
                if prior_source:
                    prior_text = " ".join(prior_source)[-500:]
                    chunk_context = "\n\n".join(part for part in (
                        str(context).strip(),
                        "Earlier source from this same dialogue, for context only; do not translate it again:\n" + prior_text,
                    ) if part)
                cache_key = _chunk_cache_key(
                    self.engine_id, self.model_path, chunk, chunk_context
                )
                translated_chunk = _get_chunk_translation(cache_key)
                if translated_chunk is not None and not _is_usable_arabic_translation(translated_chunk):
                    translated_chunk = None
                if translated_chunk is None:
                    if callable(self.chunk_cache_load):
                        try:
                            translated_chunk = self.chunk_cache_load(cache_key)
                        except Exception as exc:
                            LOGGER.warning("Persistent chunk-cache read failed: %s", exc)
                    if translated_chunk and _is_usable_arabic_translation(translated_chunk):
                        _cache_chunk_translation(cache_key, translated_chunk)
                    else:
                        translated_chunk = self.translate(chunk, context=chunk_context)
                        _cache_chunk_translation(cache_key, translated_chunk)
                        if callable(self.chunk_cache_save):
                            try:
                                self.chunk_cache_save(cache_key, translated_chunk)
                            except Exception as exc:
                                LOGGER.warning("Persistent chunk-cache write failed: %s", exc)
                outputs.append(translated_chunk)
                prior_source.append(chunk)
            return clean_translation_output(" ".join(outputs))
        payload = {
            "model": self.model_path.stem,
            "messages": self.build_messages(text, context),
            "temperature": 0,
            "top_p": 1,
            # Arabic may need more output tokens than the English source,
            # especially when an OCR group contains several lines. Scale the
            # ceiling by words so a long speech is not silently cut at 192.
            "max_tokens": output_budget,
            "stream": False,
        }
        if self.engine_id == "index_translate_9b":
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        elif self.engine_id == "translategemma_4b":
            # llama.cpp's TranslateGemma template reads language codes from
            # template kwargs; extra fields on OpenAI content parts can be
            # normalized away by the server before the template sees them.
            payload["chat_template_kwargs"] = {
                "source_lang_code": "en", "target_lang_code": "ar",
            }
        response = self._post_chat_completion(payload)
        result = response.json()
        self._record_inference_timings(result)
        choice_data = result["choices"][0]
        # Long, noisy OCR fragments can trigger repetitive generation. Retrying
        # them with a 512-token ceiling multiplies latency without improving the
        # source text. For short Index inputs, make one bounded, context-free
        # recovery attempt instead; ordinary translations keep their existing
        # generation budget and retry policy.
        short_index_source = self.engine_id == "index_translate_9b" and len(text) <= 128
        output_limit_retry = (
            short_index_source and choice_data.get("finish_reason") == "length"
        )
        retry_without_context = output_limit_retry
        if (choice_data.get("finish_reason") == "length" and output_budget < 512
                and not retry_without_context):
            payload["max_tokens"] = 512
            response = self._post_chat_completion(payload)
            result = response.json()
            self._record_inference_timings(result)
            choice_data = result["choices"][0]
        if choice_data.get("finish_reason") == "length" and not retry_without_context:
            chunked = translate_chunks()
            if chunked is not None:
                return chunked
            raise TranslationOutputError("Translation reached the output limit; review or split the source.")

        choice = choice_data["message"]["content"]
        if isinstance(choice, list):
            choice = "".join(item.get("text", "") for item in choice if isinstance(item, dict))
        translated = clean_translation_output(choice)
        valid_arabic = _is_usable_arabic_translation(translated)
        if self.engine_id == "index_translate_9b":
            if (valid_arabic and _has_latin_residue(translated)
                    and not _is_implausibly_expanded_translation(text, translated)):
                repaired = _repair_latinized_source_names(text, translated)
                if repaired and not _has_latin_residue(repaired):
                    translated = repaired
            retry_without_context = retry_without_context or not valid_arabic or (
                _is_implausibly_expanded_translation(text, translated)
                or _has_latin_residue(translated)
            )
        if retry_without_context:
            # Long context can cause a short OCR fragment to echo an earlier
            # speech bubble. Retry that anomaly with the exact source only.
            retry_payload = dict(payload)
            retry_payload["messages"] = self.build_messages(text, "")
            retry_payload["messages"][-1]["content"] += (
                "\n\nThe prior output was invalid or much longer than this source. "
                "Translate only the exact current source above. Do not add any "
                "dialogue, facts, explanation, or missing words. Write names and "
                "all translated words in Arabic script; do not leave Latin words."
            )
            # Exception path only: give the model ample room to finish Arabic
            # script and natural phrasing for short OCR fragments.
            retry_payload["max_tokens"] = 128
            for retry_number in range(1 if output_limit_retry else 2):
                if retry_number:
                    retry_payload["messages"][-1]["content"] += (
                        "\nThe previous retry still violated the output rules. "
                        "Transliterate every person and place name into Arabic script. "
                        "Return only one concise Arabic translation of the current source."
                    )
                retry_response = self._post_chat_completion(retry_payload)
                retry_result = retry_response.json()
                self._record_inference_timings(retry_result)
                retry_choice = retry_result["choices"][0]
                retry_content = retry_choice["message"]["content"]
                if isinstance(retry_content, list):
                    retry_content = "".join(
                        item.get("text", "") for item in retry_content if isinstance(item, dict)
                    )
                translated = clean_translation_output(retry_content)
                valid_arabic = _is_usable_arabic_translation(translated)
                if (retry_choice.get("finish_reason") != "length" and valid_arabic
                        and _has_latin_residue(translated)
                        and not _is_implausibly_expanded_translation(text, translated)):
                    repaired = _repair_latinized_source_names(text, translated)
                    if repaired and not _has_latin_residue(repaired):
                        translated = repaired
                if (retry_choice.get("finish_reason") != "length" and valid_arabic
                        and not _is_implausibly_expanded_translation(text, translated)
                        and not _has_latin_residue(translated)):
                    break
            else:
                repaired = _repair_latinized_source_names(text, translated)
                if (repaired is not None and _is_usable_arabic_translation(repaired)
                        and not _is_implausibly_expanded_translation(text, repaired)
                        and not _has_latin_residue(repaired)):
                    translated = repaired
                else:
                    diagnostics = {
                        "finish_reason": retry_choice.get("finish_reason"),
                        "valid_arabic": _is_usable_arabic_translation(translated),
                        "expanded": _is_implausibly_expanded_translation(text, translated),
                        "latin_residue": _has_latin_residue(translated),
                        "name_repair_available": repaired is not None,
                    }
                    raise TranslationOutputError(
                        "Index returned incomplete, corrupted, or implausibly expanded text; "
                        f"final retry checks: {diagnostics}."
                    )
        elif not valid_arabic:
            raise TranslationOutputError(
                "Translation output was not valid Arabic or contained replacement characters."
            )
        return translated

    def shutdown(self) -> None:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                self._process.terminate()
                try:
                    self._process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait(timeout=5)
            self._process = None
            self._loaded_model_identity = None


class IndexTranslateAdapter(LocalLlamaTranslationAdapter):
    engine_id = "index_translate_9b"
    model_name = "Index-Translate-9B Q4_K_M"
    model_path = INDEX_MODEL

    @staticmethod
    def build_messages(text: str, context: str = "") -> list[dict]:
        prompt = (
            "Translate this English webtoon text into fluent, natural Modern "
            "Standard Arabic. Use strictly Modern Standard Arabic, never "
            "colloquial dialect. Preserve meaning, emotion, tone, and confirmed "
            "names. Prefer idiomatic, concise phrasing without omitting meaning. "
            "Use prior dialogue only to resolve references and terminology. Do "
            "not invent facts, repeat earlier dialogue, or guess missing OCR "
            "words. For short reactions, choose a natural conversational Arabic "
            "equivalent in context rather than a dictionary gloss. Translate the intended "
            "meaning and tone, not English word order or idioms word for word. Infer Arabic "
            "gender only when the source or context clearly identifies it; otherwise prefer "
            "a natural phrasing that avoids guessing the speaker or addressee gender. Keep "
            "confirmed names in Arabic script and glossary terms consistent. If OCR is damaged "
            "or incomplete, preserve the uncertainty instead of completing or inventing words. Return only "
            "one Arabic translation.\n\n"
            "CURRENT TEXT:\n" + text.strip()
        )
        context_text = str(context or "").strip()
        if context_text:
            prompt += "\n\nCONTEXT (reference only; do not translate):\n" + context_text
        return [{"role": "user", "content": prompt}]

    def translate_many(self, items: list[dict]) -> list[str]:
        """Translate a small ordered group with one shared Index prompt/inference.

        Each item must contain ``text`` and ``context``. The response parser is
        intentionally strict: malformed, missing, duplicated, or reordered IDs
        raise TranslationOutputError so callers can fall back to one request per
        region without silently attaching dialogue to the wrong bubble.
        """
        if len(items) < 2:
            raise ValueError("Index batch translation requires at least two regions.")
        normalized = []
        for item in items:
            text = " ".join(str(item.get("text", "")).split()).strip()
            context = str(item.get("context", "")).strip()
            if not text:
                raise ValueError("Index batch contains an empty source region.")
            normalized.append({"text": text, "context": context})

        sections = []
        output_budget = 0
        for index, item in enumerate(normalized, 1):
            sections.append(
                f"BUBBLE {index}\nCURRENT TEXT:\n{item['text']}\n\n"
                f"CONTEXT (reference only; do not translate):\n{item['context']}"
            )
            output_budget += min(512, max(64, len(item["text"].split()) * 4 + 24))
        prompt_chars = sum(len(section) for section in sections)
        if prompt_chars > 4500 or output_budget > 1536:
            raise TranslationOutputError(
                "Index batch exceeds the conservative input/output budget; use separate regions."
            )

        instructions = (
            "Translate this English webtoon text into fluent, natural Modern Standard Arabic. "
            "Use strictly Modern Standard Arabic, never colloquial dialect. Preserve meaning, "
            "emotion, tone, and confirmed names. Prefer idiomatic, concise phrasing without "
            "omitting meaning. Use prior dialogue only to resolve references and terminology. "
            "Do not invent facts, repeat earlier dialogue, or guess missing OCR words. For "
            "short reactions, choose a natural conversational Arabic equivalent in context "
            "rather than a dictionary gloss. Translate intended meaning and tone, not English "
            "word order or idioms word for word. Infer Arabic gender only when the source or "
            "context clearly identifies it; otherwise avoid guessing speaker or addressee gender. "
            "Keep confirmed names in Arabic script and glossary terms consistent. If OCR is damaged "
            "or incomplete, preserve uncertainty rather than completing words. Translate every "
            "numbered bubble independently, using only its own context to resolve references. "
            "Preserve the supplied IDs and source order. Return exactly one line per bubble in the "
            "form `1. <Arabic translation>`. Do not omit, merge, split, or reorder bubbles; do not "
            "translate context.\n\n"
        )
        prompt = instructions + "\n\n---\n\n".join(sections)
        payload = {
            "model": self.model_path.stem,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "top_p": 1,
            "max_tokens": output_budget,
            "stream": False,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        self._ensure_server()
        response = self._post_chat_completion(payload)
        result = response.json()
        self._record_inference_timings(result)
        choice = result["choices"][0]
        if choice.get("finish_reason") == "length":
            raise TranslationOutputError("Index batch reached its output-token limit.")
        raw = str(choice.get("message", {}).get("content", "")).strip()

        marker = re.compile(r"(?<!\S)(\d{1,2})\s*[.)]\s*(?=[«\"'“‘\u0600-\u06ff])")
        matches = list(marker.finditer(raw))
        translations = []
        seen = []
        if matches and raw[:matches[0].start()].strip():
            raise TranslationOutputError("Index batch returned text before the first bubble ID.")
        for index, match in enumerate(matches):
            number = int(match.group(1))
            end = matches[index + 1].start() if index + 1 < len(matches) else len(raw)
            translated = clean_translation_output(raw[match.end():end])
            if (number in seen or number != index + 1
                    or not _is_usable_arabic_translation(translated)
                    or _has_latin_residue(translated)):
                raise TranslationOutputError(
                    "Index batch returned duplicated, reordered, or non-Arabic bubble output."
                )
            seen.append(number)
            translations.append(translated)
        if seen != list(range(1, len(normalized) + 1)):
            raise TranslationOutputError("Index batch omitted one or more bubble IDs.")
        return translations



class TranslateGemmaAdapter(LocalLlamaTranslationAdapter):
    engine_id = "translategemma_4b"
    model_name = "TranslateGemma-4B-it Q4_K_M"
    model_path = TRANSLATEGEMMA_MODEL

    @staticmethod
    def build_messages(text: str, context: str = "") -> list[dict]:
        # The llama.cpp server injects source/target language codes through
        # chat_template_kwargs, so keep the message as a plain text part.
        # TranslateGemma's translation template is intentionally source-only.
        return [{"role": "user", "content": text.strip()}]


class BalancedAutomaticTranslator:
    """Sequential local translator router; the web app can pin it to Index only."""

    def __init__(self, index_adapter=None, translategemma_adapter=None, allow_fallback=True):
        self.adapters = [index_adapter or IndexTranslateAdapter()]
        if allow_fallback:
            self.adapters.append(translategemma_adapter or TranslateGemmaAdapter())
        self._unavailable_for_session: set[str] = set()
        self._failure_reasons: dict[str, str] = {}
        self._active_adapter = None
        self.last_engine_id = None
        self.last_model_name = None
        self._lock = threading.RLock()

    def status(self) -> dict:
        result = {}
        for adapter in self.adapters:
            available = adapter.available()
            if available:
                availability_reason = None
            elif not Path(adapter.model_path).is_file():
                availability_reason = "ملف النموذج غير موجود"
            else:
                availability_reason = "ملف llama-server.exe غير موجود"
            result[adapter.engine_id] = {
                "available": available,
                "model": adapter.model_name,
                "path": str(adapter.model_path),
                "availability_reason": availability_reason,
                "session_error": self._failure_reasons.get(adapter.engine_id),
            }
        return result

    def preferred_engine_id(self) -> str:
        for adapter in self.adapters:
            if adapter.engine_id not in self._unavailable_for_session and adapter.available():
                return adapter.engine_id
        return "unavailable"

    def translate_with(self, engine_id: str, text: str, context: str = "") -> str:
        """Run one chosen engine; allow independent requests to share server slots."""
        with self._lock:
            adapter = next((item for item in self.adapters if item.engine_id == engine_id), None)
            if adapter is None:
                raise ValueError(f"Unsupported translation engine: {engine_id}")
            if not adapter.available():
                reason = self.status()[engine_id]["availability_reason"]
                raise RuntimeError(f"{adapter.model_name} is unavailable: {reason}")
            if self._active_adapter is not adapter:
                if self._active_adapter is not None:
                    self._active_adapter.shutdown()
                self._active_adapter = adapter

        # Selection and startup stay protected, while inference can use the
        # server's slots for independent regions with unchanged prompts/settings.
        try:
            translated = adapter.translate(text, context=context)
            if not translated or not _is_usable_arabic_translation(translated):
                raise TranslationOutputError("Model returned no usable Arabic translation.")
            with self._lock:
                self.last_engine_id = adapter.engine_id
                self.last_model_name = adapter.model_name
            return translated
        except TranslationOutputError as exc:
            with self._lock:
                self._failure_reasons[engine_id] = f"{type(exc).__name__}: {exc}"
            raise
        except Exception as exc:
            with self._lock:
                self._failure_reasons[engine_id] = f"{type(exc).__name__}: {exc}"
                adapter.shutdown()
                if self._active_adapter is adapter:
                    self._active_adapter = None
            raise

    def translate_many(self, engine_id: str, items: list[dict]) -> list[str]:
        """Run a single adapter's validated multi-region request, when supported."""
        with self._lock:
            adapter = next((item for item in self.adapters if item.engine_id == engine_id), None)
            if adapter is None:
                raise ValueError(f"Unsupported translation engine: {engine_id}")
            if not adapter.available():
                reason = self.status()[engine_id]["availability_reason"]
                raise RuntimeError(f"{adapter.model_name} is unavailable: {reason}")
            batch_method = getattr(adapter, "translate_many", None)
            if not callable(batch_method):
                raise TranslationOutputError(f"{adapter.model_name} does not support batch requests.")
            if self._active_adapter is not adapter:
                if self._active_adapter is not None:
                    self._active_adapter.shutdown()
                self._active_adapter = adapter

        translations = batch_method(items)
        if len(translations) != len(items) or any(
            not text or not _is_usable_arabic_translation(text) for text in translations
        ):
            raise TranslationOutputError("Index batch returned incomplete or unusable Arabic output.")
        with self._lock:
            self.last_engine_id = adapter.engine_id
            self.last_model_name = adapter.model_name
        return translations

    def translate(self, text: str, context: str = "") -> str:
        with self._lock:
            for adapter in self.adapters:
                if adapter.engine_id in self._unavailable_for_session or not adapter.available():
                    continue
                if self._active_adapter is not adapter:
                    if self._active_adapter is not None:
                        self._active_adapter.shutdown()
                    self._active_adapter = adapter
                try:
                    translated = adapter.translate(text, context=context)
                    if translated and _is_usable_arabic_translation(translated):
                        self.last_engine_id = adapter.engine_id
                        self.last_model_name = adapter.model_name
                        return translated
                    raise TranslationOutputError("لم ينتج المحرك ترجمة عربية غير فارغة.")
                except TranslationOutputError as exc:
                    reason = f"{type(exc).__name__}: {exc}"
                    self._failure_reasons[adapter.engine_id] = reason
                    LOGGER.warning("Local translation engine %s returned invalid output for one request: %s",
                                   adapter.engine_id, reason)
                    continue
                except Exception as exc:
                    reason = f"{type(exc).__name__}: {exc}"
                    self._failure_reasons[adapter.engine_id] = reason
                    LOGGER.warning("Local translation engine %s failed; trying the next engine: %s",
                                   adapter.engine_id, reason)
                    adapter.shutdown()
                    self._unavailable_for_session.add(adapter.engine_id)
                    if self._active_adapter is adapter:
                        self._active_adapter = None
            details = []
            for adapter in self.adapters:
                reason = self._failure_reasons.get(adapter.engine_id)
                if reason is None and not adapter.available():
                    reason = self.status()[adapter.engine_id]["availability_reason"]
                if reason:
                    details.append(f"{adapter.model_name}: {reason}")
            suffix = " " + "؛ ".join(details) if details else ""
            engines = "Index أو TranslateGemma" if len(self.adapters) > 1 else "Index"
            raise RuntimeError(f"لم يتوفر محرك {engines} صالح للترجمة." + suffix)

    def shutdown(self) -> None:
        with self._lock:
            if self._active_adapter is not None:
                self._active_adapter.shutdown()
            self._active_adapter = None

