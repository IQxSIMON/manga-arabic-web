import os
import re
import time
import atexit
import csv
import hashlib
import math
import ipaddress
import socket
import sqlite3
import io
import json
import uuid
import tempfile
import threading
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor
import zlib
import zipfile
import xml.etree.ElementTree as ET
from collections import OrderedDict
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from urllib.parse import urlparse, urljoin

import requests
from flask import Flask, jsonify, render_template, request, Response, send_file
from PIL import Image, UnidentifiedImageError
from google_vision_ocr import extract_google_vision
from gemini_cloud_ocr import recognize_regions as recognize_regions_with_gemini
from ocr_engine import LocalRapidOCR
import library_store
from source_providers import providers
from image_pipeline.renderer import render_translated_page
from translator_engine.engine import TranslatorEngine
from translator_engine.idioms import translate_reviewed_phrase
from translator_engine.llama_cpp_engine import LlamaCppTranslator, DEFAULT_PROOFREADER_MODEL
from translator_engine.madlad_engine import MadladTranslator
from translator_engine.adapters import (
    BalancedAutomaticTranslator,
    GENERATION_POLICY_VERSION,
    INDEX_BATCH_PROMPT_VERSION,
    INDEX_PROMPT_VERSION,
    TranslationOutputError,
    TRANSLATEGEMMA_PROMPT_VERSION,
    clean_translation_output,
    has_arabic_letters,
    _has_latin_residue,
    _is_implausibly_expanded_translation,
    _is_usable_arabic_translation,
)

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False
app.config["MAX_CONTENT_LENGTH"] = 180 * 1024 * 1024

SUPPORTED_VERTICAL_COMIC_TYPES = {"manga", "manhwa", "manhua"}
UNSUPPORTED_LAYOUT_MESSAGE = "هذا النمط غير مدعوم حالياً. يدعم التطبيق الفصول الطولية التي تُقرأ بالتمرير من أعلى إلى أسفل."


def _request_authority(value, default_port=80):
    """Return a normalized (host, port) pair for a URL authority, if valid."""
    try:
        parsed = urlparse(f"//{value}")
        hostname = (parsed.hostname or "").rstrip(".").casefold()
        if not hostname or parsed.username or parsed.password:
            return None
        port = parsed.port or default_port
        return hostname, port
    except ValueError:
        return None


def _same_request_origin(value):
    """Compare an Origin or Referer authority with the current local request."""
    try:
        parsed = urlparse(value)
        if not parsed.hostname or parsed.username or parsed.password:
            return False
        default_port = 443 if request.scheme.casefold() == "https" else 80
        expected = _request_authority(request.host, default_port=default_port)
        actual = (parsed.hostname.rstrip(".").casefold(), parsed.port or default_port)
        # The public preview tunnel forwards "host:port" while the browser
        # origin has no port; compare authorities ignoring the incoming port.
        expected_host = expected[0].rstrip(".").casefold() if expected else None
        expected_port = expected[1] if expected else None
        if expected_host is not None and actual[0] == expected_host and expected_port in (80, 443, 3000):
            return True
        # The Vite preview proxy rewrites Host to the local bind address, so an
        # origin on the configured preview tunnel host is trusted even though
        # the request authority is local.
        allowed_hosts = {
            part.strip().rstrip(".").casefold()
            for part in os.getenv("PREVIEW_ALLOWED_HOST", "").split(",")
            if part.strip()
        }
        if actual[0] in allowed_hosts and actual[1] in (80, 443, 3000):
            return True
        return expected_host is not None and actual == (expected_host, expected_port)
    except ValueError:
        return False


@app.before_request
def protect_local_http_authority():
    """Reject DNS-rebinding Host headers and cross-origin browser writes."""
    host_authority = _request_authority(request.host)
    if host_authority is None:
        return jsonify(error="اسم مضيف الخادم المحلي غير صالح."), 400
    hostname = host_authority[0]
    try:
        configured_host = resolve_local_bind_host().casefold()
    except ValueError:
        configured_host = "127.0.0.1"
    allowed_hosts = {"localhost", "127.0.0.1", configured_host}
    preview_hosts = {
        part.strip().casefold()
        for part in os.getenv("PREVIEW_ALLOWED_HOST", "").split(",")
        if part.strip()
    }
    allowed_hosts.update(preview_hosts)
    if os.getenv("VERCEL") and hostname.endswith(".vercel.app"):
        allowed_hosts.add(hostname)
    if hostname not in allowed_hosts:
        return jsonify(error="رفض الطلب لأن اسم المضيف لا يطابق عنوان التطبيق المحلي."), 400

    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        origin = request.headers.get("Origin", "").strip()
        referer = request.headers.get("Referer", "").strip()
        fetch_site = request.headers.get("Sec-Fetch-Site", "").strip().casefold()
        if origin:
            if not _same_request_origin(origin):
                return jsonify(error="رُفض طلب الكتابة لأنه صادر من موقع آخر."), 403
        elif referer:
            if not _same_request_origin(referer):
                return jsonify(error="رُفض طلب الكتابة لأنه صادر من موقع آخر."), 403
        elif fetch_site in {"cross-site", "same-site"}:
            return jsonify(error="رُفض طلب الكتابة لغياب مصدر محلي موثوق."), 403


def resolve_local_bind_host(value=None):
    """Allow loopback by default or one private IPv4 address for phone testing."""
    host = str(value if value is not None else os.getenv("MANGA_BIND_HOST", "127.0.0.1")).strip()
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError("MANGA_BIND_HOST يجب أن يكون عنوان IPv4 محلياً.") from exc
    if address.version != 4 or address.is_unspecified or not (address.is_loopback or address.is_private):
        raise ValueError("يسمح التطبيق بعنوان loopback أو بعنوان IPv4 خاص بالشبكة المحلية فقط.")
    return str(address)


PAGE_PIPELINE_VERSION = 30
RENDER_PIPELINE_VERSION = 11
PAGE_TRANSLATION_CONTEXT_VERSION = 21
HTTP = requests.Session()
HTTP.headers.update({
    # MangaDex asks API clients to identify themselves; a generic browser UA
    # can be redirected to its interactive edge challenge instead of JSON.
    "User-Agent": "MangaArabicTranslator/0.1 (local manga translation client)",
    "Accept": "application/json, image/avif, image/webp, image/png, image/jpeg, */*",
    "Accept-Language": "en-US,en;q=0.9"
})
CHAPTER_HOSTS = frozenset(
    hostname for provider_id in providers.provider_ids()
    for hostname in providers.by_id(provider_id).hosts
)
OCR_CACHE = OrderedDict()  # Cache key -> text, OCR lines, provider, regions, and timings.
OCR_CACHE_MAX_BYTES = 24 * 1024 * 1024
OCR_CACHE_MAX_ENTRIES = 40
_OCR_CACHE_BYTES = 0
_OCR_CACHE_LOCK = threading.RLock()
REMOTE_TRANSLATION_PROGRESS = OrderedDict()
REMOTE_TRANSLATION_PROGRESS_MAX_ENTRIES = 128
_REMOTE_TRANSLATION_PROGRESS_LOCK = threading.RLock()
# Checkpoint for /api/translate/chapter: fingerprint -> {"ocr_by_id", "requests_sent", "usage"}.
# Lets the client retry the same chapter in several requests within the serverless time limit.
CHAPTER_TRANSLATION_CACHE = OrderedDict()
CHAPTER_TRANSLATION_CACHE_MAX_ENTRIES = 8
_CHAPTER_TRANSLATION_CACHE_LOCK = threading.RLock()


def _chapter_translation_cache_get(fingerprint):
    with _CHAPTER_TRANSLATION_CACHE_LOCK:
        entry = CHAPTER_TRANSLATION_CACHE.get(fingerprint)
        if entry is not None:
            CHAPTER_TRANSLATION_CACHE.move_to_end(fingerprint)
        return entry


def _chapter_translation_cache_put(fingerprint, ocr_by_id, requests_sent, usage):
    with _CHAPTER_TRANSLATION_CACHE_LOCK:
        CHAPTER_TRANSLATION_CACHE[fingerprint] = {
            "ocr_by_id": ocr_by_id,
            "requests_sent": requests_sent,
            "usage": usage,
        }
        CHAPTER_TRANSLATION_CACHE.move_to_end(fingerprint)
        while len(CHAPTER_TRANSLATION_CACHE) > CHAPTER_TRANSLATION_CACHE_MAX_ENTRIES:
            CHAPTER_TRANSLATION_CACHE.popitem(last=False)
REMOTE_RENDER_CACHE_TEMP = tempfile.TemporaryDirectory(prefix="manga-arabic-previews-")
REMOTE_RENDER_CACHE_DIR = Path(REMOTE_RENDER_CACHE_TEMP.name)
REMOTE_RENDER_CACHE = OrderedDict()  # Preview ID -> (temporary path, byte size)
REMOTE_RENDER_CACHE_MAX_BYTES = 512 * 1024 * 1024
REMOTE_RENDER_CACHE_MAX_ENTRIES = 8
_REMOTE_RENDER_CACHE_BYTES = 0
_REMOTE_RENDER_CACHE_LOCK = threading.RLock()
_PAGE_PROCESS_LOCKS = {}
_PAGE_PROCESS_LOCKS_LOCK = threading.RLock()
ACTIVE_PAGE_RUNTIME = {}
_ACTIVE_PAGE_RUNTIME_LOCK = threading.RLock()
_LOCAL_OCR_INIT_LOCK = threading.Lock()
_BALANCED_TRANSLATOR_INIT_LOCK = threading.Lock()
TRANSLATOR = None
LOCAL_OCR = None
QWEN_TRANSLATOR = None
QWEN_PROOFREADER = None
MADLAD_TRANSLATOR = None
BALANCED_TRANSLATOR = None
ENABLE_LOCAL_PROOFREADER = os.getenv("MANGA_ENABLE_LOCAL_PROOFREADER", "0").strip().lower() in {"1", "true", "yes", "on"}
# Vertical, English-lettered comic builds use Index as their single exposed translation model.
CURRENT_TRANSLATION_ENGINE = "index_translate_9b"
TRANSLATION_RECIPE_VERSIONS = {
    "index_translate_9b": f"{INDEX_PROMPT_VERSION}:{INDEX_BATCH_PROMPT_VERSION}:{GENERATION_POLICY_VERSION}",
    "translategemma_4b": f"{TRANSLATEGEMMA_PROMPT_VERSION}:{GENERATION_POLICY_VERSION}",
}


def get_balanced_translator():
    """Initialize the shared local-model router once under concurrent requests."""
    global BALANCED_TRANSLATOR
    if BALANCED_TRANSLATOR is None:
        with _BALANCED_TRANSLATOR_INIT_LOCK:
            if BALANCED_TRANSLATOR is None:
                BALANCED_TRANSLATOR = BalancedAutomaticTranslator(allow_fallback=False)
    return BALANCED_TRANSLATOR


def translation_recipe_signature(engine_id=None):
    """Identify an engine prompt and local model file for cache freshness."""
    if engine_id is None:
        engine_id = CURRENT_TRANSLATION_ENGINE
        if engine_id == "balanced_auto":
            router = get_balanced_translator()
            engine_id = router.preferred_engine_id()
    if engine_id == "unavailable":
        return None
    recipe = TRANSLATION_RECIPE_VERSIONS.get(engine_id, "legacy")
    model_identity = ""
    if engine_id in {"index_translate_9b", "translategemma_4b"}:
        router = get_balanced_translator()
        adapter = next((item for item in router.adapters if item.engine_id == engine_id), None)
        model_path = getattr(adapter, "model_path", None)
        if model_path is not None:
            try:
                stat = Path(model_path).stat()
                model_identity = f"|{model_path}|{stat.st_size}|{stat.st_mtime_ns}"
            except OSError:
                model_identity = f"|{model_path}"
    return f"{engine_id}|{recipe}{model_identity}"


def active_translation_recipe_signature():
    """Identify the effective local model recipe used for saved page outputs."""
    return translation_recipe_signature()


def shutdown_local_translation_models():
    """Stop child inference servers when Flask exits normally."""
    for translator in (BALANCED_TRANSLATOR, QWEN_TRANSLATOR, QWEN_PROOFREADER):
        shutdown = getattr(translator, "shutdown", None)
        if shutdown:
            try:
                shutdown()
            except Exception:
                app.logger.exception("Failed to stop a local translation process cleanly")


atexit.register(shutdown_local_translation_models)


def cache_ocr_result(key, value):
    """Store serialized OCR metadata within a byte budget, skipping oversized entries."""
    global _OCR_CACHE_BYTES
    serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    entry_bytes = len(serialized)
    if entry_bytes > OCR_CACHE_MAX_BYTES:
        return
    with _OCR_CACHE_LOCK:
        previous = OCR_CACHE.pop(key, None)
        if previous is not None:
            _OCR_CACHE_BYTES -= len(previous)
        OCR_CACHE[key] = serialized
        _OCR_CACHE_BYTES += entry_bytes
        while (_OCR_CACHE_BYTES > OCR_CACHE_MAX_BYTES
               or len(OCR_CACHE) > OCR_CACHE_MAX_ENTRIES):
            _old_key, old_value = OCR_CACHE.popitem(last=False)
            _OCR_CACHE_BYTES -= len(old_value)


def update_remote_translation_progress(job_id, **values):
    if not job_id:
        return
    now = time.time()
    with _REMOTE_TRANSLATION_PROGRESS_LOCK:
        expired = [key for key, value in REMOTE_TRANSLATION_PROGRESS.items()
                   if now - value.get("updated_at", now) > 3600]
        for key in expired:
            REMOTE_TRANSLATION_PROGRESS.pop(key, None)
        state = REMOTE_TRANSLATION_PROGRESS.pop(job_id, {})
        state.update(values)
        state["updated_at"] = now
        REMOTE_TRANSLATION_PROGRESS[job_id] = state
        while len(REMOTE_TRANSLATION_PROGRESS) > REMOTE_TRANSLATION_PROGRESS_MAX_ENTRIES:
            REMOTE_TRANSLATION_PROGRESS.popitem(last=False)


def update_local_page_runtime(chapter_id, page_number, **values):
    """Expose elapsed live OCR/inference time without rewriting page JSON each second."""
    key = (str(chapter_id), int(page_number))
    now = time.monotonic()
    with _ACTIVE_PAGE_RUNTIME_LOCK:
        if not values:
            ACTIVE_PAGE_RUNTIME.pop(key, None)
            return
        ACTIVE_PAGE_RUNTIME[key] = {**values, "updated_monotonic": now}
        stale = [
            item_key for item_key, state in ACTIVE_PAGE_RUNTIME.items()
            if now - state.get("updated_monotonic", now) > 3600
        ]
        for item_key in stale:
            ACTIVE_PAGE_RUNTIME.pop(item_key, None)


def cache_remote_render(preview_id, preview):
    """Persist temporary previews to disk under a bounded LRU budget."""
    global _REMOTE_RENDER_CACHE_BYTES
    if len(preview) > REMOTE_RENDER_CACHE_MAX_BYTES:
        raise ValueError("حجم المعاينة يتجاوز حد التخزين المؤقت المحلي.")
    path = REMOTE_RENDER_CACHE_DIR / f"{preview_id}.png"
    temporary_path = REMOTE_RENDER_CACHE_DIR / f"{preview_id}.png.part"
    with _REMOTE_RENDER_CACHE_LOCK:
        old_entry = REMOTE_RENDER_CACHE.pop(preview_id, None)
        if old_entry is not None:
            _REMOTE_RENDER_CACHE_BYTES -= old_entry[1]
            try:
                old_entry[0].unlink(missing_ok=True)
            except OSError:
                pass
        temporary_path.write_bytes(preview)
        temporary_path.replace(path)
        REMOTE_RENDER_CACHE[preview_id] = (path, len(preview))
        _REMOTE_RENDER_CACHE_BYTES += len(preview)
        while (_REMOTE_RENDER_CACHE_BYTES > REMOTE_RENDER_CACHE_MAX_BYTES
               or len(REMOTE_RENDER_CACHE) > REMOTE_RENDER_CACHE_MAX_ENTRIES):
            _old_id, (old_path, old_size) = REMOTE_RENDER_CACHE.popitem(last=False)
            _REMOTE_RENDER_CACHE_BYTES -= old_size
            try:
                old_path.unlink(missing_ok=True)
            except OSError:
                # The file may be in use by a concurrent response. It remains
                # inside the process-owned temporary directory and is cleaned
                # when the app exits.
                pass
        return path


def valid_chapter_url(url):
    return providers.for_url(url) is not None


def public_https_url(url):
    """Reject credentials, unusual ports, and hostnames resolving to non-public IPs."""
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        if parsed.scheme != "https" or not hostname or parsed.username or parsed.password:
            return False
        manga_network_host = bool(hostname and hostname.lower().endswith(".mangadex.network"))
        if parsed.port not in (None, 443) and not manga_network_host:
            return False
        try:
            addresses = {ipaddress.ip_address(hostname)}
        except ValueError:
            addresses = {
                ipaddress.ip_address(item[4][0].split("%", 1)[0])
                for item in socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
            }
        return bool(addresses) and all(address.is_global for address in addresses)
    except (ValueError, OSError, socket.gaierror):
        return False


def safe_get(url, headers=None, timeout=25, max_bytes=12 * 1024 * 1024, allowed_hosts=None, method="GET", data=None):
    """Fetch HTTPS while revalidating redirects, allowing safe provider POSTs, and bounding bodies."""
    method = str(method).upper()
    if method not in {"GET", "POST"}:
        raise ValueError("طريقة الطلب غير مدعومة.")
    current_url = url
    for _ in range(4):
        hostname = (urlparse(current_url).hostname or "").lower()
        if (allowed_hosts is not None and hostname not in allowed_hosts) or not public_https_url(current_url):
            raise ValueError("تم رفض الرابط لأنه غير آمن أو لا يشير إلى عنوان عام.")
        request_options = {
            "headers": headers, "timeout": timeout,
            "allow_redirects": False, "stream": True,
        }
        if method == "POST":
            response = HTTP.post(current_url, data=data, **request_options)
        else:
            response = HTTP.get(current_url, **request_options)
        if response.is_redirect or response.is_permanent_redirect:
            location = response.headers.get("Location")
            response.close()
            if not location:
                raise ValueError("استجابة إعادة التوجيه لا تحتوي على رابط وجهة.")
            if method == "POST" and response.status_code in {301, 302, 303}:
                method, data = "GET", None
            current_url = urljoin(current_url, location)
            continue
        response.raise_for_status()
        content = bytearray()
        for chunk in response.iter_content(64 * 1024):
            content.extend(chunk)
            if len(content) > max_bytes:
                response.close()
                raise ValueError("حجم المحتوى أكبر من الحد المسموح.")
        response._content = bytes(content)
        response.close()
        return response
    raise ValueError("تجاوز الرابط الحد المسموح لإعادة التوجيه.")


@app.get("/")
def home():
    return render_template("index.html")


@app.errorhandler(413)
def request_too_large(_error):
    return jsonify(error="حجم الطلب كبير جداً. الحد الأقصى لاستيراد الفصل 180 MB."), 413


@app.errorhandler(sqlite3.Error)
def storage_unavailable(error):
    app.logger.exception("Library storage unavailable", exc_info=error)
    return jsonify(error="تعذر الوصول إلى مخزن المكتبة على الخادم؛ أعد المحاولة بعد لحظات."), 503


@contextmanager
def _page_processing_lock(chapter_id, page_number):
    """Serialize concurrent OCR/translation requests for one saved page."""
    key = (str(chapter_id), int(page_number))
    with _PAGE_PROCESS_LOCKS_LOCK:
        entry = _PAGE_PROCESS_LOCKS.get(key)
        if entry is None:
            entry = [threading.Lock(), 0]
            _PAGE_PROCESS_LOCKS[key] = entry
        entry[1] += 1
    try:
        with entry[0]:
            yield
    finally:
        with _PAGE_PROCESS_LOCKS_LOCK:
            entry[1] -= 1
            if entry[1] == 0 and _PAGE_PROCESS_LOCKS.get(key) is entry:
                _PAGE_PROCESS_LOCKS.pop(key, None)


def _serialize_page_route(view):
    """Run page mutations after any active OCR/translation on that page."""
    @wraps(view)
    def wrapped(chapter_id, page_number, *args, **kwargs):
        with _page_processing_lock(chapter_id, page_number):
            return view(chapter_id, page_number, *args, **kwargs)
    return wrapped


def _validated_upload(file_storage):
    content = file_storage.read(12 * 1024 * 1024 + 1)
    return _validated_image_bytes(content)


def _validated_image_bytes(content):
    if not content or len(content) > 12 * 1024 * 1024:
        raise ValueError("كل صورة يجب أن تكون بين بايت واحد و12 MB.")
    try:
        with Image.open(io.BytesIO(content)) as image:
            image_format = image.format
            width, height = image.size
            if image_format not in {"JPEG", "PNG", "WEBP"}:
                raise ValueError("الصيغ المدعومة هي JPEG وPNG وWEBP.")
            if width < 16 or height < 16 or width > 12000 or height > 30000 or width * height > 60_000_000:
                raise ValueError("أبعاد الصورة خارج الحدود المسموحة.")
            image.verify()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("أحد الملفات ليس صورة سليمة أو مدعومة.") from exc
    extension = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}[image_format]
    return extension, content


def _validated_cbz_pages(file_storage):
    archive_bytes = file_storage.read(180 * 1024 * 1024 + 1)
    if len(archive_bytes) > 180 * 1024 * 1024:
        raise ValueError("حجم ملف CBZ يتجاوز 180 MB.")
    pages = []
    total_uncompressed = 0
    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            entries = archive.infolist()
            if len(entries) > 2000:
                raise ValueError("ملف CBZ يحتوي عدداً غير مسموح به من العناصر.")
            members = [
                item for item in entries
                if not item.is_dir() and Path(item.filename).suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
            ]
            if len(members) > 300:
                raise ValueError("يجب ألا يتجاوز الفصل 300 صفحة.")
            members.sort(key=lambda item: _natural_name_key(item.filename))
            for item in members:
                if item.flag_bits & 0x1:
                    raise ValueError("ملفات CBZ المشفرة غير مدعومة.")
                if item.file_size > 12 * 1024 * 1024:
                    raise ValueError("إحدى صفحات CBZ تتجاوز 12 MB.")
                total_uncompressed += item.file_size
                if total_uncompressed > 150 * 1024 * 1024:
                    raise ValueError("الحجم المفكوك لصفحات CBZ يتجاوز 150 MB.")
                with archive.open(item) as page_file:
                    content = page_file.read(12 * 1024 * 1024 + 1)
                if len(content) != item.file_size:
                    raise ValueError("حجم أحد عناصر CBZ لا يطابق بيانات الأرشيف.")
                extension, content = _validated_image_bytes(content)
                pages.append((item.filename, extension, content))
    except zipfile.BadZipFile as exc:
        raise ValueError("ملف CBZ غير صالح أو تالف.") from exc
    except (RuntimeError, OSError, EOFError, zlib.error) as exc:
        raise ValueError("تعذر قراءة إحدى صفحات CBZ.") from exc
    if not pages:
        raise ValueError("لم أجد صفحات JPEG أو PNG أو WEBP داخل ملف CBZ.")
    return [(extension, content) for _name, extension, content in pages]


def _natural_name_key(name):
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", name)]


@app.get("/api/library")
def library_list():
    archived = request.args.get("archived", "false").strip().lower() == "true"
    return jsonify(chapters=library_store.list_chapters(archived=archived))


@app.get("/api/library/backup")
def library_backup():
    archive_path = None
    try:
        archive_path = library_store.create_library_backup()
        response = send_file(
            archive_path,
            mimetype="application/zip",
            as_attachment=True,
            download_name="manga-arabic-library-backup.zip",
            max_age=0,
        )
        response.headers["Cache-Control"] = "no-store"
        response.call_on_close(lambda: library_store.cleanup_library_backup(archive_path))
        return response
    except (OSError, sqlite3.Error, zipfile.BadZipFile) as exc:
        if archive_path is not None:
            library_store.cleanup_library_backup(archive_path)
        app.logger.exception("Local library backup failed")
        return jsonify(error=f"تعذر إنشاء النسخة الاحتياطية للمكتبة: {exc}"), 500


@app.post("/api/library/<chapter_id>/archive")
def library_archive(chapter_id):
    if not re.fullmatch(r"[0-9a-f]{32}", chapter_id, re.IGNORECASE):
        return jsonify(error="معرّف الفصل غير صالح."), 400
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or type(payload.get("archived")) is not bool:
        return jsonify(error="حدد ما إذا كنت تريد أرشفة الفصل أو استعادته."), 400
    chapter = library_store.set_chapter_archived(chapter_id, payload["archived"])
    if chapter is None:
        return jsonify(error="الفصل غير موجود في المكتبة المحلية."), 404
    return jsonify(chapter=chapter, archived=payload["archived"])


@app.patch("/api/library/<chapter_id>/reading-position")
def library_reading_position(chapter_id):
    if not re.fullmatch(r"[0-9a-f]{32}", chapter_id, re.IGNORECASE):
        return jsonify(error="معرّف الفصل غير صالح."), 400
    payload = request.get_json(silent=True)
    page_number = payload.get("page_number") if isinstance(payload, dict) else None
    if type(page_number) is not int or not 1 <= page_number <= 300:
        return jsonify(error="رقم صفحة القراءة غير صالح."), 400
    if not library_store.update_reading_position(chapter_id, page_number):
        if library_store.get_chapter(chapter_id) is None:
            return jsonify(error="الفصل غير موجود في المكتبة المحلية."), 404
        return jsonify(error="رقم الصفحة غير موجود في هذا الفصل."), 404
    return jsonify(chapter_id=chapter_id, last_read_page=page_number)


@app.post("/api/library/import")
def library_import():
    title = (request.form.get("title") or "").strip()[:160]
    series_title = (request.form.get("series_title") or title).strip()[:160]
    series_type = (request.form.get("series_type") or "manhwa").strip().lower()
    direction = (request.form.get("reading_direction") or "ltr").strip().lower()
    uploads = [item for item in request.files.getlist("pages") if item.filename]
    archive_upload = request.files.get("chapter_archive")
    if not title:
        return jsonify(error="أدخل عنوان العمل أو الفصل."), 400
    if str(request.form.get("reading_layout", "")).strip().lower() != "vertical":
        return jsonify(error=UNSUPPORTED_LAYOUT_MESSAGE), 422
    if series_type not in SUPPORTED_VERTICAL_COMIC_TYPES or direction != "ltr":
        return jsonify(error="يدعم هذا الإصدار الأعمال ذات النص الإنجليزي والقراءة الطولية من اليسار إلى اليمين."), 400
    if archive_upload and archive_upload.filename and uploads:
        return jsonify(error="استورد صوراً متعددة أو ملف CBZ، وليس كليهما معاً."), 400
    try:
        if archive_upload and archive_upload.filename:
            pages = _validated_cbz_pages(archive_upload)
        else:
            if not uploads or len(uploads) > 300:
                return jsonify(error="اختر صوراً أو ملف CBZ يحتوي من صفحة واحدة إلى 300 صفحة."), 400
            uploads.sort(key=lambda item: _natural_name_key(item.filename))
            pages = [_validated_upload(item) for item in uploads]
        if sum(len(content) for _, content in pages) > 150 * 1024 * 1024:
            return jsonify(error="الحجم الإجمالي للصور يتجاوز 150 MB."), 413
        chapter = library_store.create_chapter(
            title, series_type, direction, pages,
            series_title=series_title or title,
            series_key=f"local:{(series_title or title).casefold()}",
        )
        return jsonify(chapter=chapter), 201
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    except OSError as exc:
        app.logger.exception("Local chapter import failed")
        return jsonify(error=f"تعذر حفظ الفصل محلياً: {exc}"), 500


@app.get("/api/library/<chapter_id>")
def library_chapter(chapter_id):
    chapter = library_store.get_chapter(chapter_id)
    if chapter is None:
        return jsonify(error="الفصل غير موجود في المكتبة المحلية."), 404
    chapter["pause_requested"] = library_store.is_chapter_paused(chapter_id)
    results = refresh_chapter_translation_staleness(chapter_id, chapter)
    for page in chapter["pages"]:
        page["image_url"] = f"/api/library/{chapter_id}/pages/{page['page_number']}/image"
        result = results.get(int(page["page_number"]))
        page["rendered_url"] = (
            f"/api/library/{chapter_id}/pages/{page['page_number']}/rendered"
            if result and not result.get("translation_inputs_stale")
            and library_store.read_rendered_page(chapter_id, page["page_number"]) else None
        )
        page["result"] = result
    return jsonify(chapter=chapter)


@app.patch("/api/library/<chapter_id>/series")
def library_update_series(chapter_id):
    payload = request.get_json(silent=True) or {}
    series_title = payload.get("series_title")
    if not isinstance(series_title, str):
        return jsonify(error="أدخل عنواناً نصياً للسلسلة."), 400
    try:
        chapter = library_store.update_chapter_series(chapter_id, series_title)
        if chapter is None:
            return jsonify(error="الفصل غير موجود في المكتبة المحلية."), 404
        return jsonify(chapter=chapter)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400


@app.patch("/api/library/<chapter_id>/title")
def library_update_title(chapter_id):
    if not re.fullmatch(r"[0-9a-f]{32}", chapter_id, re.IGNORECASE):
        return jsonify(error="معرّف الفصل غير صالح."), 400
    payload = request.get_json(silent=True)
    title = payload.get("title") if isinstance(payload, dict) else None
    if not isinstance(title, str):
        return jsonify(error="أدخل عنواناً نصياً للفصل."), 400
    try:
        chapter = library_store.update_chapter_title(chapter_id, title)
        if chapter is None:
            return jsonify(error="الفصل غير موجود في المكتبة المحلية."), 404
        return jsonify(chapter=chapter)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400


@app.get("/api/library/<chapter_id>/progress")
def library_chapter_progress(chapter_id):
    chapter = library_store.get_chapter(chapter_id)
    if chapter is None:
        return jsonify(error="الفصل غير موجود في المكتبة المحلية."), 404
    # Progress is polled every few seconds during long chapters. Recomputing
    # context freshness for every completed page on every poll repeatedly
    # rereads earlier dialogue and adds avoidable database work. Freshness is
    # still enforced when the chapter is opened, translated, rendered, or
    # exported; this endpoint reports the most recently saved page states.
    results = {
        int(page["page_number"]): library_store.read_page_result(
            chapter_id, int(page["page_number"])
        )
        for page in chapter["pages"]
    }
    pages = []
    completed = 0
    total_regions = 0
    completed_regions = 0
    active_page = None
    fallback_page = None
    for page in chapter["pages"]:
        result = results.get(int(page["page_number"]))
        status = "not_started"
        progress = {"stage": "not_started", "completed_regions": 0, "total_regions": 0}
        if result:
            status = result.get("processing_status", "complete" if result.get("pipeline_version") == 2 else "not_started")
            progress = result.get("progress", progress)
            if result.get("translation_inputs_stale"):
                status = "stale"
                progress = {**progress, "stage": "context_changed"}
            elif status == "complete" and not _page_has_complete_translations(result):
                status = "translation_in_progress"
                progress = {
                    **progress,
                    "stage": "translation",
                    "completed_regions": sum(
                        _region_is_complete(line) for line in result["translated_lines"]
                    ) if isinstance(result.get("translated_lines"), list) else 0,
                    "total_regions": len(result["translated_lines"]),
                } if isinstance(result.get("translated_lines"), list) else {
                    **progress, "stage": "translation", "completed_regions": 0, "total_regions": 0
                }
        with _ACTIVE_PAGE_RUNTIME_LOCK:
            runtime = dict(ACTIVE_PAGE_RUNTIME.get(
                (str(chapter_id), int(page["page_number"])), {}
            ))
        runtime_elapsed = max(0.0, time.monotonic() - runtime["started_monotonic"]) if runtime.get("started_monotonic") else 0.0
        if runtime:
            status = "ocr_in_progress" if runtime.get("stage") == "ocr" else "translation_in_progress"
            progress = {
                **progress,
                "stage": runtime.get("stage", "translation"),
                "current_region": runtime.get("current_region"),
            }
        if status == "complete":
            completed += 1
        else:
            candidate = {"page_number": page["page_number"], "status": status,
                         "stage": progress.get("stage"), "progress": progress,
                         "ocr_elapsed_seconds": result.get("ocr_elapsed_seconds") if result else None,
                         "translation_elapsed_seconds": result.get("translation_elapsed_seconds") if result else None}
            if runtime:
                candidate["current_stage_elapsed_seconds"] = round(runtime_elapsed, 1)
                if runtime.get("stage") == "ocr":
                    candidate["ocr_elapsed_seconds"] = round(runtime_elapsed, 1)
                else:
                    candidate["current_region_elapsed_seconds"] = round(runtime_elapsed, 1)
                    candidate["translation_elapsed_seconds"] = round(
                        float(candidate["translation_elapsed_seconds"] or 0.0) + runtime_elapsed, 1
                    )
            # A failed page stays resumable as translation_in_progress. Prefer
            # the page currently doing OCR/inference so a later page's live
            # region is not hidden behind that earlier saved failure.
            has_active_request = bool(
                status in {"ocr_in_progress", "translation_in_progress"}
                and result and (
                    not result.get("last_translation_error")
                    or progress.get("current_region") is not None
                )
            )
            if has_active_request and active_page is None:
                active_page = candidate
            elif fallback_page is None:
                fallback_page = candidate
        total_regions += int(progress.get("total_regions", 0) or 0)
        completed_regions += int(progress.get("completed_regions", 0) or 0)
        pages.append({"page_number": page["page_number"], "status": status, "progress": progress})
    return jsonify(
        chapter_id=chapter_id, page_count=len(pages), completed_pages=completed,
        completed_regions=completed_regions, total_regions=total_regions,
        status=("complete" if pages and completed == len(pages)
                else "in_progress" if any(page["status"] != "not_started" for page in pages)
                else "not_started"),
        current=active_page or fallback_page, pages=pages,
        pause_requested=library_store.is_chapter_paused(chapter_id),
    )


@app.post("/api/library/<chapter_id>/processing/<action>")
def library_chapter_processing_control(chapter_id, action):
    if action not in {"pause", "resume"}:
        return jsonify(error="إجراء التحكم غير مدعوم."), 404
    paused = action == "pause"
    if paused:
        chapter = library_store.get_chapter(chapter_id)
        if chapter is None:
            return jsonify(error="الفصل غير موجود في المكتبة المحلية."), 404
        page_results = list(
            refresh_chapter_translation_staleness(chapter_id, chapter).values()
        )
        if page_results and all(
            result and result.get("processing_status") == "complete"
            and not result.get("translation_inputs_stale")
            and _page_has_complete_translations(result) for result in page_results
        ):
            library_store.set_chapter_paused(chapter_id, False)
            return jsonify(chapter_id=chapter_id, paused=False,
                           message="اكتملت معالجة صفحات الفصل بالفعل.")
    if not library_store.set_chapter_paused(chapter_id, paused):
        return jsonify(error="الفصل غير موجود في المكتبة المحلية."), 404
    return jsonify(chapter_id=chapter_id, paused=paused,
                   message="سيتوقف الفصل بعد إنهاء المنطقة الحالية." if paused else "استؤنفت معالجة الفصل.")


@app.get("/api/library/<chapter_id>/pages/<int:page_number>/image")
def library_page_image(chapter_id, page_number):
    stored = library_store.read_page(chapter_id, page_number)
    if stored is None:
        return jsonify(error="الصفحة غير موجودة."), 404
    path, _content = stored
    return send_file(path, conditional=True, max_age=3600)


@app.get("/api/library/<chapter_id>/pages/<int:page_number>/result")
def library_page_result(chapter_id, page_number):
    """Return the latest durable OCR/translation state after a failed request."""
    if library_store.get_chapter(chapter_id) is None:
        return jsonify(error="الفصل غير موجود في المكتبة المحلية."), 404
    result = library_store.read_page_result(chapter_id, page_number)
    if result is None:
        return jsonify(error="لا توجد نتيجة محفوظة لهذه الصفحة."), 404
    return jsonify(**result)


@app.get("/api/library/<chapter_id>/pages/<int:page_number>/rendered")
def library_page_rendered(chapter_id, page_number):
    chapter = library_store.get_chapter(chapter_id)
    if chapter is None:
        return jsonify(error="الفصل غير موجود في المكتبة المحلية."), 404
    results = refresh_chapter_translation_staleness(chapter_id, chapter)
    result = results.get(int(page_number))
    if result and result.get("translation_inputs_stale"):
        return jsonify(error="تغيّر سياق هذه الصفحة؛ حدّث الترجمة قبل عرض المعاينة."), 409
    path = library_store.read_rendered_page(chapter_id, page_number)
    if path is None:
        return jsonify(error="لم يتم إنشاء معاينة مترجمة لهذه الصفحة."), 404
    return send_file(path, mimetype="image/png", conditional=True, max_age=3600)


def _dialogue_context_line(line):
    if not isinstance(line, dict) or line.get("include", True) is False:
        return ""
    text = str(line.get("text", "")).strip()
    hint = str(line.get("speaker_hint", "")).strip()
    if not text:
        return ""
    if line.get("region_type") == "external_text":
        source_label = f"Narration/caption: {text}"
    else:
        source_label = f"Speaker note: {hint}\nDialogue: {text}" if hint else f"Dialogue: {text}"
    confirmed = str(line.get("translated", "")).strip()
    if confirmed and line.get("review_status") == "human_reviewed" and has_arabic_letters(confirmed):
        source_label += (
            "\nConfirmed Arabic rendering of this earlier region (reference only): " + confirmed
        )
    return source_label


def _region_has_usable_translation(line):
    """Require actual Arabic output, whether generated or reviewed by a person."""
    if not isinstance(line, dict):
        return False
    translated = str(line.get("translated", "")).strip()
    return bool(translated) and has_arabic_letters(translated) and "\ufffd" not in translated


def _cached_translation_passes_output_policy(line):
    """Reuse an old engine result only when the active integrity rules accept it."""
    if not isinstance(line, dict):
        return False
    if line.get("include", True) is False:
        return True
    source = str(line.get("text", "")).strip()
    translated = str(line.get("translated", "")).strip()
    return bool(source and _is_usable_arabic_translation(translated)
                and not _is_implausibly_expanded_translation(source, translated)
                and not _has_latin_residue(translated))


def _region_is_complete(line):
    """An intentionally excluded region needs no inference to finish the page."""
    return isinstance(line, dict) and (
        line.get("include", True) is False or _region_has_usable_translation(line)
    )


def _region_box_overlap(first, second):
    """Geometry match for an OCR region across a detector refresh."""
    try:
        def bounds(item):
            box = item.get("bubble_bbox")
            if isinstance(box, (list, tuple)) and len(box) == 4:
                x1, y1, x2, y2 = (float(value) for value in box)
            else:
                x1, y1 = float(item["left"]), float(item["top"])
                x2 = x1 + float(item["width"])
                y2 = y1 + float(item["height"])
            if x2 <= x1 or y2 <= y1:
                return None
            return x1, y1, x2, y2
        a, b = bounds(first), bounds(second)
        if a is None or b is None:
            return 0.0
        intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(
            0, min(a[3], b[3]) - max(a[1], b[1])
        )
        area_a = (a[2] - a[0]) * (a[3] - a[1])
        area_b = (b[2] - b[0]) * (b[3] - b[1])
        return intersection / (area_a + area_b - intersection)
    except (KeyError, TypeError, ValueError, OverflowError):
        return 0.0


def _assign_stable_region_ids(previous_lines, detected_lines):
    """Keep unique IDs on geometrically matched regions, even if OCR text changes."""
    previous = [item for item in previous_lines if isinstance(item, dict)] if isinstance(previous_lines, list) else []
    used_previous = set()
    used_ids = set()
    changed = False
    for detected in detected_lines:
        if not isinstance(detected, dict):
            continue
        existing = detected.get("region_id")
        if isinstance(existing, str) and existing and existing not in used_ids:
            used_ids.add(existing)
            continue
        candidates = []
        for position, old in enumerate(previous):
            old_id = old.get("region_id")
            if (position in used_previous or not isinstance(old_id, str) or not old_id
                    or old_id in used_ids):
                continue
            overlap = _region_box_overlap(old, detected)
            same_type = old.get("region_type") == detected.get("region_type")
            same_text = " ".join(str(old.get("text", "")).split()).casefold() == (
                " ".join(str(detected.get("text", "")).split()).casefold()
            )
            if overlap >= 0.5 and (same_type or (same_text and overlap >= 0.7)):
                candidates.append((overlap, same_text, position, old_id))
        if candidates:
            _overlap, _same_text, position, region_id = max(candidates)
            used_previous.add(position)
        else:
            region_id = "reg_" + uuid.uuid4().hex
        detected["region_id"] = region_id
        used_ids.add(region_id)
        changed = True
    return changed


def _preserve_exact_human_edits(previous_lines, detected_lines):
    """Carry reviewed text and explicit exclusions across an OCR refresh by exact match."""
    if not isinstance(previous_lines, list) or not isinstance(detected_lines, list):
        return
    reusable = {}
    reviewed_by_id = {}
    for previous in previous_lines:
        if not isinstance(previous, dict):
            continue
        text = " ".join(str(previous.get("text", "")).split()).casefold()
        region_type = str(previous.get("region_type", "speech_bubble"))
        if not text:
            continue
        reviewed = previous.get("review_status") == "human_reviewed" and _region_has_usable_translation(previous)
        user_excluded = previous.get("include") is False and not previous.get("auto_exclusion_reason")
        has_layout = isinstance(previous.get("manual_layout_bbox"), list)
        has_manual_bubble_mask = isinstance(previous.get("manual_bubble_polygon"), list)
        if not (reviewed or user_excluded or has_layout or has_manual_bubble_mask):
            continue
        reusable.setdefault((region_type, text), []).append(previous)
        if isinstance(previous.get("region_id"), str):
            reviewed_by_id[previous["region_id"]] = previous

    for detected in detected_lines:
        if not isinstance(detected, dict):
            continue
        text = " ".join(str(detected.get("text", "")).split()).casefold()
        region_type = str(detected.get("region_type", "speech_bubble"))
        matches = reusable.get((region_type, text))
        matched_id = detected.get("region_id")
        previous = reviewed_by_id.pop(matched_id, None)
        if previous is not None:
            old_key = (str(previous.get("region_type", "speech_bubble")),
                       " ".join(str(previous.get("text", "")).split()).casefold())
            old_matches = reusable.get(old_key, [])
            if previous in old_matches:
                old_matches.remove(previous)
        elif not matches:
            continue
        if previous is None:
            previous = max(matches, key=lambda item: _region_box_overlap(item, detected))
            if (_region_box_overlap(previous, detected) < 0.5
                    and all(key in previous for key in ("left", "top", "width", "height"))):
                continue
            matches.remove(previous)
        if previous.get("include") is False:
            detected["include"] = False
        manual = previous.get("manual_layout_bbox")
        new_bubble = detected.get("bubble_bbox")
        if (isinstance(manual, list) and len(manual) == 4
                and isinstance(new_bubble, (list, tuple)) and len(new_bubble) == 4
                and new_bubble[0] <= manual[0] < manual[2] <= new_bubble[2]
                and new_bubble[1] <= manual[1] < manual[3] <= new_bubble[3]):
            detected["manual_layout_bbox"] = manual.copy()
        manual_polygon = previous.get("manual_bubble_polygon")
        if (isinstance(manual_polygon, list) and len(manual_polygon) >= 3
                and isinstance(new_bubble, (list, tuple)) and len(new_bubble) == 4):
            try:
                if all(new_bubble[0] <= point[0] < new_bubble[2]
                       and new_bubble[1] <= point[1] < new_bubble[3]
                       for point in manual_polygon):
                    detected["manual_bubble_polygon"] = [point.copy() for point in manual_polygon]
            except (TypeError, IndexError):
                pass
        if previous.get("review_status") == "human_reviewed" and _region_has_usable_translation(previous):
            detected["text"] = str(previous.get("text", detected["text"])).strip()
            detected["translated"] = str(previous["translated"]).strip()
            detected["review_status"] = "human_reviewed"
            if str(previous.get("speaker_hint", "")).strip():
                detected["speaker_hint"] = str(previous["speaker_hint"]).strip()


_DOMAIN_ONLY_RE = re.compile(
    r"(?:https?://)?(?:www\.)?[a-z0-9](?:[a-z0-9-]*[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+(?:/[^\s,;|·]*)?",
    re.IGNORECASE,
)
_SCANLATION_CREDIT_ROLE_RE = re.compile(
    r"\b(?:translator|proofreader|cleaner|redrawer|typesetter|letterer|editor|quality)\b",
    re.IGNORECASE,
)
_SCANLATION_CREDIT_STOP_WORDS = {"the", "a", "an", "and", "of", "to", "for", "is", "was", "are", "were"}


def _is_domain_only_credit(text):
    """Recognize standalone scanlation/source URLs without hiding prose."""
    value = str(text or "").strip()
    if not value:
        return False
    remainder = _DOMAIN_ONLY_RE.sub("", value)
    return bool(_DOMAIN_ONLY_RE.search(value)) and not re.sub(r"[\s,;|·()\[\]{}<>]+", "", remainder)


def _is_scanlation_role_credit(text):
    """Match compact external scanlation credits with an all-caps credited name."""
    value = str(text or "").strip()
    if not value or len(value) > 240 or re.search(r"[.!?؟…]$", value):
        return False
    for role in _SCANLATION_CREDIT_ROLE_RE.finditer(value):
        credited_name = re.match(r"\s*(?:[:|/—-]\s*)?([A-Za-z][A-Za-z0-9_-]{1,})", value[role.end():])
        if not credited_name:
            continue
        name = credited_name.group(1)
        if name.isupper() and name.casefold() not in _SCANLATION_CREDIT_STOP_WORDS:
            return True
    return False


def _is_series_byline_credit(text):
    """Keep an all-caps series title plus its multi-party byline unchanged."""
    value = " ".join(str(text or "").split())
    if not value or len(value) > 240 or re.search(r"[.!?؟…]$", value):
        return False
    byline = re.search(r"\s+by\s+(.+)$", value, re.IGNORECASE)
    if not byline or not re.search(r"[,&/]", byline.group(1)):
        return False
    title = value[:byline.start()].strip()
    if ":" not in title:
        return False
    letters = [character for character in title if character.isalpha()]
    if len(letters) < 8 or sum(character.isupper() for character in letters) / len(letters) < 0.85:
        return False
    credited_names = re.findall(r"\b[A-Z][A-Za-z0-9'&.-]*\b", byline.group(1))
    return len(credited_names) >= 2


def _is_source_promotion(text):
    """Recognize obvious scan-site notices, ads, recruitment and patron blocks."""
    value = " ".join(str(text or "").casefold().split())
    if not value:
        return False
    if "for faster update" in value:
        return True
    if "manhuaplus.org" in value and (
        "faster release" in value or "please read on" in value
    ):
        return True
    generic_site_promotion_markers = (
        "read only at", "read at", "read advance", "read the chapters only at",
        "read this series at", "reading this series at", "by reading this series",
        "byreadingthisseries", "faster updates", "support us", "support the team",
        "help us pay our staff",
        "premium chapters", "discord.gg", "recruitment", "actively recruiting",
    )
    if (_DOMAIN_ONLY_RE.search(value)
            and any(marker in value for marker in generic_site_promotion_markers)):
        return True

    genz_site_domains = ("genztoons.com", "genzupdates.com", "cenztoons.com")
    genz_promotion_markers = (
        "read only at", "read at", "read advance", "premium chapters",
        "faster updates", "support us", "help us pay our staff", "discord.gg",
        "recruitment", "actively recruiting",
    )
    if (any(domain in value for domain in genz_site_domains)
            and any(marker in value for marker in genz_promotion_markers)):
        return True
    if "original version" in value and "buy the comic" in value:
        return True
    if "commission your favorite series" in value:
        return True
    if "support us at" in value and any(
        service in value for service in ("paypal", "patreon", "ko-fi", "kofi")
    ):
        return True
    if "special thanks to our patrons" in value:
        return True
    if "recruiting" in value and any(
        role in value for role in ("translator", "editor", "proofreader", "cleaner")
    ):
        return True
    return False



def _is_fragmented_source_promotion(line, lines):
    """Match a short CTA fragment only when it overlaps a nearby domain-linked ad."""
    if not isinstance(line, dict) or line.get("region_type") != "external_text":
        return False
    value = " ".join(str(line.get("text", "")).casefold().split())
    cta_fragments = (
        "support the team", "support us", "by reading", "read only at", "read at",
        "faster updates",
    )
    if not any(fragment in value for fragment in cta_fragments):
        return False
    try:
        left = float(line.get("left", 0))
        top = float(line.get("top", 0))
        width = float(line.get("width", 0))
        height = float(line.get("height", 0))
    except (TypeError, ValueError):
        return False
    if width <= 0 or height <= 0:
        return False
    right, bottom = left + width, top + height

    for other in lines:
        if other is line or not isinstance(other, dict):
            continue
        if (other.get("region_type") != "external_text"
                or not _is_source_promotion(other.get("text"))):
            continue
        other_text = str(other.get("text", "")).casefold()
        if not _DOMAIN_ONLY_RE.search(other_text):
            continue
        try:
            other_left = float(other.get("left", 0))
            other_top = float(other.get("top", 0))
            other_width = float(other.get("width", 0))
            other_height = float(other.get("height", 0))
        except (TypeError, ValueError):
            continue
        if other_width <= 0 or other_height <= 0:
            continue
        overlap_x = max(0.0, min(right, other_left + other_width) - max(left, other_left))
        horizontal_ratio = overlap_x / min(width, other_width)
        other_bottom = other_top + other_height
        vertical_gap = max(0.0, max(top, other_top) - min(bottom, other_bottom))
        if horizontal_ratio >= 0.35 and vertical_gap <= 80:
            return True
    return False


def _is_explicit_embedded_source_promotion(text):
    """Match unambiguous source-site calls to action printed inside a speech bubble."""
    value = " ".join(str(text or "").casefold().split())
    return (
        "want to read a new chapter" in value
        and "join our discord" in value
        and ("updates in real time" in value or "discord.gg/" in value)
    )


def _is_ambiguous_short_external_fragment(line):
    """Preserve tiny OCR scraps and brand marks that have no reliable translation."""
    if not isinstance(line, dict) or line.get("region_type") != "external_text":
        return False
    value = " ".join(str(line.get("text", "")).split())
    letters = re.sub(r"[^A-Za-z]", "", value)
    if not letters:
        return False
    common_short_words = {
        "OK", "NO", "GO", "OH", "HI", "HA", "AH", "EH", "OW", "YES", "YEP",
        "HEY", "RUN", "DIE", "HELP", "STOP", "FIRE", "WAIT", "WHAT", "WHO",
        "WHY", "LOL", "OMG", "WTF", "SOS", "NOW", "ALL", "THE", "YOU", "NOT",
        "ARE", "NEW", "OLD", "END", "WAR", "WIN", "ONE", "TWO", "DAY", "BAD",
    }
    if letters.isupper() and len(letters) <= 4 and letters not in common_short_words:
        return True
    if re.fullmatch(r"[A-Z][a-z][A-Z]", value):
        try:
            area = max(0, float(line.get("width", 0))) * max(0, float(line.get("height", 0)))
        except (TypeError, ValueError):
            area = 0
        return area <= 5000
    return False


def _is_low_confidence_external_region(line):
    """Keep uncertain OCR captions unchanged until a reader reviews them."""
    if not isinstance(line, dict) or line.get("region_type") != "external_text":
        return False
    try:
        confidence = float(line.get("confidence"))
    except (TypeError, ValueError):
        return False
    return 0.0 <= confidence < 0.90 and bool(str(line.get("text", "")).strip())


def _page_has_complete_translations(result):
    """Require usable output for each included OCR region before rendering."""
    if not isinstance(result, dict):
        return False
    if result.get("no_text_detected"):
        return True
    lines = result.get("translated_lines")
    return isinstance(lines, list) and bool(lines) and all(_region_is_complete(line) for line in lines)


def _region_translation_instruction(region_type):
    if region_type == "external_text":
        return (
            "Current region is visible English narration/caption outside a speech bubble. "
            "Translate it as concise, natural Arabic narration and preserve its complete meaning; "
            "do not treat it as spoken dialogue."
        )
    return (
        "Current region is dialogue inside a speech bubble. Translate the full utterance "
        "as natural Arabic speech, preserving its tone and complete meaning."
    )


def _low_ocr_confidence_instruction(confidence):
    """Tell contextual translation models when recognized source may be noisy."""
    try:
        value = float(confidence)
    except (TypeError, ValueError):
        return ""
    if not 0.0 <= value < 0.60:
        return ""
    return (
        f"Current OCR confidence instruction: confidence is low ({value:.0%}) and recognized English may contain errors. "
        "Use nearby dialogue only to resolve a clear ambiguity; translate the recoverable meaning and "
        "do not invent unreadable words or missing facts."
    )


def _build_translation_context(region_type, dialogue_context="", confidence=None):
    """Assemble trusted region and OCR guidance before prior comic dialogue."""
    return "\n".join(part for part in (
        _region_translation_instruction(region_type),
        _low_ocr_confidence_instruction(confidence),
        str(dialogue_context or "").strip(),
    ) if part)


def recent_chapter_context(chapter_id, page_number, limit=3, chapter=None):
    """Collect only actual OCR dialogue from the latest completed earlier pages."""
    try:
        limit = max(0, int(limit))
    except (TypeError, ValueError):
        limit = 3
    if limit == 0:
        return ""
    chapter = chapter or library_store.get_chapter(chapter_id)
    if not chapter:
        return ""
    collected = []
    earlier_pages = sorted(
        (page for page in chapter.get("pages", [])
         if int(page.get("page_number", 0)) < int(page_number)),
        key=lambda page: int(page.get("page_number", 0)), reverse=True,
    )
    # Search a short tail so new translations can still find recent dialogue
    # when the immediately preceding pages are unprocessed or stale.
    for page in earlier_pages[:12]:
        previous = library_store.read_page_result(chapter_id, int(page["page_number"]))
        if (not previous or previous.get("processing_status") != "complete"
                or previous.get("translation_inputs_stale")
                or previous.get("pipeline_version") != PAGE_PIPELINE_VERSION
                or previous.get("translation_context_version") != PAGE_TRANSLATION_CONTEXT_VERSION):
            continue
        lines = previous.get("translated_lines") or []
        page_context = [
            _dialogue_context_line(line)
            for line in lines if _dialogue_context_line(line)
        ]
        if page_context:
            remaining = limit - len(collected)
            # Pages are visited newest first. Prefix older dialogue ahead of
            # the already collected newer tail, preserving chronological order.
            collected = page_context[-remaining:] + collected
        if len(collected) >= limit:
            break
    return "\n".join(collected)


def chapter_glossary_signature(chapter):
    """Fingerprint global plus series terms so completed pages refresh on edits."""
    scope = chapter.get("series_title") or chapter.get("series_key") or chapter["title"]
    terms = library_store.list_glossary(scope)
    return hashlib.sha256(
        json.dumps(terms, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]


def page_translation_context_signature(previous_context, lines):
    """Fingerprint source dialogue and user speaker notes feeding page context."""
    context_inputs = [
        {
            "text": " ".join(str(line.get("text", "")).split()),
            "speaker_hint": " ".join(str(line.get("speaker_hint", "")).split()),
            "region_type": line.get("region_type", "speech_bubble"),
            "ocr_confidence": (
                round(float(line.get("confidence")), 4)
                if isinstance(line.get("confidence"), (int, float)) else None
            ),
            "confirmed_translation": (
                " ".join(str(line.get("translated", "")).split())
                if line.get("review_status") == "human_reviewed" else ""
            ),
        }
        for line in lines
    ]
    payload = json.dumps(
        {"previous": " ".join(str(previous_context).split()), "lines": context_inputs},
        ensure_ascii=False, sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def refresh_chapter_translation_staleness(chapter_id, chapter):
    """Mark saved pages and previews stale when their inputs no longer match."""
    work_scope = chapter.get("series_key") or chapter.get("series_title") or chapter["title"]
    glossary_signature = chapter_glossary_signature(chapter)
    engine_signature = active_translation_recipe_signature()
    refreshed = {}
    for page in chapter["pages"]:
        page_number = int(page["page_number"])
        result = library_store.read_page_result(chapter_id, page_number)
        if not result:
            continue
        if result.get("processing_status") == "complete":
            previous_context = recent_chapter_context(chapter_id, page_number, chapter=chapter)
            context_signature = page_translation_context_signature(
                previous_context, result.get("translated_lines", [])
            )
            stale = (
                result.get("pipeline_version") != PAGE_PIPELINE_VERSION
                or result.get("translation_context_version") != PAGE_TRANSLATION_CONTEXT_VERSION
                or result.get("translation_work_scope") != work_scope
                or result.get("translation_glossary_signature") != glossary_signature
                or result.get("translation_context_signature") != context_signature
                or (engine_signature is not None and not result.get("no_text_detected")
                    and result.get("translation_engine_signature") != engine_signature)
            )
            if stale:
                if not result.get("translation_inputs_stale") or not result.get("render_stale"):
                    result["translation_inputs_stale"] = True
                    result["render_stale"] = True
                    library_store.save_page_result(chapter_id, page_number, result)
            elif result.pop("translation_inputs_stale", None):
                library_store.save_page_result(chapter_id, page_number, result)
        if result.get("render_pipeline_version") != RENDER_PIPELINE_VERSION:
            if not result.get("render_stale"):
                result["render_stale"] = True
                library_store.save_page_result(chapter_id, page_number, result)
        refreshed[page_number] = result
    return refreshed


def _page_translation_workers():
    try:
        configured = int(os.getenv("MANGA_TRANSLATION_WORKERS", "2"))
    except (TypeError, ValueError):
        configured = 2
    return min(4, max(1, configured))


def process_local_chapter_page(chapter_id, page_number, time_budget=None):
    page_started = time.perf_counter()
    deadline = page_started + time_budget if time_budget else None

    def budget_exceeded():
        return deadline is not None and time.perf_counter() > deadline

    chapter = library_store.get_chapter(chapter_id)
    stored = library_store.read_page(chapter_id, page_number)
    if chapter is None or stored is None:
        return None, (jsonify(error="الفصل أو الصفحة غير موجودة."), 404)
    work_scope = chapter.get("series_key") or chapter.get("series_title") or chapter["title"]
    glossary_signature = chapter_glossary_signature(chapter)
    engine_signature = active_translation_recipe_signature()
    cached = library_store.read_page_result(chapter_id, page_number)
    if cached is not None and isinstance(cached.get("translated_lines"), list):
        if _assign_stable_region_ids([], cached["translated_lines"]):
            library_store.save_page_result(chapter_id, page_number, cached)
    previous_page_context = recent_chapter_context(chapter_id, page_number, chapter=chapter)
    cached_lines = cached.get("translated_lines", []) if cached else []
    context_signature = page_translation_context_signature(
        previous_page_context, cached_lines if isinstance(cached_lines, list) else []
    )
    if (cached is not None
            and cached.get("pipeline_version") == PAGE_PIPELINE_VERSION
            and cached.get("processing_status") == "complete"
            and cached.get("translation_context_version") == PAGE_TRANSLATION_CONTEXT_VERSION
            and cached.get("translation_work_scope") == work_scope
            and cached.get("translation_glossary_signature") == glossary_signature
            and cached.get("translation_context_signature") == context_signature
            and (cached.get("no_text_detected") or (
                isinstance(cached.get("translated_lines"), list)
                and bool(cached["translated_lines"])
                and all(_region_is_complete(line) for line in cached["translated_lines"])
            ))
            and (engine_signature is None or cached.get("no_text_detected")
                 or cached.get("translation_engine_signature") == engine_signature)
            and not cached.get("translation_inputs_stale")):
        return cached, None
    if (cached is not None and cached.get("pipeline_version") == PAGE_PIPELINE_VERSION
            and isinstance(cached.get("translated_lines"), list)
            and (cached.get("no_text_detected") or cached.get("ocr_provider") not in {None, "pending"})
            and cached.get("processing_status") in {"translation_in_progress", "complete", "paused"}):
        # Resume from saved OCR and completed utterances after a restart or a
        # single-line translation failure; do not redo successful work.
        result = cached
        translated_lines = result["translated_lines"]
        translation_inputs_changed = (
            result.get("translation_context_version") != PAGE_TRANSLATION_CONTEXT_VERSION
            or result.get("translation_work_scope") != work_scope
            or result.get("translation_glossary_signature") != glossary_signature
            or result.get("translation_context_signature") != context_signature
            or (engine_signature is not None and not result.get("no_text_detected")
                and result.get("translation_engine_signature") != engine_signature)
        )
        if translation_inputs_changed:
            # Translation context, series identity, or glossary can change
            # independently of OCR. Preserve human edits and reuse detections.
            refresh_reasons = []
            if result.get("translation_context_version") != PAGE_TRANSLATION_CONTEXT_VERSION:
                refresh_reasons.append("تحديث قواعد السياق")
            if result.get("translation_work_scope") != work_scope:
                refresh_reasons.append("تغيير السلسلة")
            if result.get("translation_glossary_signature") != glossary_signature:
                refresh_reasons.append("تحديث القاموس")
            if result.get("translation_context_signature") != context_signature:
                refresh_reasons.append("تغير نصوص الحوار أو ملاحظات المتحدث السابقة")
            if (engine_signature is not None and not result.get("no_text_detected")
                    and result.get("translation_engine_signature") != engine_signature):
                refresh_reasons.append("تغيير محرك الترجمة أو وصفته")
            result["translation_refresh_reason"] = "، ".join(refresh_reasons)
            rendered_translation_changed = False
            only_engine_changed = (
                engine_signature is not None
                and result.get("translation_engine_signature") != engine_signature
                and result.get("translation_context_version") == PAGE_TRANSLATION_CONTEXT_VERSION
                and result.get("translation_work_scope") == work_scope
                and result.get("translation_glossary_signature") == glossary_signature
                and result.get("translation_context_signature") == context_signature
            )
            for line in translated_lines:
                reusable = only_engine_changed and _cached_translation_passes_output_policy(line)
                if line.get("review_status") != "human_reviewed" and not reusable:
                    rendered_translation_changed = rendered_translation_changed or bool(line.get("translated"))
                    line["translated"] = ""
                    line.pop("translation_elapsed_seconds", None)
                    line.pop("translation_attempt_elapsed_seconds", None)
            if rendered_translation_changed:
                result["render_stale"] = True
            result["translation_elapsed_seconds"] = 0.0
            result["processing_status"] = "translation_in_progress"
            result["translation_work_scope"] = work_scope
            result["translation_context_version"] = PAGE_TRANSLATION_CONTEXT_VERSION
            result["translation_glossary_signature"] = glossary_signature
            result["translation_context_signature"] = context_signature
            if engine_signature is not None:
                result["translation_engine_signature"] = engine_signature
            result.pop("last_translation_error", None)
            result["original"] = "\n".join(item.get("text", "") for item in translated_lines)
            result["translated"] = "\n".join(
                item.get("translated", "") for item in translated_lines if item.get("translated")
            )
            completed_region_count = sum(_region_is_complete(line) for line in translated_lines)
            result["progress"] = {"stage": "translation", "completed_regions": completed_region_count,
                                  "total_regions": len(translated_lines)}
            library_store.save_page_result(chapter_id, page_number, result)
    else:
        _path, image_bytes = stored
        result = {
            "pipeline_version": PAGE_PIPELINE_VERSION,
            "translation_context_version": PAGE_TRANSLATION_CONTEXT_VERSION,
            "translation_work_scope": work_scope,
            "translation_glossary_signature": glossary_signature,
            "translation_context_signature": context_signature,
            "translation_engine_signature": engine_signature,
            "translation_inputs_stale": False,
            "processing_status": "ocr_in_progress",
            "original": "",
            "translated": "",
            "ocr_lines": [],
            "dialogue_regions": [],
            "nontranslatable_annotations": [],
            "translated_lines": [],
            "ocr_provider": "pending",
            "ocr_elapsed_seconds": None,
            "ocr_timing_breakdown": None,
            "translation_elapsed_seconds": 0.0,
            "progress": {"stage": "bubble_and_external_text_detection", "completed_regions": 0,
                         "total_regions": 0},
        }
        if cached is not None:
            result["render_stale"] = True
        library_store.save_page_result(chapter_id, page_number, result)
        if library_store.is_chapter_paused(chapter_id):
            result["processing_status"] = "paused"
            result["progress"] = {"stage": "paused_before_ocr", "completed_regions": 0,
                                  "total_regions": 0}
            library_store.save_page_result(chapter_id, page_number, result)
            return result, None
        try:
            update_local_page_runtime(
                chapter_id, page_number, stage="ocr", started_monotonic=time.monotonic(),
            )
            ocr_result = run_local_ocr(
                image_bytes, chapter["reading_direction"],
                include_external=chapter.get("series_type") in SUPPORTED_VERTICAL_COMIC_TYPES,
            )
        except RuntimeError as exc:
            if str(exc) != "لم يعثر OCR المحلي على نص واضح داخل الفقاعات أو خارجها.":
                raise
            result.update({
                "processing_status": "complete",
                "translation_glossary_signature": glossary_signature,
                "translation_context_signature": context_signature,
                "translation_inputs_stale": False,
                "no_text_detected": True,
                "ocr_provider": "rapidocr_onnxruntime_cpu",
                "progress": {"stage": "complete", "completed_regions": 0, "total_regions": 0},
                "page_processing_elapsed_seconds": round(time.perf_counter() - page_started, 4),
            })
            library_store.save_page_result(chapter_id, page_number, result)
            return result, None
        finally:
            update_local_page_runtime(chapter_id, page_number)
        translated_lines = [
            {**line, "index": index, "translated": ""}
            for index, line in enumerate(ocr_result.get("regions", ocr_result["lines"]), 1)
        ]
        _assign_stable_region_ids(
            cached.get("translated_lines", []) if isinstance(cached, dict) else [],
            translated_lines,
        )
        _preserve_exact_human_edits(
            cached.get("translated_lines", []) if isinstance(cached, dict) else [],
            translated_lines,
        )
        context_signature = page_translation_context_signature(previous_page_context, translated_lines)
        result = {
            "pipeline_version": PAGE_PIPELINE_VERSION,
            "translation_context_version": PAGE_TRANSLATION_CONTEXT_VERSION,
            "translation_work_scope": work_scope,
            "translation_glossary_signature": glossary_signature,
            "translation_context_signature": context_signature,
            "translation_engine_signature": engine_signature,
            "translation_inputs_stale": False,
            "processing_status": "translation_in_progress",
            "original": ocr_result["text"],
            "translated": "",
            "ocr_lines": ocr_result["lines"],
            "dialogue_regions": ocr_result.get("regions", []),
            "nontranslatable_annotations": ocr_result.get("nontranslatable_annotations", []),
            "translated_lines": translated_lines,
            "ocr_provider": ocr_result["provider"],
            "ocr_elapsed_seconds": ocr_result["elapsed_seconds"],
            "ocr_timing_breakdown": _ocr_timing_breakdown(ocr_result),
            "progress": {"stage": "translation", "completed_regions": 0,
                         "total_regions": len(translated_lines)},
        }
        library_store.save_page_result(chapter_id, page_number, result)

    if deadline is not None and budget_exceeded() and result.get("processing_status") != "complete":
        return result, None

    completed_region_count = sum(_region_is_complete(line) for line in translated_lines)
    # Scanlation credits and series bylines are often detected as external text.
    # Keep those regions in the source form; translated creator lists are not
    # narrative and can be corrupted even when OCR confidence is high.
    # Exclude only untouched credit regions;
    # explicit user choices and dialogue are never changed.
    auto_exclusions = False
    for line in translated_lines:
        if (isinstance(line, dict) and line.get("include") is not False
                and "include" not in line
                and (LocalRapidOCR.is_likely_nonverbal_effect(line.get("text"))
                     or (line.get("region_type") == "speech_bubble"
                         and _is_explicit_embedded_source_promotion(line.get("text")))
                     or (line.get("region_type") == "external_text"
                         and (_is_source_promotion(line.get("text"))
                              or _is_fragmented_source_promotion(line, translated_lines)
                              or _is_domain_only_credit(line.get("text"))
                              or _is_scanlation_role_credit(line.get("text"))
                              or _is_series_byline_credit(line.get("text"))
                              or _is_ambiguous_short_external_fragment(line)
                              or _is_low_confidence_external_region(line))))):
            line["include"] = False
            line["auto_exclusion_reason"] = (
                "sound_effect" if LocalRapidOCR.is_likely_nonverbal_effect(line.get("text"))
                else "source_promotion" if (
                    _is_explicit_embedded_source_promotion(line.get("text"))
                    or (line.get("region_type") == "external_text"
                        and (_is_source_promotion(line.get("text"))
                             or _is_fragmented_source_promotion(line, translated_lines)))
                )
                else "source_credit" if _is_domain_only_credit(line.get("text"))
                else "scanlation_credit" if _is_scanlation_role_credit(line.get("text"))
                else "series_byline_credit" if _is_series_byline_credit(line.get("text"))
                else "ambiguous_short_external_fragment" if _is_ambiguous_short_external_fragment(line)
                else "low_ocr_confidence"
            )
            line["translated"] = ""
            line.pop("translation_attempt_elapsed_seconds", None)
            auto_exclusions = True
    if auto_exclusions:
        completed_region_count = sum(_region_is_complete(line) for line in translated_lines)
        result["progress"] = {"stage": "translation", "completed_regions": completed_region_count,
                              "total_regions": len(translated_lines)}
        library_store.save_page_result(chapter_id, page_number, result)
    pending_regions = []
    for index, line in enumerate(translated_lines, 1):
        if isinstance(line, dict) and line.get("include", True) is False:
            continue
        if _region_has_usable_translation(line):
            continue
        line["translated"] = ""
        line.pop("review_status", None)
        local_context = "\n".join(
            _dialogue_context_line(item)
            for item in translated_lines[max(0, index - 3):index - 1]
            if _dialogue_context_line(item)
        )
        context = "\n".join(part for part in (previous_page_context, local_context) if part)
        if str(line.get("speaker_hint", "")).strip():
            context = f"User-provided speaker note for current dialogue: {line['speaker_hint'].strip()}\n" + context
        context = _build_translation_context(
            line.get("region_type"), context, line.get("confidence")
        )
        pending_regions.append((index, line, context))

    workers = _page_translation_workers()
    translation_run_started = time.perf_counter()
    translation_units = []
    cursor = 0
    batch_limit = _index_batch_size() if CURRENT_TRANSLATION_ENGINE == "index_translate_9b" else 1
    while cursor < len(pending_regions):
        first_index, first_line, _first_context = pending_regions[cursor]
        unit = [pending_regions[cursor]]
        cursor += 1
        if first_line.get("region_type", "speech_bubble") == "speech_bubble" and batch_limit > 1:
            while cursor < len(pending_regions) and len(unit) < batch_limit:
                next_index, next_line, _next_context = pending_regions[cursor]
                if (next_index != unit[-1][0] + 1
                        or next_line.get("region_type", "speech_bubble") != "speech_bubble"):
                    break
                unit.append(pending_regions[cursor])
                cursor += 1
        translation_units.append(unit)

    next_pending = 0
    in_flight = {}
    failures = []
    pause_requested = False
    budget_yielded = False

    def submit_available(pool):
        nonlocal next_pending, pause_requested, budget_yielded
        while next_pending < len(translation_units) and len(in_flight) < workers:
            if library_store.is_chapter_paused(chapter_id):
                pause_requested = True
                break
            if budget_exceeded():
                budget_yielded = True
                break
            unit = translation_units[next_pending]
            next_pending += 1
            requests_for_unit = [
                {
                    "text": line["text"], "context": context,
                    "region_type": line.get("region_type", "speech_bubble"),
                    "chapter_title": chapter["title"], "page_index": page_number,
                    "chapter_id": chapter_id, "region_index": index,
                    "bubble_id": line.get("bubble_id"), "work_scope": work_scope,
                    "glossary_scope": chapter.get("series_title"),
                }
                for index, line, context in unit
            ]
            started = time.perf_counter()
            future = pool.submit(_translate_region_group_with_cloud_fallback, requests_for_unit)
            in_flight[future] = (unit, started)

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="manga-translate") as pool:
        submit_available(pool)
        while in_flight:
            current_unit, current_started = min(in_flight.values(), key=lambda item: item[1])
            result["progress"] = {
                "stage": "translation",
                "completed_regions": completed_region_count,
                "total_regions": len(translated_lines),
                "current_region": current_unit[0][0],
            }
            library_store.save_page_result(chapter_id, page_number, result)
            update_local_page_runtime(
                chapter_id, page_number, stage="translation",
                current_region=current_unit[0][0], started_monotonic=current_started,
            )
            done, _pending = concurrent.futures.wait(
                in_flight, return_when=concurrent.futures.FIRST_COMPLETED,
            )
            for future in done:
                unit, started = in_flight.pop(future)
                elapsed = time.perf_counter() - started
                try:
                    outputs = future.result()
                    if not isinstance(outputs, list) or len(outputs) != len(unit):
                        raise RuntimeError("The translation engine returned a mismatched region count.")
                    for (index, line, _context), translated in zip(unit, outputs):
                        if not isinstance(translated, str) or not has_arabic_letters(translated):
                            raise RuntimeError("The translation engine did not return usable Arabic text.")
                        line["translated"] = translated.strip()
                        line["translation_elapsed_seconds"] = round(elapsed, 4)
                        line.pop("translation_attempt_elapsed_seconds", None)
                        completed_region_count += 1
                except Exception as exc:
                    for index, line, _context in unit:
                        line["translation_attempt_elapsed_seconds"] = round(elapsed, 4)
                    result["processing_status"] = "translation_in_progress"
                    failures.append((unit[0][0], exc))
                result["progress"] = {
                    "stage": "translation",
                    "completed_regions": completed_region_count,
                    "total_regions": len(translated_lines),
                }
                library_store.save_page_result(chapter_id, page_number, result)

            if not failures and not pause_requested:
                submit_available(pool)

    result["translation_elapsed_seconds"] = round(
        float(result.get("translation_elapsed_seconds", 0.0))
        + time.perf_counter() - translation_run_started, 4
    )
    update_local_page_runtime(chapter_id, page_number)
    if budget_yielded and not failures and not pause_requested:
        result["processing_status"] = "translation_in_progress"
        result["progress"] = {"stage": "translation",
                              "completed_regions": completed_region_count,
                              "total_regions": len(translated_lines)}
        result["page_processing_elapsed_seconds"] = round(time.perf_counter() - page_started, 4)
        library_store.save_page_result(chapter_id, page_number, result)
        return result, None
    if failures:
        failed_index, failed_exception = failures[0]
        result["last_translation_error"] = {
            "region_index": failed_index,
            "message": str(failed_exception).strip()[:300] or type(failed_exception).__name__,
        }
        result["page_processing_elapsed_seconds"] = round(time.perf_counter() - page_started, 4)
        library_store.save_page_result(chapter_id, page_number, result)
        raise failed_exception
    if pause_requested:
        result["processing_status"] = "paused"
        result["progress"] = {
            "stage": "paused",
            "completed_regions": completed_region_count,
            "total_regions": len(translated_lines),
        }
        library_store.save_page_result(chapter_id, page_number, result)
        return result, None
    result.pop("last_translation_error", None)

    result["processing_status"] = "complete"
    result.pop("last_translation_error", None)
    result["translation_context_version"] = PAGE_TRANSLATION_CONTEXT_VERSION
    result["translation_work_scope"] = work_scope
    result["translation_glossary_signature"] = glossary_signature
    result["translation_context_signature"] = context_signature
    if engine_signature is not None:
        result["translation_engine_signature"] = engine_signature
    result["translation_inputs_stale"] = False
    result["progress"] = {"stage": "complete",
                          "completed_regions": completed_region_count,
                          "total_regions": len(translated_lines)}
    result["translated"] = "\n".join(line["translated"] for line in translated_lines if line.get("translated"))
    result["page_processing_elapsed_seconds"] = round(time.perf_counter() - page_started, 4)
    library_store.save_page_result(chapter_id, page_number, result)
    return result, None


@app.post("/api/library/<chapter_id>/pages/<int:page_number>/translate")
def library_translate_page(chapter_id, page_number):
    try:
        payload = request.get_json(silent=True) or {}
        requested_budget = payload.get("time_budget")
        time_budget = requested_budget if isinstance(requested_budget, (int, float)) else None
        if time_budget is not None and not 5 <= time_budget <= 55:
            time_budget = 50
        with _page_processing_lock(chapter_id, page_number):
            result, error = process_local_chapter_page(chapter_id, page_number, time_budget=time_budget)
        if error:
            return error
        return jsonify(**result)
    except (OSError, ValueError, RuntimeError) as exc:
        app.logger.exception("Local page translation failed")
        return jsonify(error=f"تعذرت معالجة الصفحة محلياً: {exc}"), 500


@app.post("/api/library/<chapter_id>/pages/<int:page_number>/review")
@_serialize_page_route
def library_review_page(chapter_id, page_number):
    result = library_store.read_page_result(chapter_id, page_number)
    if result is None:
        return jsonify(error="شغّل OCR والترجمة قبل مراجعة الصفحة."), 409
    chapter = library_store.get_chapter(chapter_id)
    payload = request.get_json(silent=True) or {}
    edits = payload.get("regions")
    if not isinstance(edits, list) or len(edits) > 300:
        return jsonify(error="صيغة مراجعة المناطق غير صالحة."), 400
    _assign_stable_region_ids([], result["translated_lines"])
    by_index = {item["index"]: item for item in result["translated_lines"]}
    merge_groups = payload.get("merge_regions", [])
    split_requests = payload.get("split_regions", [])
    bubble_mask_edits = payload.get("bubble_mask_edits", [])
    merge_items = []
    if not isinstance(merge_groups, list) or len(merge_groups) > 50:
        return jsonify(error="قائمة دمج المناطق غير صالحة."), 400
    if not isinstance(split_requests, list) or len(split_requests) > 50:
        return jsonify(error="قائمة تقسيم المناطق غير صالحة."), 400
    if not isinstance(bubble_mask_edits, list) or len(bubble_mask_edits) > 50:
        return jsonify(error="قائمة حدود الأقنعة غير صالحة."), 400
    used_merge_ids = set()
    positions = {item["region_id"]: position
                 for position, item in enumerate(result["translated_lines"])}
    for group in merge_groups:
        if (not isinstance(group, list) or len(group) != 2
                or any(not isinstance(value, str) for value in group)
                or group[0] == group[1] or used_merge_ids.intersection(group)):
            return jsonify(error="كل عملية دمج تحتاج منطقتين مختلفتين وغير مكررتين."), 400
        first = next((item for item in result["translated_lines"] if item["region_id"] == group[0]), None)
        second = next((item for item in result["translated_lines"] if item["region_id"] == group[1]), None)
        if (first is None or second is None or not first.get("bubble_id")
                or first.get("bubble_id") != second.get("bubble_id")
                or first.get("region_type") != "speech_bubble"
                or second.get("region_type") != "speech_bubble"
                or abs(positions[group[0]] - positions[group[1]]) != 1):
            return jsonify(error="يمكن دمج منطقتين متجاورتين من فقاعة الحوار نفسها فقط."), 400
        if positions[group[0]] > positions[group[1]]:
            first, second = second, first
            group = [group[1], group[0]]
        used_merge_ids.update(group)
        merge_items.append((group, first, second))
    split_items = []
    used_split_ids = set()
    for request_item in split_requests:
        if not isinstance(request_item, dict):
            return jsonify(error="بيانات تقسيم المنطقة غير صالحة."), 400
        region_id = request_item.get("region_id")
        first_text = request_item.get("first_text")
        second_text = request_item.get("second_text")
        axis = request_item.get("axis")
        ratio = request_item.get("ratio", 50)
        target = next((item for item in result["translated_lines"]
                       if item.get("region_id") == region_id), None)
        if (not isinstance(region_id, str) or region_id in used_split_ids
                or not isinstance(first_text, str) or not isinstance(second_text, str)
                or not first_text.strip() or not second_text.strip()
                or len(first_text) > 500 or len(second_text) > 500
                or not isinstance(axis, str) or axis not in {"x", "y"}
                or type(ratio) is not int or not 20 <= ratio <= 80
                or target is None
                or target.get("region_type") not in {"speech_bubble", "external_text"}
                or (target.get("region_type") == "speech_bubble" and target.get("bubble_id") is None)
                or region_id in used_merge_ids):
            return jsonify(error="لا يمكن تقسيم هذه المنطقة. تحقق من النصين ونوع المنطقة والمحور والنسبة."), 400
        raw_boxes = target.get("ocr_boxes") or []
        if not isinstance(raw_boxes, list):
            return jsonify(error="بيانات صناديق OCR لهذه المنطقة غير صالحة."), 400
        normalized_boxes = []
        for box in raw_boxes:
            if not isinstance(box, (list, tuple)):
                continue
            try:
                values = [float(value) for value in box]
            except (TypeError, ValueError, OverflowError):
                continue
            if (len(values) != 4 or not all(math.isfinite(value) for value in values)
                    or values[2] <= 0 or values[3] <= 0):
                continue
            normalized_boxes.append(values)
        if len(normalized_boxes) < 2:
            return jsonify(error="يلزم وجود صندوقي OCR على الأقل داخل الفقاعة حتى يكون التقسيم مكانياً آمناً."), 400
        start_index = 0 if axis == "x" else 1
        extent_index = 2 if axis == "x" else 3
        centers = [box[start_index] + box[extent_index] / 2
                   for box in normalized_boxes]
        ordered_centers = sorted(set(centers))
        if len(ordered_centers) < 2:
            return jsonify(error="صناديق OCR متراكبة ولا يمكن فصلها مكانياً على المحور المختار."), 400
        low, high = ordered_centers[0], ordered_centers[-1]
        desired_cut = low + (high - low) * ratio / 100
        cut = min(((left + right) / 2 for left, right in zip(ordered_centers, ordered_centers[1:])),
                  key=lambda value: abs(value - desired_cut))
        first_indexes = [index for index, center in enumerate(centers) if center < cut]
        second_indexes = [index for index, center in enumerate(centers) if center >= cut]
        if not first_indexes or not second_indexes:
            return jsonify(error="لم ينتج التقسيم مجموعتين مكانيّتين واضحتين من صناديق OCR."), 400
        polygons = target.get("ocr_polygons") or []
        if not isinstance(polygons, list):
            polygons = []

        def child_geometry(indices):
            child_boxes = [normalized_boxes[index] for index in indices]
            child_polygons = [polygons[index] for index in indices if index < len(polygons)]
            left = min(box[0] for box in child_boxes)
            top = min(box[1] for box in child_boxes)
            right = max(box[0] + box[2] for box in child_boxes)
            bottom = max(box[1] + box[3] for box in child_boxes)
            return child_boxes, child_polygons, left, top, right, bottom

        first_geometry = child_geometry(first_indexes)
        second_geometry = child_geometry(second_indexes)
        split_items.append((target, first_text.strip(), second_text.strip(),
                            first_geometry, second_geometry))
        used_split_ids.add(region_id)
    validated_mask_edits = []
    used_mask_regions = set()
    used_mask_bubbles = set()
    for mask_edit in bubble_mask_edits:
        if not isinstance(mask_edit, dict):
            return jsonify(error="بيانات تعديل قناع الفقاعة غير صالحة."), 400
        region_id = mask_edit.get("region_id")
        polygon = mask_edit.get("polygon")
        target = next((item for item in result["translated_lines"]
                       if item.get("region_id") == region_id), None)
        if (not isinstance(region_id, str) or region_id in used_mask_regions
                or target is None or target.get("region_type") != "speech_bubble"
                or target.get("bubble_id") is None or region_id in used_merge_ids
                or region_id in used_split_ids):
            return jsonify(error="اختر منطقة فقاعة صالحة واحدة لتعديل حدود قناعها."), 400
        bubble_box = target.get("bubble_bbox")
        if (not isinstance(bubble_box, (list, tuple)) or len(bubble_box) != 4
                or any(type(value) is not int for value in bubble_box)
                or bubble_box[2] <= bubble_box[0] or bubble_box[3] <= bubble_box[1]):
            return jsonify(error="حدود الفقاعة الأصلية غير صالحة لتعديل القناع."), 400
        bubble_key = (target.get("bubble_id"), tuple(bubble_box))
        if bubble_key in used_mask_bubbles:
            return jsonify(error="عدّل قناع الفقاعة مرة واحدة في كل عملية."), 400
        parsed_polygon = None
        if polygon is not None:
            if (not isinstance(polygon, list) or not 3 <= len(polygon) <= 120
                    or not isinstance(bubble_box, (list, tuple)) or len(bubble_box) != 4):
                return jsonify(error="ارسم ثلاثة نقاط أو أكثر داخل حدود الفقاعة."), 400
            try:
                bx1, by1, bx2, by2 = (int(value) for value in bubble_box)
                parsed_polygon = []
                for point in polygon:
                    if (not isinstance(point, (list, tuple)) or len(point) != 2
                            or any(type(value) is not int for value in point)):
                        raise ValueError("invalid point")
                    x, y = point
                    if not (bx1 <= x < bx2 and by1 <= y < by2):
                        raise ValueError("point outside bubble")
                    parsed_polygon.append([x, y])
            except (TypeError, ValueError, OverflowError):
                return jsonify(error="يجب أن تقع نقاط القناع داخل حدود الفقاعة المكتشفة."), 400
            area_twice = abs(sum(
                point[0] * parsed_polygon[(index + 1) % len(parsed_polygon)][1]
                - parsed_polygon[(index + 1) % len(parsed_polygon)][0] * point[1]
                for index, point in enumerate(parsed_polygon)
            ))
            bubble_area = max(1, (bx2 - bx1) * (by2 - by1))
            if area_twice < max(50, bubble_area * 0.02):
                return jsonify(error="المضلع صغير جداً؛ أحط بمنطقة القناع الآمنة كاملة."), 400
        validated_mask_edits.append((target, parsed_polygon))
        used_mask_regions.add(region_id)
        used_mask_bubbles.add(bubble_key)
    requested_order = payload.get("region_order")
    reading_order_changed = False
    if requested_order is not None:
        current_ids = [item.get("region_id") for item in result["translated_lines"]]
        if (not isinstance(requested_order, list) or len(requested_order) != len(current_ids)
                or any(not isinstance(value, str) for value in requested_order)
                or len(set(requested_order)) != len(requested_order)
                or set(requested_order) != set(current_ids)):
            return jsonify(error="ترتيب القراءة يجب أن يحتوي معرف كل منطقة مرة واحدة."), 400
        reading_order_changed = requested_order != current_ids
    if (merge_groups or split_requests) and reading_order_changed:
        return jsonify(error="احفظ ترتيب القراءة أولاً، ثم نفّذ الدمج."), 400
    if sum(bool(items) for items in (merge_groups, split_requests, bubble_mask_edits)) > 1:
        return jsonify(error="نفّذ تعديل القناع أو الدمج أو التقسيم في عملية منفصلة."), 400
    previous_page_context = recent_chapter_context(chapter_id, page_number, chapter=chapter)
    try:
        for edit in edits:
            if not isinstance(edit, dict) or not isinstance(edit.get("index"), int):
                raise ValueError("رقم منطقة المراجعة غير صالح.")
            target = by_index.get(edit["index"])
            if target is None:
                raise ValueError("منطقة المراجعة غير موجودة.")
            source_text = edit.get("source", target["text"])
            translated_text = edit.get("translated", target["translated"])
            speaker_hint = edit.get("speaker_hint", target.get("speaker_hint", ""))
            if not isinstance(source_text, str) or not isinstance(translated_text, str) or not isinstance(speaker_hint, str):
                raise ValueError("نصوص المراجعة يجب أن تكون نصوصاً.")
            if len(source_text) > 500 or len(translated_text) > 800 or len(speaker_hint) > 160:
                raise ValueError("نص المنطقة أطول من الحد المسموح.")
            include_value = bool(edit.get("include", True))
            if include_value and translated_text.strip() and not has_arabic_letters(translated_text):
                raise ValueError("اكتب الترجمة بالعربية أو استبعد المنطقة من صورة المعاينة.")
            previous_source = target["text"]
            previous_translation = target["translated"]
            source_was_corrected = source_text.strip() != previous_source.strip()
            speaker_hint = speaker_hint.strip()
            speaker_hint_changed = speaker_hint != str(target.get("speaker_hint", "")).strip()
            translation_regenerated = False
            translation_was_manually_corrected = translated_text.strip() != previous_translation.strip()
            include_was_changed = bool(target.get("include", True)) != include_value
            layout_changed = False
            if "layout_bbox" in edit and target.get("bubble_id") is not None:
                layout = edit["layout_bbox"]
                if layout is None:
                    parsed_layout = None
                else:
                    if (not isinstance(layout, list) or len(layout) != 4
                            or any(type(value) is not int for value in layout)):
                        raise ValueError("حدود كتابة الفقاعة يجب أن تكون أربعة أعداد صحيحة.")
                    x, y, box_width, box_height = layout
                    bubble_box = target.get("bubble_bbox")
                    if (not isinstance(bubble_box, (list, tuple)) or len(bubble_box) != 4
                            or box_width < 12 or box_height < 12
                            or x < bubble_box[0] or y < bubble_box[1]
                            or x + box_width > bubble_box[2]
                            or y + box_height > bubble_box[3]):
                        raise ValueError("اجعل حدود الكتابة داخل الفقاعة وبحجم لا يقل عن 12 بكسل.")
                    parsed_layout = [x, y, x + box_width, y + box_height]
                layout_changed = parsed_layout != target.get("manual_layout_bbox")
                if parsed_layout is None:
                    target.pop("manual_layout_bbox", None)
                else:
                    target["manual_layout_bbox"] = parsed_layout
            ordered_regions = result["translated_lines"]
            current_position = next(
                (position for position, item in enumerate(ordered_regions)
                 if item.get("index") == edit["index"]),
                0,
            )
            local_context = "\n".join(
                _dialogue_context_line(item)
                for item in ordered_regions[max(0, current_position - 2):current_position]
                if _dialogue_context_line(item)
            )
            dialogue_context = "\n".join(
                part for part in (previous_page_context, local_context) if part
            )
            if ((source_text != target["text"] or speaker_hint_changed)
                    and translated_text == target["translated"]):
                if not include_value:
                    # Do not spend inference on excluded text; clear any old
                    # output so re-including the corrected region requires a
                    # fresh translation instead of reusing stale Arabic.
                    translated_text = ""
                else:
                    if speaker_hint:
                        dialogue_context = (
                            f"User-provided speaker note for current dialogue: {speaker_hint}\n"
                            + dialogue_context
                        )
                    translation_context = _build_translation_context(
                        target.get("region_type"), dialogue_context,
                        None if source_was_corrected else target.get("confidence"),
                    )
                    translated_text = translate_text(
                        source_text, chapter["title"] if chapter else "", page_number,
                        context=translation_context,
                        chapter_id=chapter_id, region_index=edit["index"],
                        bubble_id=target.get("bubble_id"),
                        work_scope=(chapter.get("series_key") or chapter.get("series_title") or chapter["title"])
                        if chapter else None,
                        glossary_scope=chapter.get("series_title") if chapter else None,
                    )
                    if not has_arabic_letters(translated_text):
                        raise RuntimeError("لم ينتج محرك الترجمة نصاً عربياً لهذه المنطقة.")
                    translation_regenerated = True
            if source_was_corrected:
                _record_translation_event(
                    "ocr_correction", previous_source, source_text.strip(), "human_ocr",
                    chapter_id=chapter_id, chapter_title=chapter["title"] if chapter else "",
                    page_index=page_number, region_index=edit["index"],
                    bubble_id=target.get("bubble_id"), review_status="human_reviewed",
                    metadata={"translation_at_time": translated_text.strip()},
                )
            if translation_was_manually_corrected:
                _record_translation_event(
                    "human_correction", source_text.strip(), translated_text.strip(), "human",
                    chapter_id=chapter_id, chapter_title=chapter["title"] if chapter else "",
                    page_index=page_number, region_index=edit["index"],
                    bubble_id=target.get("bubble_id"), review_status="human_reviewed",
                    metadata={"previous_source": previous_source,
                              "previous_translation": previous_translation},
                )
            target["text"] = source_text.strip()
            target["translated"] = translated_text.strip()
            target["speaker_hint"] = speaker_hint
            if source_was_corrected and not translation_regenerated:
                target.pop("review_status", None)
            elif translation_regenerated:
                target.pop("review_status", None)
            elif translation_was_manually_corrected:
                target["review_status"] = "human_reviewed"
            target["include"] = include_value
            if (source_was_corrected or speaker_hint_changed or translation_was_manually_corrected
                    or include_was_changed or layout_changed):
                result["render_stale"] = True
                result.pop("render_quality", None)
        for target, polygon in validated_mask_edits:
            matching_regions = [item for item in result["translated_lines"]
                                if item.get("bubble_id") == target.get("bubble_id")
                                and item.get("bubble_bbox") == target.get("bubble_bbox")]
            for item in matching_regions:
                if polygon is None:
                    item.pop("manual_bubble_polygon", None)
                else:
                    item["manual_bubble_polygon"] = [point.copy() for point in polygon]
            result["render_stale"] = True
            result.pop("render_quality", None)
        if split_items:
            insertions = {}
            for target, first_text, second_text, first_geo, second_geo in split_items:
                for item, text, geometry in (
                    (target, first_text, first_geo), (None, second_text, second_geo),
                ):
                    boxes, polygons, left, top, right, bottom = geometry
                    child = {**target, "text": text, "translated": "",
                             "left": int(left), "top": int(top),
                             "width": max(1, int(math.ceil(right)) - int(left)),
                             "height": max(1, int(math.ceil(bottom)) - int(top)),
                             "ocr_boxes": boxes, "ocr_polygons": polygons}
                    child.pop("manual_layout_bbox", None)
                    child.pop("review_status", None)
                    if item is None:
                        child["region_id"] = "reg_" + uuid.uuid4().hex
                        child["split_parent_id"] = target["region_id"]
                    else:
                        child["split_parent_id"] = target["region_id"]
                    child.pop("translation_elapsed_seconds", None)
                    child.pop("translation_attempt_elapsed_seconds", None)
                    insertions.setdefault(target["region_id"], []).append(child)
            expanded = []
            for item in result["translated_lines"]:
                children = insertions.get(item.get("region_id"))
                expanded.extend(children if children else [item])
            result["translated_lines"] = expanded
            for position, item in enumerate(result["translated_lines"], 1):
                item["index"] = position
            result["processing_status"] = "translation_in_progress"
            result["progress"] = {"stage": "translation", "completed_regions": 0,
                                  "total_regions": len(result["translated_lines"])}
            result["translation_inputs_stale"] = True
            result["translation_context_signature"] = "manual_region_split"
            result["render_stale"] = True
            result.pop("render_quality", None)
        result["original"] = "\n".join(item["text"] for item in result["translated_lines"])
        result["translated"] = "\n".join(
            item["translated"] for item in result["translated_lines"] if item["translated"]
        )
        if reading_order_changed:
            rank = {region_id: position for position, region_id in enumerate(requested_order)}
            result["translated_lines"].sort(key=lambda item: rank[item["region_id"]])
            for position, item in enumerate(result["translated_lines"], 1):
                item["index"] = position
            result["original"] = "\n".join(item["text"] for item in result["translated_lines"])
            result["translated"] = "\n".join(
                item["translated"] for item in result["translated_lines"] if item["translated"]
            )
            result["render_stale"] = True
            result["reading_order_reviewed"] = True
        if merge_items:
            merged_by_position = {}
            remove_ids = set()
            for group, first, second in merge_items:
                boxes = [first.get("left", 0), first.get("top", 0),
                         first.get("left", 0) + first.get("width", 0),
                         first.get("top", 0) + first.get("height", 0),
                         second.get("left", 0), second.get("top", 0),
                         second.get("left", 0) + second.get("width", 0),
                         second.get("top", 0) + second.get("height", 0)]
                left, top = min(boxes[0], boxes[4]), min(boxes[1], boxes[5])
                right, bottom = max(boxes[2], boxes[6]), max(boxes[3], boxes[7])
                merged = {
                    **first,
                    "region_id": "reg_" + uuid.uuid4().hex,
                    "text": " ".join((first.get("text", "").strip(), second.get("text", "").strip())).strip(),
                    "translated": "",
                    "left": left, "top": top, "width": right - left, "height": bottom - top,
                    "ocr_boxes": [*(first.get("ocr_boxes") or []), *(second.get("ocr_boxes") or [])],
                    "ocr_polygons": [*(first.get("ocr_polygons") or []), *(second.get("ocr_polygons") or [])],
                    "include": first.get("include", True) and second.get("include", True),
                    "merge_parent_ids": list(group),
                }
                merged.pop("manual_layout_bbox", None)
                merged.pop("review_status", None)
                remove_ids.update(group)
                merged_by_position[min(positions[group[0]], positions[group[1]])] = merged
            result["translated_lines"] = [
                merged_by_position.get(position, item)
                for position, item in enumerate(result["translated_lines"])
                if item.get("region_id") not in remove_ids or position in merged_by_position
            ]
            for position, item in enumerate(result["translated_lines"], 1):
                item["index"] = position
            result["original"] = "\n".join(item["text"] for item in result["translated_lines"])
            result["translated"] = "\n".join(
                item["translated"] for item in result["translated_lines"] if item["translated"]
            )
            result["processing_status"] = "translation_in_progress"
            result["progress"] = {"stage": "translation", "completed_regions": 0,
                                  "total_regions": len(result["translated_lines"])}
            result["translation_inputs_stale"] = True
            result["translation_context_signature"] = "manual_region_merge"
            result["render_stale"] = True
            result.pop("render_quality", None)
        if chapter:
            result["translation_work_scope"] = (
                chapter.get("series_key") or chapter.get("series_title") or chapter["title"]
            )
            if result.get("processing_status") == "complete":
                # A human-confirmed translation becomes context for later
                # bubbles. Store its new signature now so rendering this same
                # reviewed page is not incorrectly blocked as stale.
                result["translation_context_version"] = PAGE_TRANSLATION_CONTEXT_VERSION
                result["translation_context_signature"] = page_translation_context_signature(
                    previous_page_context, result["translated_lines"]
                )
                result["translation_glossary_signature"] = chapter_glossary_signature(chapter)
                result["translation_engine_signature"] = active_translation_recipe_signature()
                result["translation_inputs_stale"] = reading_order_changed
                if reading_order_changed:
                    result["translation_context_signature"] = "reading_order_changed"
        library_store.save_page_result(chapter_id, page_number, result)
        return jsonify(**result)
    except (ValueError, RuntimeError) as exc:
        return jsonify(error=str(exc)), 400


@app.post("/api/library/<chapter_id>/pages/<int:page_number>/regions/<int:region_index>/translate")
@_serialize_page_route
def library_retranslate_region(chapter_id, page_number, region_index):
    """Re-translate one corrected OCR region with nearby chapter dialogue."""
    result = library_store.read_page_result(chapter_id, page_number)
    chapter = library_store.get_chapter(chapter_id)
    if result is None or chapter is None:
        return jsonify(error="شغّل OCR والترجمة قبل إعادة ترجمة المنطقة."), 409
    line = next((item for item in result.get("translated_lines", [])
                 if item.get("index") == region_index), None)
    if line is None:
        return jsonify(error="منطقة الترجمة غير موجودة."), 404
    payload = request.get_json(silent=True) or {}
    source_text = payload.get("source", line.get("text", ""))
    if not isinstance(source_text, str) or not source_text.strip() or len(source_text) > 500:
        return jsonify(error="أدخل نصاً إنجليزياً مصححاً لا يتجاوز 500 حرف."), 400
    source_text = source_text.strip()
    if result.get("processing_status") == "complete":
        refreshed = refresh_chapter_translation_staleness(chapter_id, chapter)
        result = refreshed.get(int(page_number), result)
        if result.get("translation_inputs_stale"):
            library_store.save_page_result(chapter_id, page_number, result)
            return jsonify(
                error="سياق الصفحة أو محركها تغيّر؛ حدّث ترجمة الصفحة كاملة قبل إعادة ترجمة منطقة منفردة."
            ), 409
        line = next((item for item in result.get("translated_lines", [])
                     if item.get("index") == region_index), None)
        if line is None:
            return jsonify(error="منطقة الترجمة غير موجودة."), 404
    lines = result["translated_lines"]
    position = lines.index(line)
    previous_page_context = recent_chapter_context(chapter_id, page_number, chapter=chapter)
    local_context = "\n".join(
        _dialogue_context_line(item)
        for item in lines[max(0, position - 2):position]
        if _dialogue_context_line(item)
    )
    dialogue_context = "\n".join(
        part for part in (previous_page_context, local_context) if part
    )
    speaker_hint = payload.get("speaker_hint", line.get("speaker_hint", ""))
    if not isinstance(speaker_hint, str) or len(speaker_hint) > 160:
        return jsonify(error="ملاحظة المتحدث يجب ألا تتجاوز 160 حرفاً."), 400
    speaker_hint = speaker_hint.strip()
    if speaker_hint:
        dialogue_context = (
            f"User-provided speaker note for current dialogue: {speaker_hint}\n"
            + dialogue_context
        )
    translation_context = _build_translation_context(
        line.get("region_type"), dialogue_context,
        line.get("confidence") if source_text == str(line.get("text", "")).strip() else None,
    )
    previous_source = line.get("text", "")
    started = time.perf_counter()
    try:
        translated = translate_text(
            source_text, chapter["title"], page_number,
            context=translation_context,
            chapter_id=chapter_id, region_index=region_index,
            bubble_id=line.get("bubble_id"),
            work_scope=chapter.get("series_key") or chapter.get("series_title") or chapter["title"],
            glossary_scope=chapter.get("series_title"),
        )
        if not has_arabic_letters(translated):
            raise RuntimeError("لم ينتج محرك الترجمة نصاً عربياً لهذه المنطقة.")
    except (OSError, ValueError, RuntimeError) as exc:
        attempt_elapsed = round(time.perf_counter() - started, 4)
        line["translation_attempt_elapsed_seconds"] = attempt_elapsed
        result["translation_elapsed_seconds"] = round(
            float(result.get("translation_elapsed_seconds", 0.0)) + attempt_elapsed, 4
        )
        result["last_translation_error"] = {
            "region_index": region_index,
            "message": str(exc).strip()[:300] or type(exc).__name__,
        }
        library_store.save_page_result(chapter_id, page_number, result)
        return jsonify(
            error=f"تعذرت إعادة ترجمة المنطقة: {exc}",
            attempt_elapsed_seconds=attempt_elapsed,
        ), 502
    line["translation_elapsed_seconds"] = round(time.perf_counter() - started, 4)
    line.pop("translation_attempt_elapsed_seconds", None)
    if previous_source.strip() != source_text:
        _record_translation_event(
            "ocr_correction", previous_source, source_text, "human_ocr",
            chapter_id=chapter_id, chapter_title=chapter["title"],
            page_index=page_number, region_index=region_index,
            bubble_id=line.get("bubble_id"), review_status="human_reviewed",
            metadata={"translation_at_time": translated},
        )
    line["text"] = source_text
    line["translated"] = translated
    line["speaker_hint"] = speaker_hint
    line.pop("review_status", None)
    result.pop("last_translation_error", None)
    result["original"] = "\n".join(item.get("text", "") for item in lines)
    result["translated"] = "\n".join(
        item.get("translated", "") for item in lines if item.get("translated")
    )
    result["translation_elapsed_seconds"] = round(
        float(result.get("translation_elapsed_seconds", 0.0)) + time.perf_counter() - started, 4
    )
    result["render_stale"] = True
    result.pop("render_quality", None)
    result["translation_work_scope"] = (
        chapter.get("series_key") or chapter.get("series_title") or chapter["title"]
    )
    if result.get("processing_status") == "complete":
        result["translation_context_version"] = PAGE_TRANSLATION_CONTEXT_VERSION
        result["translation_context_signature"] = page_translation_context_signature(
            previous_page_context, lines
        )
        result["translation_glossary_signature"] = chapter_glossary_signature(chapter)
        result["translation_engine_signature"] = active_translation_recipe_signature()
        result["translation_inputs_stale"] = False
    library_store.save_page_result(chapter_id, page_number, result)
    return jsonify(**result)


@app.post("/api/library/<chapter_id>/pages/<int:page_number>/render")
@_serialize_page_route
def library_render_page(chapter_id, page_number):
    try:
        chapter = library_store.get_chapter(chapter_id)
        if chapter is None:
            return jsonify(error="الفصل غير موجود في المكتبة المحلية."), 404
        refreshed = refresh_chapter_translation_staleness(chapter_id, chapter)
        stored = library_store.read_page(chapter_id, page_number)
        result = refreshed.get(int(page_number))
        if stored is None or result is None:
            return jsonify(error="شغّل OCR والترجمة على الصفحة قبل إنشاء المعاينة."), 409
        if result.get("translation_inputs_stale"):
            return jsonify(error="تغيّر سياق الصفحة أو القاموس؛ حدّث الترجمة قبل إنشاء المعاينة."), 409
        if (result.get("processing_status") != "complete"
                or not _page_has_complete_translations(result)):
            return jsonify(error="لم تكتمل ترجمة جميع المناطق المدرجة. أعد المحاولة أو استبعد المنطقة غير المترجمة قبل إنشاء المعاينة."), 409
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return jsonify(error="بيانات المعاينة غير صالحة."), 400
        selected = payload.get("regions")
        translated_lines = result.get("translated_lines")
        if not isinstance(translated_lines, list):
            translated_lines = []
        if selected is not None:
            if not isinstance(selected, list) or len(selected) > 300 or any(not isinstance(index, int) for index in selected):
                return jsonify(error="قائمة مناطق المعاينة غير صالحة."), 400
            included = set(selected)
            regions = [
                item for item in translated_lines
                if isinstance(item, dict)
                and item.get("index") in included
                and item.get("include", True) is not False
                and _region_has_usable_translation(item)
            ]
        else:
            regions = [
                item for item in translated_lines
                if isinstance(item, dict)
                and item.get("include", True) is not False
                and _region_has_usable_translation(item)
            ]
        if not regions:
            original_is_intentional = result.get("no_text_detected") or (
                translated_lines
                and all(isinstance(item, dict) and item.get("include", True) is False
                        for item in translated_lines)
            )
            if not original_is_intentional:
                return jsonify(error="اختر منطقة نص واحدة على الأقل أو استبعد جميع المناطق لحفظ الصفحة الأصلية."), 400
            _path, image_bytes = stored
            preserved = io.BytesIO()
            with Image.open(io.BytesIO(image_bytes)) as source_image:
                source_image.convert("RGB").save(preserved, format="PNG", optimize=True)
            rendered = preserved.getvalue()
            path = library_store.save_rendered_page(chapter_id, page_number, rendered)
            result["render_stale"] = False
            result["render_pipeline_version"] = RENDER_PIPELINE_VERSION
            result["render_quality"] = {
                "rendered_regions": 0,
                # User-excluded regions are intentional, not renderer failures.
                "skipped_regions": 0,
                "skipped_region_indices": [],
            }
            library_store.save_page_result(chapter_id, page_number, result)
            return jsonify(
                rendered_url=f"/api/library/{chapter_id}/pages/{page_number}/rendered",
                rendered_regions=0,
                skipped_regions=0,
                skipped_region_indices=[],
                quality_note="لا توجد مناطق نص مدرجة؛ حُفظت الصفحة الأصلية دون تغيير.",
                bubble_repair_strategies={},
                file_name=path.name,
                preserved_original=True,
            )
        _path, image_bytes = stored
        rendered, quality = render_translated_page(image_bytes, regions)
        path = library_store.save_rendered_page(chapter_id, page_number, rendered)
        result["render_stale"] = False
        result["render_pipeline_version"] = RENDER_PIPELINE_VERSION
        result["render_quality"] = quality
        library_store.save_page_result(chapter_id, page_number, result)
        return jsonify(
            rendered_url=f"/api/library/{chapter_id}/pages/{page_number}/rendered",
            rendered_regions=quality["rendered_regions"],
            skipped_regions=quality["skipped_regions"],
            rendered_region_indices=quality.get("rendered_region_indices", []),
            skipped_region_indices=quality.get("skipped_region_indices", []),
            quality_note="تُعرض فقاعات الحوار والنصوص الخارجية المكتشفة. تستخدم الفقاعات تعبئة أو إعادة بناء لونية أو ترميماً موضعياً؛ وقد تُترك المنطقة إذا لم تلائمها العربية بأمان.",
            bubble_repair_strategies=quality.get("bubble_repair_strategies", {}),
            file_name=path.name,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        app.logger.exception("Local image render failed")
        return jsonify(error=f"تعذر إنشاء معاينة الصفحة: {exc}"), 500


@app.get("/api/library/<chapter_id>/pages/<int:page_number>/layers.zip")
@_serialize_page_route
def library_export_page_layers(chapter_id, page_number):
    """Export one page with separate editable artwork, removal and text layers."""
    chapter = library_store.get_chapter(chapter_id)
    if chapter is None:
        return jsonify(error="Chapter not found."), 404
    stored = library_store.read_page(chapter_id, page_number)
    if stored is None:
        return jsonify(error="Page not found."), 404
    try:
        result = refresh_chapter_translation_staleness(chapter_id, chapter).get(int(page_number))
        if result is None:
            return jsonify(error="Translate this page before exporting its layers."), 409
        if result.get("translation_inputs_stale"):
            return jsonify(error="Page translations are stale; translate the page again first."), 409
        if (result.get("processing_status") != "complete"
                or not _page_has_complete_translations(result)):
            return jsonify(error="Complete page translation before exporting its layers."), 409

        _page_path, image_bytes = stored
        regions = [
            item for item in result.get("translated_lines", [])
            if isinstance(item, dict) and item.get("include", True) is not False
            and _region_has_usable_translation(item)
        ]
        if regions:
            final_png, quality, layers = render_translated_page(
                image_bytes, regions, include_layers=True
            )
        else:
            with Image.open(io.BytesIO(image_bytes)) as source:
                original = source.convert("RGB")
                final_stream = io.BytesIO()
                original.save(final_stream, format="PNG", optimize=True)
                final_png = final_stream.getvalue()
                mask_stream = io.BytesIO()
                Image.new("L", original.size, 0).save(mask_stream, format="PNG", optimize=True)
                overlay_stream = io.BytesIO()
                Image.new("RGBA", original.size, (0, 0, 0, 0)).save(
                    overlay_stream, format="PNG", optimize=True
                )
                underlay_stream = io.BytesIO()
                original.save(underlay_stream, format="PNG", optimize=True)
            layers = {
                "original.png": final_png,
                "text_removal_mask.png": mask_stream.getvalue(),
                "repaired_underlay.png": underlay_stream.getvalue(),
                "translated_text_overlay.png": overlay_stream.getvalue(),
            }
            quality = {"rendered_regions": 0, "skipped_regions": 0, "preserved_original": True}

        manifest = {
            "chapter_id": chapter_id,
            "page_number": page_number,
            "layers": list(layers),
            "quality": quality,
            "regions": [
                {key: item.get(key) for key in (
                    "index", "region_id", "bubble_id", "region_type", "text", "translated",
                    "left", "top", "width", "height", "bubble_bbox", "manual_bubble_polygon",
                    "manual_layout_bbox",
                ) if key in item}
                for item in result.get("translated_lines", []) if isinstance(item, dict)
            ],
        }
        bundle = io.BytesIO()
        with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("final.png", final_png)
            for name, content in layers.items():
                archive.writestr(name, content)
            archive.writestr("regions.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        bundle.seek(0)
        return send_file(
            bundle, mimetype="application/zip", as_attachment=True,
            download_name=f"page-{page_number:04d}-layers.zip",
        )
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
        app.logger.exception("Page layer export failed")
        return jsonify(error=str(exc)), 500


@app.get("/api/library/<chapter_id>/export")
def library_export(chapter_id):
    chapter = library_store.get_chapter(chapter_id)
    if chapter is None:
        return jsonify(error="الفصل غير موجود في المكتبة المحلية."), 404
    results = refresh_chapter_translation_staleness(chapter_id, chapter)
    export_format = request.args.get("format", "bundle").lower()
    if export_format == "cbz":
        edition = request.args.get("edition", "original").lower()
        if edition not in {"original", "rendered"}:
            return jsonify(error="صيغة edition يجب أن تكون original أو rendered."), 400
        if edition == "rendered" and any(
            not (result := results.get(int(page["page_number"])))
            or result.get("processing_status") != "complete"
            or result.get("translation_inputs_stale")
            or not _page_has_complete_translations(result)
            or library_store.read_rendered_page(chapter_id, page["page_number"]) is None
            for page in chapter["pages"]
        ):
            return jsonify(error="أنشئ معاينة مترجمة لكل صفحات الفصل قبل تصدير CBZ المترجم."), 409
        comic_info = ET.Element("ComicInfo")
        ET.SubElement(comic_info, "Title").text = chapter["title"]
        ET.SubElement(comic_info, "Series").text = chapter.get("series_title") or chapter["title"]
        if chapter.get("source_chapter_url"):
            ET.SubElement(comic_info, "Web").text = chapter["source_chapter_url"]
        attribution_names = [
            item.get("name", "") for item in chapter.get("source_attributions", [])
            if isinstance(item, dict) and item.get("name")
        ]
        if attribution_names:
            ET.SubElement(comic_info, "Notes").text = "Source credits: " + ", ".join(attribution_names)
        ET.SubElement(comic_info, "Manga").text = "YesAndRightToLeft" if chapter["reading_direction"] == "rtl" else "Yes"
        comic_pages = ET.SubElement(comic_info, "Pages")
        bundle = io.BytesIO()
        with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
            page_index = 0
            for page in chapter["pages"]:
                if edition == "rendered":
                    image_path = library_store.read_rendered_page(chapter_id, page["page_number"])
                    image_bytes = image_path.read_bytes()
                    extension = "png"
                else:
                    stored = library_store.read_page(chapter_id, page["page_number"])
                    if not stored:
                        continue
                    image_path, image_bytes = stored
                    extension = image_path.suffix.lstrip(".")
                archive.writestr(f"{page_index + 1:04d}.{extension}", image_bytes)
                ET.SubElement(comic_pages, "Page", Image=str(page_index), Type="Story")
                page_index += 1
            ET.SubElement(comic_info, "PageCount").text = str(page_index)
            archive.writestr(
                "ComicInfo.xml",
                ET.tostring(comic_info, encoding="utf-8", xml_declaration=True),
            )
        bundle.seek(0)
        return send_file(bundle, mimetype="application/vnd.comicbook+zip", as_attachment=True,
                         download_name=f"{chapter_id}.cbz", max_age=0)
    if export_format != "bundle":
        return jsonify(error="الصيغ المدعومة هي bundle وcbz."), 400
    bundle = io.BytesIO()
    manifest = {
        "title": chapter["title"],
        "series_title": chapter.get("series_title") or chapter["title"],
        "series_type": chapter["series_type"],
        "reading_direction": chapter["reading_direction"],
        "source_provider": chapter.get("source_provider") or None,
        "source_chapter_url": chapter.get("source_chapter_url") or None,
        "source_attributions": chapter.get("source_attributions", []),
        "pages": [],
        "note": "Original images and translation data are included. Any rendered previews use approximate OCR regions and require visual review; they are not verified finished artwork.",
    }
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
        for page in chapter["pages"]:
            stored = library_store.read_page(chapter_id, page["page_number"])
            if not stored:
                continue
            path, image_bytes = stored
            archive.writestr(f"images/{path.name}", image_bytes)
            result = library_store.read_page_result(chapter_id, page["page_number"])
            manifest["pages"].append({"page_number": page["page_number"], "translation": result})
            rendered = library_store.read_rendered_page(chapter_id, page["page_number"])
            if rendered:
                archive.write(rendered, f"rendered/{rendered.name}")
        archive.writestr("translations.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    bundle.seek(0)
    return send_file(bundle, mimetype="application/zip", as_attachment=True,
                     download_name=f"{chapter_id}.zip", max_age=0)


@app.post("/api/chapter")
def get_chapter():
    data = request.get_json(silent=True) or {}
    if str(data.get("reading_layout", "")).strip().lower() != "vertical":
        return jsonify(error=UNSUPPORTED_LAYOUT_MESSAGE), 422
    url = (data.get("url") or "").strip()
    provider = providers.for_url(url)
    if provider is None:
        return jsonify(error="لا يوجد مصدر مسجل لهذا الرابط؛ المصادر الحالية تعمل عبر HTTPS."), 400
    try:
        chapter = provider.load_chapter(
            url,
            lambda target, hosts, byte_limit, **options: safe_get(
                target, timeout=25, max_bytes=byte_limit, allowed_hosts=hosts, **options
            ),
        )
        blocked = [group for group in chapter.get("scanlation_group_ids", []) if library_store.is_source_blocked(provider.provider_id, group)]
        if blocked:
            return jsonify(error="هذا الفصل نُشر بواسطة مجموعة مستبعدة من قائمة الإزالة المحلية."), 410
        return jsonify(chapter)
    except ValueError as exc:
        return jsonify(error=str(exc)), 422
    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else 0
        if status_code in {401, 403, 429}:
            return jsonify(error="المصدر منع الجلب وطلب تحققاً أو تسجيل دخول. لا يمكن إكمال هذا الفصل آلياً؛ جرّب رابط فصل متاحاً للعامة دون تحقق."), 403
        return jsonify(error=f"رفض المصدر طلب جلب الفصل (HTTP {status_code})."), 502
    except requests.RequestException as exc:
        return jsonify(error=f"تعذر جلب الفصل: {exc}"), 502


@app.post("/api/source-import")
def import_remote_chapter():
    data = request.get_json(silent=True) or {}
    if str(data.get("reading_layout", "")).strip().lower() != "vertical":
        return jsonify(error=UNSUPPORTED_LAYOUT_MESSAGE), 422
    url = str(data.get("url", "")).strip()
    series_type = str(data.get("series_type", "manhwa")).strip().lower()
    direction = str(data.get("reading_direction", "ltr")).strip().lower()
    provider = providers.for_url(url)
    if provider is None:
        return jsonify(error="رابط فصل غير مدعوم."), 400
    if series_type not in SUPPORTED_VERTICAL_COMIC_TYPES or direction != "ltr":
        return jsonify(error="استيراد الفصول مخصص حالياً للأعمال ذات النص الإنجليزي والقراءة الطولية من اليسار إلى اليمين."), 400
    try:
        chapter = provider.load_chapter(
            url,
            lambda target, hosts, byte_limit, **options: safe_get(
                target, timeout=25, max_bytes=byte_limit, allowed_hosts=hosts, **options
            ),
        )
        chapter_type = str(chapter.get("series_type") or series_type).strip().lower()
        if chapter_type not in SUPPORTED_VERTICAL_COMIC_TYPES:
            return jsonify(error="نوع العمل غير معروف؛ اختر نوعه ثم استورده بوضع التمرير العمودي."), 422
        if any(library_store.is_source_blocked(provider.provider_id, group) for group in chapter.get("scanlation_group_ids", [])):
            return jsonify(error="هذا الفصل تابع لمجموعة مستبعدة محلياً."), 410
        source_chapter_url = chapter.get("source_url") or url
        existing = library_store.get_chapter_by_source(provider.provider_id, source_chapter_url)
        if existing is not None:
            if existing.get("archived_at"):
                existing = library_store.set_chapter_archived(existing["id"], False) or existing
            return jsonify(chapter=existing, already_imported=True), 200
        images = chapter.get("pages", [])
        if not images or len(images) > 300:
            return jsonify(error="عدد صفحات الفصل خارج الحدود (1–300)."), 422
        pages = []
        total_bytes = 0
        for page in images:
            response = safe_get(page["image"], headers={"Referer": chapter.get("source_url", url)}, timeout=30,
                                max_bytes=12 * 1024 * 1024)
            content_type = response.headers.get("Content-Type", "").split(";")[0].lower()
            if not content_type.startswith("image/"):
                raise ValueError("أعاد المصدر ملفاً ليس صورة.")
            extension, content = _validated_image_bytes(response.content)
            total_bytes += len(content)
            if total_bytes > 150 * 1024 * 1024:
                raise ValueError("الحجم الإجمالي لصفحات الفصل يتجاوز 150 MB.")
            pages.append((extension, content))
        imported = library_store.create_chapter(
            chapter["title"], chapter_type, direction, pages,
            series_title=chapter.get("series_title") or chapter["title"],
            series_key=chapter.get("series_key") or f"{provider.provider_id}:{chapter.get('series_title') or chapter['title']}",
            source_provider=provider.provider_id,
            source_chapter_url=source_chapter_url,
            source_attributions=chapter.get("attributions", []),
        )
        imported["attributions"] = chapter.get("attributions", [])
        return jsonify(chapter=imported), 201
    except sqlite3.IntegrityError:
        # A simultaneous request may win the unique remote-source identity.
        existing = None
        if provider is not None and "source_chapter_url" in locals():
            existing = library_store.get_chapter_by_source(provider.provider_id, source_chapter_url)
        if existing is not None:
            if existing.get("archived_at"):
                existing = library_store.set_chapter_archived(existing["id"], False) or existing
            return jsonify(chapter=existing, already_imported=True), 200
        raise
    except ValueError as exc:
        return jsonify(error=str(exc)), 422
    except requests.RequestException as exc:
        return jsonify(error=f"تعذر تنزيل الفصل: {exc}"), 502


@app.get("/api/sources")
def sources_list():
    return jsonify(sources=providers.descriptors())


@app.get("/api/sources/<provider_id>/search")
def source_search(provider_id):
    provider = providers.by_id(provider_id)
    if provider is None:
        return jsonify(error="مصدر البحث غير مسجل."), 404
    query = (request.args.get("q") or "").strip()
    if len(query) < 2:
        return jsonify(error="أدخل حرفين على الأقل للبحث."), 400
    try:
        result = providers.search(
            provider_id, query,
            lambda target, hosts, byte_limit, **options: safe_get(target, timeout=25, max_bytes=byte_limit, allowed_hosts=hosts, **options),
        )
        return jsonify(results=result, source=provider_id)
    except requests.ConnectionError:
        if provider_id == "mangadex":
            return jsonify(error="تعذر اتصال الخادم بـMangaDex عبر HTTPS. تحقّق من اتصال الإنترنت ومن سماح جدار حماية Windows لملف Python بالاتصالات الصادرة على المنفذ 443. إذا كانت الشبكة تتطلب Proxy، اضبط HTTPS_PROXY قبل تشغيل التطبيق."), 503
        return jsonify(error=f"تعذر اتصال الخادم بـ{provider.display_name} عبر HTTPS. تحقق من الإنترنت أو إعداد HTTPS_PROXY."), 503
    except ValueError as exc:
        return jsonify(error=str(exc)), 422
    except (requests.RequestException, ValueError, RuntimeError) as exc:
        app.logger.warning("%s search failed: %s", provider_id, exc)
        return jsonify(error=f"تعذر إكمال البحث في {provider.display_name}. تحقق من الاتصال وحاول مجدداً."), 502


@app.get("/api/sources/<provider_id>/works/<path:work_id>/chapters")
def source_chapters(provider_id, work_id):
    provider = providers.by_id(provider_id)
    if provider is None:
        return jsonify(error="مصدر الفصول غير مسجل."), 404
    try:
        chapters = providers.chapters(
            provider_id, work_id,
            lambda target, hosts, byte_limit, **options: safe_get(target, timeout=25, max_bytes=byte_limit, allowed_hosts=hosts, **options),
        )
        blocked_ids = {item["source_id"] for item in library_store.list_source_blocks()}
        chapters = [item for item in chapters if not blocked_ids.intersection(group.get("id") for group in item.get("groups", []))]
        return jsonify(chapters=chapters, source=provider_id)
    except ValueError as exc:
        return jsonify(error=str(exc)), 422
    except requests.ConnectionError:
        return jsonify(error=f"تعذر اتصال الخادم بـ{provider.display_name} عبر HTTPS. تحقق من الإنترنت أو إعداد HTTPS_PROXY."), 503
    except (requests.RequestException, RuntimeError) as exc:
        app.logger.warning("%s chapter listing failed: %s", provider_id, exc)
        return jsonify(error=f"تعذر جلب قائمة فصول {provider.display_name}. تحقق من الاتصال وحاول مجدداً."), 502


@app.get("/api/sources/mangadex/manga/<path:work_id>/chapters")
def legacy_mangadex_chapters(work_id):
    """Keep the original MangaDex endpoint available for older open tabs."""
    return source_chapters("mangadex", work_id)


@app.get("/api/source-blocks")
def source_blocks_list():
    return jsonify(blocks=library_store.list_source_blocks())


@app.post("/api/source-blocks")
def source_blocks_add():
    data = request.get_json(silent=True) or {}
    source_id = str(data.get("source_id", ""))
    source_name = str(data.get("source_name", "Unknown group"))
    reason = str(data.get("reason", ""))
    if not re.fullmatch(r"[0-9a-f-]{36}", source_id, re.IGNORECASE):
        return jsonify(error="معرّف مجموعة MangaDex غير صالح."), 400
    library_store.save_source_block("mangadex", source_id, source_name, reason)
    return jsonify(blocks=library_store.list_source_blocks()), 201


@app.delete("/api/source-blocks/<source_id>")
def source_blocks_remove(source_id):
    if not re.fullmatch(r"[0-9a-f-]{36}", source_id, re.IGNORECASE):
        return jsonify(error="معرّف مجموعة MangaDex غير صالح."), 400
    removed = library_store.delete_source_block("mangadex", source_id)
    return jsonify(removed=removed, blocks=library_store.list_source_blocks())


def run_ocr_space(image_bytes, content_type, api_key):
    extension = "png" if "png" in content_type else "webp" if "webp" in content_type else "jpg"
    last_error = None
    for attempt in range(3):
        try:
            response = HTTP.post("https://api.ocr.space/parse/image",
                data={"apikey": api_key, "language": "eng", "isOverlayRequired": "true"},
                files={"file": (f"manga-page.{extension}", image_bytes, content_type)}, timeout=(20, 120))
            response.raise_for_status()
            payload = response.json()
            if payload.get("IsErroredOnProcessing"):
                raise RuntimeError(str(payload.get("ErrorMessage") or "OCR.space processing failed"))
            results = payload.get("ParsedResults") or []
            extracted = "\n".join(item.get("ParsedText", "") for item in results).strip()
            if not extracted: raise RuntimeError("OCR.space لم يتمكن من قراءة نص واضح.")
            lines = []
            for result in results:
                for line in (result.get("TextOverlay") or {}).get("Lines") or []:
                    words = line.get("Words") or []
                    if not words: continue
                    left = min(float(w.get("Left", 0)) for w in words)
                    top = min(float(w.get("Top", 0)) for w in words)
                    right = max(float(w.get("Left", 0)) + float(w.get("Width", 0)) for w in words)
                    bottom = max(float(w.get("Top", 0)) + float(w.get("Height", 0)) for w in words)
                    text = " ".join(str(w.get("WordText", "")) for w in words).strip()
                    if text and right > left and bottom > top:
                        lines.append({"text": text, "left": round(left,2), "top": round(top,2), "width": round(right-left,2), "height": round(bottom-top,2)})
            return extracted, lines
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            last_error = exc
            if attempt < 2: time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"OCR.space فشل بعد 3 محاولات: {last_error}")


def run_local_ocr(image_bytes, reading_direction="ltr", include_external=True):
    global LOCAL_OCR
    if LOCAL_OCR is None:
        with _LOCAL_OCR_INIT_LOCK:
            if LOCAL_OCR is None:
                LOCAL_OCR = LocalRapidOCR()
    return LOCAL_OCR.extract(
        image_bytes, reading_direction=reading_direction,
        include_external=include_external,
    )


def run_local_detection(image_bytes, reading_direction="ltr", include_external=True):
    """Detect bubble/text crop geometry only; Gemini performs character OCR."""
    global LOCAL_OCR
    if LOCAL_OCR is None:
        with _LOCAL_OCR_INIT_LOCK:
            if LOCAL_OCR is None:
                LOCAL_OCR = LocalRapidOCR()
    return LOCAL_OCR.detect_regions_only(
        image_bytes, reading_direction=reading_direction,
        include_external=include_external,
    )


def _ocr_timing_breakdown(ocr_result):
    """Expose measured OCR stages and the residual page-processing time."""
    bubble_seconds = ocr_result.get("bubble_detection_and_ocr_seconds")
    external_seconds = ocr_result.get("external_ocr_seconds")
    detector_seconds = ocr_result.get("comic_detector_seconds")
    adjacent_seconds = ocr_result.get("adjacent_bubble_recovery_seconds", 0)
    edge_crop_seconds = ocr_result.get("edge_crop_recovery_seconds", 0)
    measured = [value for value in (bubble_seconds, external_seconds, detector_seconds,
                                    adjacent_seconds, edge_crop_seconds)
                if isinstance(value, (int, float)) and value >= 0]
    elapsed = ocr_result.get("elapsed_seconds")
    residual = None
    if isinstance(elapsed, (int, float)) and elapsed >= 0 and len(measured) == 5:
        residual = round(max(0.0, elapsed - sum(measured)), 4)
    timing = {
        "bubble_detection_and_ocr_seconds": bubble_seconds,
        "speech_bubble_mask_count": ocr_result.get("speech_bubble_mask_count"),
        "speech_bubble_masks_with_primary_ocr": ocr_result.get("speech_bubble_masks_with_primary_ocr"),
        "speech_bubble_masks_without_primary_ocr": ocr_result.get("speech_bubble_masks_without_primary_ocr"),
        "external_ocr_seconds": external_seconds,
        "adjacent_bubble_recovery_seconds": adjacent_seconds,
        "adjacent_bubble_recovery_count": ocr_result.get("adjacent_bubble_recovery_count", 0),
        "edge_crop_recovery_seconds": edge_crop_seconds,
        "edge_crop_recovery_count": ocr_result.get("edge_crop_recovery_count", 0),
        "comic_detector_seconds": detector_seconds,
        "other_ocr_processing_seconds": residual,
        "external_region_count": ocr_result.get("external_region_count", 0),
        "comic_detector_region_count": ocr_result.get("comic_detector_region_count", 0),
        "comic_detector_ocr_line_count": ocr_result.get("comic_detector_ocr_line_count", 0),
    }
    # Optional sub-stage keys are reported only when the pipeline measured them.
    for key in ("bubble_detection_seconds", "bubble_ocr_seconds",
                "bubble_split_detector_seconds", "bubble_split_count",
                "malformed_bubble_recognition_seconds",
                "suspicious_bubble_line_retry_seconds"):
        if key in ocr_result:
            timing[key] = ocr_result[key]
    return timing


def _record_translation_event(event_type, source_text, target_text, engine_id, *,
                              chapter_id=None, chapter_title="", page_index=None,
                              region_index=None, bubble_id=None, context="",
                              model_name=None, quantization=None,
                              review_status="unreviewed", metadata=None,
                              prompt_version=None):
    """Best-effort append-only provenance; a logging failure must not break reading."""
    try:
        return library_store.record_translation_event(
            event_type=event_type, source_text=source_text, target_text=target_text,
            engine_id=engine_id, chapter_id=chapter_id, chapter_title=chapter_title,
            page_index=page_index, region_index=region_index, bubble_id=bubble_id,
            context_text=context, model_name=model_name, quantization=quantization,
            prompt_version=prompt_version or ("balanced-dialogue-v1" if model_name else None),
            review_status=review_status, training_eligible=False, metadata=metadata,
        )
    except Exception:
        app.logger.exception("Could not append translation provenance")
        return None


def matching_glossary_entries(text, entries=None, work_scope=None):
    """Return only glossary phrases present as complete English terms in the source."""
    source = " ".join(str(text).split())
    if entries is None:
        entries = library_store.list_glossary(work_scope) if work_scope else library_store.list_glossary()
    matches = []
    for entry in entries:
        term = str(entry.get("source_term", "")).strip()
        target = str(entry.get("target_term", "")).strip()
        if not term or not target:
            continue
        if not has_arabic_letters(target):
            continue
        left = r"(?<![A-Za-z0-9])" if term[0].isalnum() else ""
        right = r"(?![A-Za-z0-9])" if term[-1].isalnum() else ""
        if re.search(left + re.escape(term) + right, source, flags=re.IGNORECASE):
            matches.append({"source_term": term[:60], "target_term": target[:80],
                            "scope_key": entry.get("scope_key", "*")})
    # A series-specific equivalent supersedes a global equivalent for the
    # same phrase, while the longest matching terms remain first.
    preferred = {}
    scope_preference = {
        scope: rank for rank, scope in enumerate(library_store.glossary_scope_aliases(work_scope))
    }
    for item in matches:
        key = library_store.normalize_term(item["source_term"])
        existing = preferred.get(key)
        rank = scope_preference.get(item.get("scope_key"), 100)
        existing_rank = scope_preference.get(existing.get("scope_key"), 100) if existing else 101
        if rank < existing_rank:
            preferred[key] = item
    return sorted(preferred.values(), key=lambda item: len(item["source_term"]), reverse=True)[:6]


def build_model_context(dialogue_context, glossary_entries):
    """Keep recent dialogue and applicable glossary instructions in a small prompt."""
    parts = []
    context_text = str(dialogue_context or "").strip()
    region_instruction = ""
    if context_text.startswith("Current region is "):
        region_instruction, _, context_text = context_text.partition("\n")
    context_text = context_text.strip()
    ocr_quality_instruction = ""
    if context_text.startswith("Current OCR confidence instruction:"):
        ocr_quality_instruction, _, context_text = context_text.partition("\n")
        context_text = context_text.strip()
    if region_instruction:
        parts.append(region_instruction)
    if ocr_quality_instruction:
        parts.append(ocr_quality_instruction)
    if context_text:
        compact_lines = [" ".join(line.split()) for line in context_text.splitlines()]
        bounded_context = "\n".join(line for line in compact_lines if line).strip()
        if len(bounded_context) > 900:
            bounded_context = bounded_context[-900:]
            # Avoid beginning with a cut-off caption or speaker-note label.
            first_line_end = bounded_context.find("\n")
            if first_line_end >= 0:
                bounded_context = bounded_context[first_line_end + 1:]
            else:
                first_word_end = bounded_context.find(" ")
                if first_word_end >= 0:
                    bounded_context = bounded_context[first_word_end + 1:]
        parts.append("PRIOR DIALOGUE (reference only; do not translate again):\n" + bounded_context)
    if glossary_entries:
        terms = "\n".join(
            f"- {entry['source_term']} = {entry['target_term']}"
            for entry in glossary_entries
        )
        parts.append("GLOSSARY (keep these equivalents):\n" + terms)
    return "\n\n".join(parts)


def _model_glossary_entries(text, term_scope):
    """Select glossary entries exactly as the ordinary Index path does."""
    entries = matching_glossary_entries(text, work_scope=term_scope)
    normalized_source = library_store.normalize_term(text)
    looks_like_complete_utterance = (
        len(normalized_source.split()) >= 3
        or bool(re.search(r"[.!?…][\"'’”)]*$", normalized_source))
    )
    if looks_like_complete_utterance:
        entries = [
            entry for entry in entries
            if library_store.normalize_term(entry["source_term"]) != normalized_source
        ]
    return entries


def _index_batch_size():
    """Return a conservative configurable group size; 1 disables batching."""
    try:
        configured = int(os.getenv("MANGA_INDEX_BATCH_SIZE", "4"))
    except (TypeError, ValueError):
        configured = 4
    return min(5, max(1, configured))


def _translate_index_prompt_batch(items):
    """Send small, same-page speech groups while preserving each item's context/glossary."""
    if CURRENT_TRANSLATION_ENGINE != "index_translate_9b":
        raise TranslationOutputError("Numbered batch mode is available only for Index.")
    prepared = []
    for item in items:
        text = str(item.get("text", "")).strip()
        term_scope = item.get("glossary_scope") or item.get("work_scope")
        entries = _model_glossary_entries(text, term_scope)
        prepared.append({
            "text": text,
            "context": build_model_context(item.get("context", ""), entries),
        })
    router = get_balanced_translator()
    return router.translate_many(CURRENT_TRANSLATION_ENGINE, prepared)


def reviewed_memory_scope(text, chapter_title, context, work_scope=None, glossary_scope=None):
    """Scope trusted human corrections by work, applicable glossary, and dialogue."""
    glossary_entries = matching_glossary_entries(text, work_scope=glossary_scope or work_scope)
    glossary_signature = hashlib.sha256(
        json.dumps(glossary_entries, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:12] if glossary_entries else "none"
    normalized_context = " ".join((context or "").split()).casefold()
    context_signature = hashlib.sha256(
        normalized_context.encode("utf-8")
    ).hexdigest()[:12] if normalized_context else "none"
    return (
        f"{work_scope or chapter_title or '_default'}|human_reviewed"
        f"|glossary:{glossary_signature}|context:{context_signature}"
    )


def _translation_memory_scope(text, engine_id, *, chapter_title="", context="",
                             work_scope=None, glossary_entries=None,
                             proofreader_active=False):
    """Build the same durable memory key used by the ordinary translation path."""
    entries = glossary_entries or []
    glossary_signature = hashlib.sha256(
        json.dumps(entries, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:12] if entries else "none"
    normalized_context = " ".join((context or "").split()).casefold()
    context_signature = hashlib.sha256(
        normalized_context.encode("utf-8")
    ).hexdigest()[:12] if normalized_context else "none"
    scope = (
        f"{work_scope or chapter_title or '_default'}|{engine_id}"
        f"|glossary:{glossary_signature}|context:{context_signature}"
    )
    recipe_version = translation_recipe_signature(engine_id)
    if recipe_version:
        scope += f"|recipe:{recipe_version}"
    if proofreader_active:
        scope += "|qwen4b-proofread"
    return scope


def _configure_persistent_chunk_cache(router):
    """Connect automatic model chunk caching to the local SQLite library."""
    for adapter in getattr(router, "adapters", ()):
        engine_id = adapter.engine_id
        adapter.chunk_cache_load = lambda key, engine=engine_id: library_store.get_translation_chunk(
            engine, key
        )
        adapter.chunk_cache_save = lambda key, value, engine=engine_id: library_store.save_translation_chunk(
            engine, key, value
        )


def translate_text(text, chapter_title="", page_index=None, line_mode=False, context="",
                   chapter_id=None, region_index=None, bubble_id=None, work_scope=None,
                   glossary_scope=None, _pretranslated=None):
    """Translate locally; model initialization is lazy to keep startup lightweight."""
    global TRANSLATOR, QWEN_TRANSLATOR, QWEN_PROOFREADER, MADLAD_TRANSLATOR, BALANCED_TRANSLATOR
    if not text.strip():
        return ""
    if CURRENT_TRANSLATION_ENGINE == "index_translate_9b":
        chapter_label = re.fullmatch(
            r"(?:CH(?:APTER)?\.?\s*)0*(\d{1,5})",
            " ".join(text.split()),
            flags=re.IGNORECASE,
        )
        if chapter_label:
            # Chapter-number headings have an unambiguous localized form. Avoid
            # spending a long model request on a short OCR label that often
            # echoes nearby dialogue instead of translating the label.
            translated_label = f"الفصل {int(chapter_label.group(1))}"
            _record_translation_event(
                "engine_output", text, translated_label, CURRENT_TRANSLATION_ENGINE,
                chapter_id=chapter_id, chapter_title=chapter_title,
                page_index=page_index, region_index=region_index,
                bubble_id=bubble_id, context=context,
                prompt_version=INDEX_PROMPT_VERSION,
                metadata={"translation_method": "chapter_label_normalization"},
            )
            return translated_label
    try:
        if line_mode:
            lines = text.splitlines()
            translations = []
            for line in lines:
                match = re.match(r"^\s*(\d+[.)、-]\s*)(.*)$", line)
                prefix, content = (match.group(1), match.group(2)) if match else ("", line)
                translations.append(prefix + translate_text(
                    content, chapter_title, page_index, context=context, chapter_id=chapter_id,
                    region_index=region_index, bubble_id=bubble_id, work_scope=work_scope,
                    glossary_scope=glossary_scope,
                ))
            return "\n".join(translations)
        term_scope = glossary_scope or work_scope
        # New-model routes should translate the current utterance with its
        # dialogue context instead of returning a saved or hand-authored
        # sentence. Glossary terms are passed as context to the model below.
        # The phrase table remains only for explicitly selected legacy engines.
        reviewed_phrase = (
            translate_reviewed_phrase(text)
            if CURRENT_TRANSLATION_ENGINE in {"opus", "madlad", "qwen3"}
            else None
        )
        if reviewed_phrase is not None and has_arabic_letters(reviewed_phrase):
            _record_translation_event("translation_result", text, reviewed_phrase, "reviewed_phrase_memory",
                                      chapter_id=chapter_id, chapter_title=chapter_title,
                                      page_index=page_index, region_index=region_index,
                                      bubble_id=bubble_id, context=context)
            return reviewed_phrase
        proofreader_active = ENABLE_LOCAL_PROOFREADER and CURRENT_TRANSLATION_ENGINE == "opus"
        if CURRENT_TRANSLATION_ENGINE in {
            "balanced_auto", "index_translate_9b", "translategemma_4b"
        }:
            glossary_entries = _model_glossary_entries(text, term_scope)
        else:
            glossary_entries = matching_glossary_entries(text, work_scope=term_scope)
        # A dialogue line can legitimately translate differently depending on
        # the preceding bubbles (gender, referents, sarcasm). Keep memories
        # from leaking across distinct conversational contexts.
        if CURRENT_TRANSLATION_ENGINE in {
            "balanced_auto", "index_translate_9b", "translategemma_4b"
        }:
            get_balanced_translator()
            _configure_persistent_chunk_cache(BALANCED_TRANSLATOR)
            selected_engine_id = (
                BALANCED_TRANSLATOR.preferred_engine_id()
                if CURRENT_TRANSLATION_ENGINE == "balanced_auto"
                else CURRENT_TRANSLATION_ENGINE
            )
        else:
            selected_engine_id = CURRENT_TRANSLATION_ENGINE
        memory_scope = _translation_memory_scope(
            text, selected_engine_id, chapter_title=chapter_title, context=context,
            work_scope=work_scope, glossary_entries=glossary_entries,
            proofreader_active=proofreader_active,
        )
        remembered = library_store.get_memory(memory_scope, text)
        if remembered is not None:
            remembered = clean_translation_output(remembered)
        if remembered is not None and has_arabic_letters(remembered):
            _record_translation_event("translation_result", text, remembered, "translation_memory",
                                      chapter_id=chapter_id, chapter_title=chapter_title,
                                      page_index=page_index, region_index=region_index,
                                      bubble_id=bubble_id, context=context,
                                      metadata={"scope": memory_scope})
            return remembered
        if remembered is not None:
            app.logger.warning("Ignoring a translation memory entry without Arabic letters")
        if CURRENT_TRANSLATION_ENGINE in {
            "balanced_auto", "index_translate_9b", "translategemma_4b"
        }:
            model_context = build_model_context(context, glossary_entries)
            if _pretranslated is not None:
                if CURRENT_TRANSLATION_ENGINE != "index_translate_9b":
                    raise ValueError("Pretranslated batch results are accepted only for Index.")
                translated = clean_translation_output(_pretranslated)
                if not _is_usable_arabic_translation(translated):
                    raise RuntimeError("The validated Index batch item is not usable Arabic.")
            elif CURRENT_TRANSLATION_ENGINE == "balanced_auto":
                translated = BALANCED_TRANSLATOR.translate(text, context=model_context)
            else:
                translated = BALANCED_TRANSLATOR.translate_with(
                    CURRENT_TRANSLATION_ENGINE, text, context=model_context
                )
            actual_engine = BALANCED_TRANSLATOR.last_engine_id
            actual_model = BALANCED_TRANSLATOR.last_model_name
            if translated:
                actual_scope = _translation_memory_scope(
                    text, actual_engine, chapter_title=chapter_title, context=context,
                    work_scope=work_scope, glossary_entries=glossary_entries,
                    proofreader_active=proofreader_active,
                )
                actual_recipe_version = translation_recipe_signature(actual_engine)
                library_store.save_memory(actual_scope, text, translated)
                _record_translation_event(
                    "engine_output", text, translated, actual_engine,
                    chapter_id=chapter_id, chapter_title=chapter_title,
                    page_index=page_index, region_index=region_index,
                    bubble_id=bubble_id, context=context, model_name=actual_model,
                    quantization="INT8" if actual_engine == "opus" else "Q4_K_M",
                    prompt_version=(
                        (INDEX_BATCH_PROMPT_VERSION if _pretranslated is not None else INDEX_PROMPT_VERSION)
                        if actual_engine == "index_translate_9b"
                        else TRANSLATEGEMMA_PROMPT_VERSION if actual_engine == "translategemma_4b"
                        else None
                    ),
                    metadata={"translation_policy": "index_only" if CURRENT_TRANSLATION_ENGINE == "index_translate_9b" else "balanced_auto",
                              "translation_mode": "numbered_batch" if _pretranslated is not None else "single_region",
                              "glossary_entries": glossary_entries,
                              "recipe_version": actual_recipe_version},
                )
            return translated
        if CURRENT_TRANSLATION_ENGINE == "qwen3":
            if QWEN_TRANSLATOR is None:
                QWEN_TRANSLATOR = LlamaCppTranslator()
            translated = QWEN_TRANSLATOR.translate(text, context=build_model_context(context, glossary_entries))
            if translated:
                library_store.save_memory(memory_scope, text, translated)
                _record_translation_event("engine_output", text, translated, "qwen3",
                                          chapter_id=chapter_id, chapter_title=chapter_title,
                                          page_index=page_index, region_index=region_index,
                                          bubble_id=bubble_id, context=context,
                                          model_name=Path(getattr(QWEN_TRANSLATOR, "model_path", "Qwen3 local model")).name,
                                          quantization="as-configured")
            return translated
        if CURRENT_TRANSLATION_ENGINE == "madlad":
            if MADLAD_TRANSLATOR is None:
                MADLAD_TRANSLATOR = MadladTranslator()
            translated = MADLAD_TRANSLATOR.translate(text, context=context)
            if translated:
                library_store.save_memory(memory_scope, text, translated)
                _record_translation_event("engine_output", text, translated, "madlad",
                                          chapter_id=chapter_id, chapter_title=chapter_title,
                                          page_index=page_index, region_index=region_index,
                                          bubble_id=bubble_id, context=context,
                                          model_name=str(getattr(MADLAD_TRANSLATOR, "model_path", "MADLAD-400")),
                                          quantization="as-configured")
            return translated
        if TRANSLATOR is None:
            TRANSLATOR = TranslatorEngine()
        translated = TRANSLATOR.translate(text)
        opus_draft = translated
        if translated:
            _record_translation_event("engine_output", text, translated, "opus",
                                      chapter_id=chapter_id, chapter_title=chapter_title,
                                      page_index=page_index, region_index=region_index,
                                      bubble_id=bubble_id, context=context,
                                      model_name="OPUS-MT en-ar ONNX INT8", quantization="INT8")
        if translated and proofreader_active:
            try:
                if QWEN_PROOFREADER is None:
                    QWEN_PROOFREADER = LlamaCppTranslator(
                        model_path=DEFAULT_PROOFREADER_MODEL,
                        port=9932,
                        startup_timeout=300,
                    )
                edited = QWEN_PROOFREADER.proofread(
                    text, translated, context=build_model_context(context, glossary_entries)
                )
                has_arabic = bool(edited) and any("\u0600" <= char <= "\u06ff" for char in edited)
                reasonable_length = len(edited) <= max(60, len(translated) * 2)
                if has_arabic and reasonable_length:
                    translated = edited
                    _record_translation_event("engine_output", text, edited, "qwen4b_proofreader",
                                              chapter_id=chapter_id, chapter_title=chapter_title,
                                              page_index=page_index, region_index=region_index,
                                              bubble_id=bubble_id, context=context,
                                              model_name=Path(getattr(QWEN_PROOFREADER, "model_path", "Qwen local proofreader")).name,
                                              quantization="as-configured",
                                              metadata={"draft_engine": "opus", "draft": opus_draft})
                else:
                    app.logger.warning("Qwen proofreader output failed validation; kept OPUS draft.")
            except (OSError, ValueError, RuntimeError, requests.RequestException) as exc:
                app.logger.warning("Qwen proofreader failed; kept OPUS draft: %s", exc)
        if translated:
            library_store.save_memory(memory_scope, text, translated)
        return translated
    except (OSError, ValueError, RuntimeError) as exc:
        raise RuntimeError(f"تعذرت الترجمة المحلية: {exc}") from exc


def _translate_index_region_group(items):
    """Translate an ordered group; fall back to the established per-region path on format failure."""
    batch_results = {}
    batch_eligible = (
        CURRENT_TRANSLATION_ENGINE == "index_translate_9b"
        and len(items) > 1
        and len(items) <= _index_batch_size()
        and all(item.get("region_type", "speech_bubble") == "speech_bubble" for item in items)
    )
    if batch_eligible:
        misses = []
        try:
            for position, item in enumerate(items):
                text = str(item.get("text", "")).strip()
                glossary_entries = _model_glossary_entries(
                    text, item.get("glossary_scope") or item.get("work_scope")
                )
                memory_scope = _translation_memory_scope(
                    text, CURRENT_TRANSLATION_ENGINE,
                    chapter_title=item.get("chapter_title", ""),
                    context=item.get("context", ""),
                    work_scope=item.get("work_scope"),
                    glossary_entries=glossary_entries,
                )
                remembered = library_store.get_memory(memory_scope, text)
                remembered = clean_translation_output(remembered) if remembered is not None else None
                if remembered is not None and has_arabic_letters(remembered):
                    # Let translate_text serve and record the hit through its
                    # established path; only uncached bubbles enter inference.
                    continue
                misses.append((position, item))

            if len(misses) > 1:
                miss_results = _translate_index_prompt_batch([item for _, item in misses])
                batch_results.update({
                    position: result
                    for (position, _item), result in zip(misses, miss_results)
                })
            elif len(misses) == 1:
                # A one-item "batch" adds formatting tokens and can weaken
                # dialogue context. Use the normal single-bubble path instead.
                pass
        except TranslationOutputError as exc:
            app.logger.warning("Index batch rejected; using separate region requests: %s", exc)
            batch_results.clear()
        except Exception as exc:
            # A cache read failure must not make the page unavailable. The
            # established translation path remains responsible for recovery.
            app.logger.warning("Index batch cache check failed; using ordinary requests: %s", exc)
            batch_results.clear()

    results = []
    for index, item in enumerate(items):
        kwargs = {
            "chapter_title": item.get("chapter_title", ""),
            "page_index": item.get("page_index"),
            "context": item.get("context", ""),
            "chapter_id": item.get("chapter_id"),
            "region_index": item.get("region_index"),
            "bubble_id": item.get("bubble_id"),
            "work_scope": item.get("work_scope"),
            "glossary_scope": item.get("glossary_scope"),
        }
        if index in batch_results:
            kwargs["_pretranslated"] = batch_results[index]
        results.append(translate_text(item["text"], **kwargs))
    return results


def _translate_region_group_gemini(items):
    """Cloud fallback for one page-translation unit when the local model is missing."""
    api_key = _gemini_api_key_from_local_settings()
    if not api_key:
        raise RuntimeError("مفتاح Gemini غير مضبوط في إعدادات الخادم، ومحرك الترجمة المحلي غير متاح.")
    normalized = [
        {"id": str(index), "text": item["text"],
         "type": item.get("region_type", "speech_bubble")}
        for index, item in enumerate(items, 1)
        if str(item.get("text", "")).strip()
    ]
    if not normalized:
        return ["" for _ in items]
    prompt = (
        "Translate the provided comic dialogue into natural, literary Arabic. "
        "Do not translate sound effects, decorative lettering, watermarks, credits, "
        "isolated UI labels, non-English text, or uncertain crops; return an empty "
        "Arabic string for those. Translate dialogue, narration, captions, signs, and "
        "phone text. Return only JSON with shape "
        "{\"translations\":[{\"id\":string,\"translation\":string}]}. "
        "Return every input id exactly once; never add or alter ids.\nREGIONS:\n"
        + json.dumps(normalized, ensure_ascii=False)
    )
    response = None
    for attempt in range(3):
        try:
            response = requests.post(
                "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent",
                headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                json={"contents": [{"parts": [{"text": prompt}]}],
                      "generationConfig": {"responseMimeType": "application/json", "temperature": 0.25}},
                timeout=(10, 60),
            )
            if response.ok or response.status_code != 429:
                break
            time.sleep(min(20.0, 5.0 * (2 ** attempt)))
        except requests.RequestException:
            if attempt == 2:
                response = None
            time.sleep(2.0)
    if response is None or not response.ok:
        raise RuntimeError("تعذرت الترجمة السحابية البديلة عبر Gemini.")
    try:
        raw = response.json()["candidates"][0]["content"]["parts"][0]["text"]
        decoded = json.loads(raw)
        mapped = {
            item["id"]: item["translation"].strip()
            for item in decoded.get("translations", [])
            if isinstance(item, dict) and isinstance(item.get("translation"), str)
        }
    except (KeyError, IndexError, TypeError, ValueError):
        raise RuntimeError("وصل رد Gemini البديل لكن تنسيقه غير صالح.")
    results = []
    for index, item in enumerate(items, 1):
        if not str(item.get("text", "")).strip():
            results.append("")
        else:
            translated = mapped.get(str(index), "")
            results.append(translated if has_arabic_letters(translated) else "")
    return results


def _translate_region_group_with_cloud_fallback(items):
    try:
        return _translate_index_region_group(items)
    except RuntimeError as exc:
        message = str(exc)
        if "unavailable" in message or "غير موجود" in message or "غير متاح" in message:
            return _translate_region_group_gemini(items)
        raise


def set_translation_engine(engine_name):
    global CURRENT_TRANSLATION_ENGINE, TRANSLATOR, QWEN_TRANSLATOR, QWEN_PROOFREADER, MADLAD_TRANSLATOR, BALANCED_TRANSLATOR
    if engine_name != "index_translate_9b":
        raise ValueError("النسخة الحالية مثبتة على محرك Index فقط.")
    if engine_name == "index_translate_9b":
        get_balanced_translator()
        if not BALANCED_TRANSLATOR.status()[engine_name]["available"]:
            reason = BALANCED_TRANSLATOR.status()[engine_name]["availability_reason"]
            raise RuntimeError(f"المحرك المطلوب غير متاح: {reason}")
    if engine_name == "qwen3" and not LlamaCppTranslator.available():
        raise RuntimeError("نموذج Qwen 3 أو برنامج llama.cpp غير مثبت محلياً بعد.")
    if engine_name == "madlad" and not MadladTranslator.available():
        raise RuntimeError("نموذج MADLAD المحلي أو CTranslate2 غير جاهز بعد.")
    if CURRENT_TRANSLATION_ENGINE == "qwen3" and engine_name != "qwen3" and QWEN_TRANSLATOR:
        QWEN_TRANSLATOR.shutdown()
        QWEN_TRANSLATOR = None
    if (engine_name != CURRENT_TRANSLATION_ENGINE and engine_name != "balanced_auto"
            and BALANCED_TRANSLATOR is not None):
        BALANCED_TRANSLATOR.shutdown()
        BALANCED_TRANSLATOR = None
    if engine_name != "madlad" and MADLAD_TRANSLATOR is not None:
        MADLAD_TRANSLATOR = None
    if engine_name != "madlad":
        MADLAD_TRANSLATOR = None
    if engine_name != "opus" and QWEN_PROOFREADER is not None:
        QWEN_PROOFREADER.shutdown()
        QWEN_PROOFREADER = None
    if engine_name != CURRENT_TRANSLATION_ENGINE:
        TRANSLATOR = None
    CURRENT_TRANSLATION_ENGINE = engine_name


def group_translation_regions(lines, reading_direction="ltr"):
    """Group OCR text lines into likely dialogue blocks before translation.

    The result is a geometric heuristic, not speech-bubble detection.
    """
    if reading_direction not in {"ltr", "rtl"}:
        reading_direction = "ltr"
    normalized = []
    for line in lines:
        try:
            item = {
                "text": str(line.get("text", "")).strip(),
                "left": float(line.get("left", 0)),
                "top": float(line.get("top", 0)),
                "width": float(line.get("width", 0)),
                "height": float(line.get("height", 0)),
                "confidence": float(line.get("confidence", 0.5)),
            }
        except (TypeError, ValueError):
            continue
        if item["text"] and item["width"] > 0 and item["height"] > 0:
            normalized.append(item)
    if not normalized:
        return []
    normalized.sort(key=lambda item: (item["top"], -item["left"] if reading_direction == "rtl" else item["left"]))
    return LocalRapidOCR.group_lines(normalized)


@app.get("/api/glossary")
def glossary_list():
    return jsonify(terms=library_store.list_glossary())


@app.get("/api/glossary/export")
def glossary_export():
    response = jsonify(version=1, terms=library_store.list_glossary())
    response.headers["Content-Disposition"] = 'attachment; filename="manga-arabic-glossary.json"'
    return response


@app.post("/api/glossary/import")
def glossary_import():
    payload = request.get_json(silent=True)
    terms = payload.get("terms") if isinstance(payload, dict) else None
    if not isinstance(terms, list) or len(terms) > 5000:
        return jsonify(error="ملف القاموس يجب أن يحتوي قائمة لا تتجاوز 5000 مصطلح."), 400
    normalized = []
    seen = set()
    for term in terms:
        if not isinstance(term, dict):
            return jsonify(error="أحد عناصر القاموس ليس كائناً صالحاً."), 400
        source = term.get("source_term", term.get("source"))
        target = term.get("target_term", term.get("target"))
        scope = term.get("scope_key", term.get("scope", "*"))
        if not all(isinstance(value, str) for value in (source, target, scope)):
            return jsonify(error="المصدر والمقابل والنطاق يجب أن تكون نصوصاً."), 400
        source, target, scope = source.strip(), target.strip(), scope.strip() or "*"
        if not source or not target or len(source) > 120 or len(target) > 200 or len(scope) > 160:
            return jsonify(error="أحد المصطلحات فارغ أو يتجاوز الحد المسموح لطوله."), 400
        key = (library_store.normalize_glossary_scope(scope), library_store.normalize_term(source))
        if key not in seen:
            normalized.append({"source_term": source, "target_term": target, "scope_key": scope})
            seen.add(key)
    try:
        imported = library_store.import_glossary_terms(normalized)
    except Exception as exc:
        app.logger.exception("Glossary import failed")
        return jsonify(error=f"تعذر استيراد القاموس: {exc}"), 500
    return jsonify(imported=imported, total=len(library_store.list_glossary())), 200


@app.get("/api/translation-history")
def translation_history():
    """Search append-only engine outputs and human corrections; JSON by default."""
    try:
        limit = int(request.args.get("limit", "500"))
        page_index = request.args.get("page_index")
        page_index = int(page_index) if page_index is not None else None
        if page_index is not None and page_index < 1:
            raise ValueError("page_index must be positive")
        events = library_store.list_translation_events(
            chapter_id=request.args.get("chapter_id") or None,
            page_index=page_index, limit=limit,
        )
    except (TypeError, ValueError) as exc:
        return jsonify(error=f"مرشحات سجل الترجمة غير صالحة: {exc}"), 400
    output_format = (request.args.get("format") or "json").lower()
    if output_format == "json":
        return jsonify(events=events)
    columns = [
        "event_id", "created_at", "event_type", "chapter_id", "chapter_title",
        "page_index", "region_index", "bubble_id", "source_text", "context_text",
        "target_text", "engine_id", "model_name", "model_revision", "quantization",
        "prompt_version", "review_status", "quality_rating", "source_license",
        "training_eligible", "metadata",
    ]
    if output_format == "jsonl":
        content = "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events)
        return Response(content, mimetype="application/x-ndjson; charset=utf-8",
                        headers={"Content-Disposition": "attachment; filename=translation-history.jsonl"})
    if output_format == "csv":
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for event in events:
            row = dict(event)
            row["metadata"] = json.dumps(row.get("metadata", {}), ensure_ascii=False)
            writer.writerow(row)
        return Response(buffer.getvalue(), mimetype="text/csv; charset=utf-8",
                        headers={"Content-Disposition": "attachment; filename=translation-history.csv"})
    return jsonify(error="صيغة التصدير المدعومة هي json أو jsonl أو csv."), 400


@app.get("/api/translation-engine")
def translation_engine_status():
    router = get_balanced_translator()
    model_status = router.status()
    automatic_engine = router.preferred_engine_id()
    preferred_engine = automatic_engine
    if preferred_engine == "index_translate_9b":
        note = "يعمل التطبيق بمحرك Index المحلي فقط، مع سياق الحوار والتمييز بين الفقاعة والسرد الخارجي. جودة المعنى تعتمد على النموذج وتحتاج مراجعة بشرية عند الشك."
    else:
        failures = [
            f"{engine_id}: {details['session_error']}"
            for engine_id, details in model_status.items()
            if details.get("session_error")
        ]
        note = "محرك Index غير جاهز. ضع أوزان Index وملف llama-server.exe في المسارات الموثقة ثم أعد تشغيل التطبيق."
        if failures:
            note += " تعذّر تشغيل: " + "؛ ".join(failures)
    payload = dict(
        current="index_translate_9b",
        mode="index_only",
        preferred_engine=preferred_engine,
        note=note,
        model_status=model_status,
        engines=[
            {"id": "index_translate_9b", "label": "Index-Translate-9B",
             "available": model_status["index_translate_9b"]["available"],
             "reason": model_status["index_translate_9b"]["availability_reason"]},
        ],
        proofreader={
            "enabled": ENABLE_LOCAL_PROOFREADER,
            "available": DEFAULT_PROOFREADER_MODEL.is_file() and LlamaCppTranslator.available(),
            "model": "Qwen3-4B Q4_K_M (local CPU)",
        },
    )
    if request.args.get("probe") == "1":
        index_adapter = next(
            (adapter for adapter in router.adapters if adapter.engine_id == "index_translate_9b"),
            None,
        )
        diagnostic = getattr(index_adapter, "diagnose_server", None)
        payload["server_diagnostic"] = (
            diagnostic() if callable(diagnostic) else {
                "status": "unsupported",
                "message": "هذا المحرك لا يوفر فحص اتصال دون تحميل النموذج.",
            }
        )
    return jsonify(**payload)


@app.post("/api/translation-engine")
def translation_engine_select():
    payload = request.get_json(silent=True) or {}
    try:
        selected_engine = str(payload.get("engine", ""))
        if selected_engine != "index_translate_9b":
            raise ValueError("النسخة الحالية مثبتة على محرك Index فقط.")
        set_translation_engine(selected_engine)
        return jsonify(**translation_engine_status().json)
    except (ValueError, RuntimeError) as exc:
        return jsonify(error=str(exc), current="index_translate_9b"), 409


@app.post("/api/translation-proofreader")
def translation_proofreader_select():
    global ENABLE_LOCAL_PROOFREADER, QWEN_PROOFREADER
    payload = request.get_json(silent=True) or {}
    enabled = bool(payload.get("enabled", False))
    if enabled and not (DEFAULT_PROOFREADER_MODEL.is_file() and LlamaCppTranslator.available()):
        return jsonify(error="نموذج Qwen3-4B أو llama.cpp غير موجود محلياً."), 409
    ENABLE_LOCAL_PROOFREADER = enabled
    if not enabled and QWEN_PROOFREADER is not None:
        QWEN_PROOFREADER.shutdown()
        QWEN_PROOFREADER = None
    return jsonify(
        enabled=ENABLE_LOCAL_PROOFREADER,
        available=DEFAULT_PROOFREADER_MODEL.is_file() and LlamaCppTranslator.available(),
        applies_to="opus",
    )


@app.post("/api/glossary")
def glossary_upsert():
    payload = request.get_json(silent=True) or {}
    source_value, target_value, scope_value = (
        payload.get("source", ""), payload.get("target", ""), payload.get("scope", "*")
    )
    if not all(isinstance(value, str) for value in (source_value, target_value, scope_value)):
        return jsonify(error="المصطلح والمقابل والنطاق يجب أن تكون نصوصاً."), 400
    source, target, scope = source_value.strip(), target_value.strip(), scope_value.strip()
    if not source or not target or len(source) > 120 or len(target) > 200:
        return jsonify(error="أدخل مصطلحاً ومقابلاً عربياً (120 و200 حرف كحد أقصى)."), 400
    if len(scope) > 160:
        return jsonify(error="اسم نطاق القاموس يجب ألا يتجاوز 160 حرفاً."), 400
    library_store.save_glossary_term(source, target, scope or "*")
    return jsonify(term={"source_term": source, "target_term": target,
                         "scope_key": library_store.normalize_glossary_scope(scope)}), 201


@app.get("/api/image")
def proxy_image():
    image_url = (request.args.get("url") or "").strip()
    referer = (request.args.get("referer") or "").strip()
    parsed = urlparse(image_url)
    ref_parsed = urlparse(referer)
    if not public_https_url(image_url) or ref_parsed.hostname not in CHAPTER_HOSTS:
        return jsonify(error="رابط الصورة أو مرجع الفصل غير صالح."), 400
    try:
        image_response = safe_get(image_url, headers={"Referer": referer}, timeout=30)
        image_response.raise_for_status()
        content_type = image_response.headers.get("Content-Type", "").split(";")[0].lower()
        if not content_type.startswith("image/"):
            return jsonify(error="المصدر لم يُرجع ملف صورة."), 415
        return Response(image_response.content, mimetype=content_type, headers={"Cache-Control": "public, max-age=1800"})
    except (requests.RequestException, ValueError) as exc:
        return jsonify(error=f"تعذر تحميل الصورة من المصدر: {exc}"), 502


@app.get("/api/translate/progress/<job_id>")
def translate_page_progress(job_id):
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        return jsonify(error="معرّف متابعة الصفحة غير صالح."), 400
    with _REMOTE_TRANSLATION_PROGRESS_LOCK:
        progress = REMOTE_TRANSLATION_PROGRESS.get(job_id)
        if progress is None:
            return jsonify(error="لم يُعثر على حالة معالجة هذه الصفحة."), 404
        snapshot = dict(progress)
    region_started = snapshot.pop("current_region_started_at", None)
    snapshot["current_region_elapsed_seconds"] = (
        round(max(0.0, time.time() - region_started), 1)
        if isinstance(region_started, (int, float)) else None
    )
    return jsonify(progress=snapshot)


@app.post("/api/translate")
def translate_page():
    page_started = time.perf_counter()
    data = request.get_json(silent=True) or {}
    if str(data.get("reading_layout", "")).strip().lower() != "vertical":
        return jsonify(error=UNSUPPORTED_LAYOUT_MESSAGE), 422
    job_id = str(data.get("job_id", ""))
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        job_id = ""
    update_remote_translation_progress(
        job_id, stage="downloading", completed_regions=0, total_regions=None,
    )
    reading_direction = str(data.get("reading_direction", "ltr")).strip().lower()
    if reading_direction not in {"ltr", "rtl"}:
        return jsonify(error="اتجاه القراءة يجب أن يكون ltr أو rtl."), 400
    image_url = (data.get("image") or "").strip()
    referer = (data.get("referer") or "").strip()
    parsed = urlparse(image_url)
    ref_parsed = urlparse(referer)
    if not public_https_url(image_url) or ref_parsed.hostname not in CHAPTER_HOSTS:
        return jsonify(error="رابط الصورة أو مرجع الفصل غير صالح."), 400

    try:
        # Download once and reuse OCR results from the in-memory cache.
        update_remote_translation_progress(job_id, stage="downloading")
        image_response = safe_get(image_url, headers={"Referer": referer}, timeout=30)
        image_response.raise_for_status()
        content_type = image_response.headers.get("Content-Type", "").split(";")[0].lower()
        if not content_type.startswith("image/"):
            return jsonify(error="رابط الصفحة لم يُرجع صورة صالحة."), 415
        image_hash = hashlib.sha256(image_response.content).hexdigest()
        include_external = str(data.get("series_type", "manhwa")).strip().lower() in SUPPORTED_VERTICAL_COMIC_TYPES
        provider = os.getenv("OCR_PROVIDER", "local").strip().lower()
        vision_key = os.getenv("GOOGLE_CLOUD_VISION_API_KEY")
        ocr_key = os.getenv("OCR_SPACE_API_KEY")
        fallback_config = f"vision={int(bool(vision_key))}:space={int(bool(ocr_key))}"
        detection_only = bool(data.get("ocr_only")) and bool(data.get("cloud_ocr"))
        ocr_cache_key = (
            f"{image_hash}:external={int(include_external)}:direction={reading_direction}"
            f":provider={provider}:{fallback_config}:detect_only={int(detection_only)}"
        )
        with _OCR_CACHE_LOCK:
            cached_payload = OCR_CACHE.get(ocr_cache_key)
            if cached_payload is not None:
                OCR_CACHE.move_to_end(ocr_cache_key)
                cached_ocr = json.loads(cached_payload)
            else:
                cached_ocr = None
        ocr_timing_breakdown = None
        if cached_ocr is not None:
            extracted, ocr_lines, ocr_provider, *cached_regions = cached_ocr
            bubble_regions = cached_regions[0] if cached_regions else None
            ocr_timing_breakdown = cached_regions[1] if len(cached_regions) > 1 else None
        else:
            update_remote_translation_progress(job_id, stage="ocr")
            if provider not in {"local", "auto", "google", "ocrspace"}:
                raise RuntimeError("OCR_PROVIDER يجب أن يكون local أو auto أو google أو ocrspace.")
            ocr_elapsed = None
            if provider in {"local", "auto"}:
                try:
                    local_result = (
                        run_local_detection(
                            image_response.content,
                            reading_direction=reading_direction,
                            include_external=include_external,
                        )
                        if detection_only else
                        run_local_ocr(
                            image_response.content,
                            reading_direction=reading_direction,
                            include_external=include_external,
                        )
                    )
                    extracted, ocr_lines = local_result["text"], local_result["lines"]
                    ocr_provider = local_result["provider"]
                    ocr_elapsed = local_result["elapsed_seconds"]
                    ocr_timing_breakdown = _ocr_timing_breakdown(local_result)
                    bubble_regions = local_result.get("regions")
                except Exception as local_error:
                    if provider == "local" or not (vision_key or ocr_key):
                        raise RuntimeError(f"فشل OCR المحلي: {local_error}") from local_error
                    extracted, ocr_lines, ocr_provider, bubble_regions = None, None, None, None
            else:
                extracted, ocr_lines, ocr_provider, bubble_regions = None, None, None, None

            if extracted is not None:
                pass
            elif provider == "google" or (provider == "auto" and vision_key):
                if not vision_key:
                    raise RuntimeError("مفتاح Google Vision غير مضبوط: GOOGLE_CLOUD_VISION_API_KEY.")
                try:
                    extracted, ocr_lines = extract_google_vision(image_response.content, vision_key)
                    ocr_provider = "google_vision"
                except Exception as google_error:
                    if not ocr_key:
                        raise RuntimeError(f"فشل Google Vision OCR: {google_error}") from google_error
                    extracted, ocr_lines = run_ocr_space(image_response.content, content_type, ocr_key)
                    ocr_provider = "ocr_space_fallback"
            elif provider in {"ocrspace", "auto"}:
                if not ocr_key:
                    raise RuntimeError("OCR المحلي غير متاح، ولم يتم ضبط مفتاح OCR سحابي احتياطي.")
                extracted, ocr_lines = run_ocr_space(image_response.content, content_type, ocr_key)
                ocr_provider = "ocr_space"
            if bubble_regions is None and ocr_lines and ocr_provider in {"google_vision", "ocr_space", "ocr_space_fallback"}:
                ocr_lines, bubble_regions = LocalRapidOCR.associate_lines_with_bubbles(
                    image_response.content, ocr_lines, reading_direction
                )
                extracted = "\n".join(line["text"] for line in ocr_lines)
            cache_ocr_result(
                ocr_cache_key,
                (extracted, ocr_lines, ocr_provider, bubble_regions, ocr_timing_breakdown),
            )
        line_texts = [line["text"] for line in ocr_lines if line.get("text")]
        if data.get("ocr_only"):
            regions = bubble_regions or group_translation_regions(ocr_lines or [], reading_direction)
            return jsonify(
                original=extracted or "",
                ocr_lines=ocr_lines or [],
                regions=[{
                    "id": f"p{data.get('page_index', 0)}r{index}",
                    "page_index": data.get("page_index"),
                    "region_index": index,
                    "text": region.get("text", ""),
                    "region_type": region.get("region_type", "speech_bubble"),
                    "left": region.get("left", 0), "top": region.get("top", 0),
                    "width": region.get("width", 0), "height": region.get("height", 0),
                    "angle": region.get("angle", 0), "bubble_id": region.get("bubble_id"),
                    "confidence": region.get("confidence"),
                    **({key: region[key] for key in (
                        "bubble_bbox", "bubble_confidence", "ocr_boxes", "ocr_polygons",
                        "detection_fallback",
                    ) if key in region}),
                } for index, region in enumerate(regions, 1)
                    if region.get("text") or detection_only],
                ocr_provider=ocr_provider,
                ocr_elapsed_seconds=ocr_elapsed if 'ocr_elapsed' in locals() else None,
                ocr_timing_breakdown=ocr_timing_breakdown,
                local_recognition_skipped=bool(detection_only),
            )
        translation_failures = []
        if line_texts:
            dialogue_regions = bubble_regions or group_translation_regions(
                ocr_lines, reading_direction
            )
            # Translate grouped dialogue fragments together so sentence
            # boundaries are less likely to be interpreted as isolated words.
            translated_lines = []
            translation_elapsed = 0.0
            completed_regions = 0
            update_remote_translation_progress(
                job_id, stage="translation", completed_regions=0,
                total_regions=len(dialogue_regions), current_region=1,
            )
            translation_requests = []
            for index, region in enumerate(dialogue_regions, 1):
                context = "\n".join(
                    _dialogue_context_line(item)
                    for item in dialogue_regions[max(0, index - 3):index - 1]
                    if _dialogue_context_line(item)
                )
                context = _build_translation_context(
                    region.get("region_type"), context, region.get("confidence")
                )
                translation_requests.append({
                    "text": region["text"], "context": context,
                    "region_type": region.get("region_type", "speech_bubble"),
                    "chapter_title": data.get("chapter_title", ""),
                    "page_index": data.get("page_index"), "region_index": index,
                    "bubble_id": region.get("bubble_id"),
                    "work_scope": data.get("work_scope") or data.get("chapter_title", ""),
                    "glossary_scope": data.get("series_title"), "region": region,
                })

            translation_units = []
            cursor = 0
            batch_limit = _index_batch_size() if CURRENT_TRANSLATION_ENGINE == "index_translate_9b" else 1
            while cursor < len(translation_requests):
                unit = [translation_requests[cursor]]
                cursor += 1
                while (len(unit) < batch_limit and cursor < len(translation_requests)
                       and unit[0]["region_type"] == "speech_bubble"
                       and translation_requests[cursor]["region_index"] == unit[-1]["region_index"] + 1
                       and translation_requests[cursor]["region_type"] == "speech_bubble"):
                    unit.append(translation_requests[cursor])
                    cursor += 1
                translation_units.append(unit)

            for unit_number, unit in enumerate(translation_units):
                first_index = unit[0]["region_index"]
                translation_started = time.perf_counter()
                update_remote_translation_progress(
                    job_id, stage="translation", completed_regions=completed_regions,
                    total_regions=len(dialogue_regions), current_region=first_index,
                    current_region_started_at=time.time(),
                )
                try:
                    outputs = _translate_index_region_group(unit)
                    if len(outputs) != len(unit):
                        raise RuntimeError("The translation engine returned a mismatched region count.")
                except (OSError, ValueError, RuntimeError, requests.RequestException) as exc:
                    elapsed = time.perf_counter() - translation_started
                    message = str(exc).strip()[:300] or type(exc).__name__
                    translation_failures.append({"index": first_index, "message": message})
                    outputs = [""] * len(unit)

                elapsed = time.perf_counter() - translation_started
                translation_elapsed += elapsed
                failed_in_unit = False
                for item, arabic_text in zip(unit, outputs):
                    index = item["region_index"]
                    region = item["region"]
                    if not isinstance(arabic_text, str) or not has_arabic_letters(arabic_text):
                        arabic_text = ""
                        failed_in_unit = True
                        if not translation_failures or translation_failures[-1]["index"] != index:
                            translation_failures.append({
                                "index": index,
                                "message": "لم ينتج المحرك ترجمة عربية لهذه المنطقة.",
                            })
                    translated_line = {
                        "index": index,
                        "text": region["text"],
                        "translated": arabic_text,
                        "left": region["left"],
                        "top": region["top"],
                        "width": region["width"],
                        "height": region["height"],
                        "translation_elapsed_seconds": round(elapsed, 4),
                        "line_indexes": region.get("line_indexes", []),
                        **({key: region[key] for key in (
                            "confidence", "bubble_id", "bubble_bbox", "bubble_confidence", "ocr_boxes",
                            "ocr_polygons", "region_type", "angle", "ocr_recovered",
                            "ocr_review_required", "ocr_review_reason", "ocr_unrecognized_word_count",
                            "ocr_suspicious_near_words",
                        ) if key in region}),
                    }
                    if not arabic_text and translation_failures and translation_failures[-1]["index"] == index:
                        translated_line["translation_error"] = translation_failures[-1]["message"]
                        translated_line["translation_attempt_elapsed_seconds"] = round(elapsed, 4)
                    translated_lines.append(translated_line)
                    completed_regions += int(bool(arabic_text))
                    update_remote_translation_progress(
                        job_id, stage="translation", completed_regions=completed_regions,
                        total_regions=len(dialogue_regions), current_region=index,
                        last_region_status="complete" if arabic_text else "failed",
                        current_region_started_at=None,
                    )
                if failed_in_unit:
                    for remaining_index, remaining_region in enumerate(
                        dialogue_regions[len(translated_lines):], len(translated_lines) + 1
                    ):
                        translated_lines.append({
                            "index": remaining_index,
                            "text": remaining_region["text"], "translated": "",
                            "left": remaining_region["left"], "top": remaining_region["top"],
                            "width": remaining_region["width"], "height": remaining_region["height"],
                            "line_indexes": remaining_region.get("line_indexes", []),
                            **({key: remaining_region[key] for key in (
                                "confidence", "bubble_id", "bubble_bbox", "bubble_confidence",
                                "ocr_boxes", "ocr_polygons", "region_type", "angle", "ocr_recovered",
                                "ocr_review_required", "ocr_review_reason", "ocr_unrecognized_word_count",
                                "ocr_suspicious_near_words",
                            ) if key in remaining_region}),
                            "translation_error": "لم تبدأ هذه المنطقة لأن المنطقة السابقة تعثرت؛ أعد محاولتها بعد مراجعة النص.",
                        })
                    break
            translated = "\n".join(item["translated"] for item in translated_lines if item["translated"])
        else:
            translation_started = time.perf_counter()
            translated = translate_text(
                extracted, data.get("chapter_title", ""), data.get("page_index"),
                work_scope=data.get("work_scope") or data.get("chapter_title", ""),
                glossary_scope=data.get("series_title"),
            )
            translation_elapsed = time.perf_counter() - translation_started
            translated_lines = []
        incomplete = bool(translation_failures)
        update_remote_translation_progress(
            job_id, stage="partial" if incomplete else "complete",
            completed_regions=sum(bool(item.get("translated")) for item in translated_lines),
            total_regions=len(translated_lines),
            error=translation_failures[0]["message"] if translation_failures else None,
            current_region_started_at=None,
        )
        return jsonify(original=extracted, translated=translated,
                       ocr_lines=ocr_lines, translated_lines=translated_lines,
                       ocr_provider=ocr_provider,
                       ocr_elapsed_seconds=ocr_elapsed if 'ocr_elapsed' in locals() else None,
                       ocr_timing_breakdown=ocr_timing_breakdown,
                       translation_failures=translation_failures if line_texts else [],
                       processing_incomplete=incomplete if line_texts else False,
                       translation_elapsed_seconds=round(translation_elapsed, 4),
                       page_processing_elapsed_seconds=round(time.perf_counter() - page_started, 4))
    except (requests.RequestException, ValueError, RuntimeError) as exc:
        update_remote_translation_progress(job_id, stage="failed", error=str(exc).strip()[:300])
        return jsonify(error=f"تعذرت المعالجة: {exc}"), 502


def _gemini_api_key_from_local_settings():
    configured = os.environ.get("GEMINI_API_KEY", "").strip()
    if configured:
        return configured
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    try:
        with open(env_path, "r", encoding="utf-8-sig") as env_file:
            for line in env_file:
                if line.startswith("GEMINI_API_KEY="):
                    return line.partition("=")[2].strip().strip("\"'")
    except OSError:
        pass
    return ""


@app.post("/api/translate/chapter")
def translate_remote_chapter_once():
    """Translate all OCR regions for one chapter in a single Gemini request."""
    data = request.get_json(silent=True) or {}
    regions = data.get("regions")
    if not isinstance(regions, list) or not regions or len(regions) > 1200:
        return jsonify(error="قائمة نصوص الفصل فارغة أو أكبر من الحد المسموح."), 400
    detector_regions = []
    seen = set()
    for region in regions:
        if not isinstance(region, dict):
            continue
        region_id = str(region.get("id", ""))[:80]
        text = " ".join(str(region.get("text", "")).split()).strip()
        if not region_id or region_id in seen or (not text and not data.get("cloud_ocr")):
            continue
        seen.add(region_id)
        detector_regions.append({
            **region,
            "id": region_id,
            "text": text[:4000],
            "page_image": str(region.get("page_image", "")).strip(),
            "type": "narration_or_caption" if region.get("region_type") == "external_text" else "dialogue",
        })
    if not detector_regions or sum(len(item["text"]) for item in detector_regions) > 120000:
        return jsonify(error="لا توجد نصوص صالحة، أو تجاوز حجم نصوص الفصل الحد الآمن."), 400
    api_key = _gemini_api_key_from_local_settings()
    if not api_key:
        return jsonify(error="مفتاح Gemini غير مضبوط في إعدادات الخادم (.env)."), 503

    ocr_metrics = {
        "model": "local_detector_only",
        "regions_expected": len(detector_regions),
        "regions_cloud_read": 0,
        "regions_fallback": len(detector_regions),
        "requests_sent": 0,
        "usage": [],
    }
    ocr_by_id = {item["id"]: item for item in detector_regions}
    time_budget = data.get("time_budget")
    time_budget = time_budget if isinstance(time_budget, (int, float)) and 10 <= time_budget <= 55 else 45
    deadline = time.perf_counter() + time_budget
    fingerprint_source = json.dumps(
        {"title": str(data.get("chapter_title", ""))[:300], "regions": detector_regions},
        ensure_ascii=False, sort_keys=True,
    )
    fingerprint = hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest()
    cached_job = _chapter_translation_cache_get(fingerprint)
    if cached_job is not None:
        ocr_by_id = {item["id"]: item for item in detector_regions}
        ocr_by_id.update(cached_job["ocr_by_id"])
        ocr_metrics["requests_sent"] = cached_job.get("requests_sent", 0)
        ocr_metrics["usage"] = list(cached_job.get("usage", []))

    def ocr_partial_response():
        cloud_read = sum(
            ocr_by_id[item["id"]].get("cloud_ocr_status") == "complete"
            for item in detector_regions if item["id"] in ocr_by_id
        )
        ocr_metrics["regions_cloud_read"] = cloud_read
        ocr_metrics["regions_fallback"] = len(detector_regions) - cloud_read
        return jsonify(
            partial=True, fingerprint=fingerprint,
            progress={"stage": "cloud_ocr",
                      "completed_regions": len([i for i in detector_regions if i["id"] in ocr_by_id and "cloud_ocr_status" in ocr_by_id[i["id"]]]),
                      "total_regions": len(detector_regions)},
            ocr_metrics=ocr_metrics,
        )

    if data.get("cloud_ocr", True):
        referer = str(data.get("referer", "")).strip()
        if not referer or not public_https_url(referer):
            return jsonify(error="مرجع الفصل غير صالح لإعادة قراءة مناطق OCR."), 400

        last_page = {"url": None, "bytes": None}
        def load_source_page(image_url):
            image_url = str(image_url or "").strip()
            if image_url != last_page["url"]:
                if not public_https_url(image_url):
                    raise ValueError("رابط إحدى صور الفصل غير صالح.")
                response = safe_get(
                    image_url, headers={"Referer": referer}, timeout=30,
                    max_bytes=12 * 1024 * 1024,
                )
                content_type = response.headers.get("Content-Type", "").split(";")[0].lower()
                if not content_type.startswith("image/"):
                    raise ValueError("مصدر إحدى الصفحات لم يُرجع صورة صالحة.")
                last_page["url"] = image_url
                last_page["bytes"] = response.content
            return last_page["bytes"]

        pending = [
            item for item in detector_regions
            if "cloud_ocr_status" not in ocr_by_id.get(item["id"], {})
        ]
        try:
            for start in range(0, len(pending), 20):
                if time.perf_counter() > deadline:
                    _chapter_translation_cache_put(
                        fingerprint, dict(ocr_by_id),
                        ocr_metrics["requests_sent"], ocr_metrics["usage"],
                    )
                    return ocr_partial_response()
                chunk = pending[start:start + 20]
                cloud_regions, chunk_metrics = recognize_regions_with_gemini(
                    chunk, load_source_page, api_key,
                )
                ocr_by_id.update({item["id"]: item for item in cloud_regions})
                ocr_metrics["requests_sent"] += chunk_metrics.get("requests_sent", 0)
                ocr_metrics["usage"].extend(chunk_metrics.get("usage", []))
        except (ValueError, RuntimeError, OSError, requests.RequestException) as exc:
            _chapter_translation_cache_put(
                fingerprint, dict(ocr_by_id),
                ocr_metrics["requests_sent"], ocr_metrics["usage"],
            )
            return jsonify(
                error=f"تعذر إكمال OCR السحابي قبل الترجمة: {exc}",
                ocr_model="gemini-3.5-flash-lite",
                partial=True, fingerprint=fingerprint,
            ), 502
        if time.perf_counter() > deadline:
            _chapter_translation_cache_put(
                fingerprint, dict(ocr_by_id),
                ocr_metrics["requests_sent"], ocr_metrics["usage"],
            )
            return ocr_partial_response()
        _chapter_translation_cache_put(
            fingerprint, dict(ocr_by_id),
            ocr_metrics["requests_sent"], ocr_metrics["usage"],
        )

    excluded_categories = {
        "sound_effect", "watermark", "ui", "decorative", "non_english", "uncertain",
    }
    normalized = []
    for item in detector_regions:
        recognized = ocr_by_id.get(item["id"], item)
        text = " ".join(str(recognized.get("text", item["text"])).split()).strip()
        category = recognized.get("content_category")
        fallback = recognized.get("cloud_ocr_status") == "local_fallback"
        letters = re.findall(r"[A-Za-z]", text)
        latin_fraction = len(letters) / max(
            1, sum(1 for char in text if char.isalpha())
        )
        uncertain_dialogue = (
            category == "uncertain"
            and item.get("region_type") == "speech_bubble"
        )
        uncertain_short_sign = (
            category == "uncertain"
            and item.get("region_type") == "external_text"
            and len(text.split()) <= 6
            and not LocalRapidOCR.is_likely_nonverbal_effect(text)
            and not _is_domain_only_credit(text)
            and not _is_source_promotion(text)
            and not _is_scanlation_role_credit(text)
            and not _is_series_byline_credit(text)
        )
        if (not text or latin_fraction < 0.55
                or (category in excluded_categories and not fallback
                    and not uncertain_dialogue and not uncertain_short_sign)):
            continue
        normalized.append({
            "id": item["id"],
            "text": text[:4000],
            "type": item["type"],
            "content_category": (
                "local_ocr_fallback" if fallback else
                "dialogue" if uncertain_dialogue else
                "sign" if uncertain_short_sign else
                (category or "uncertain")
            ),
        })
    if not normalized:
        return jsonify(
            error="لم يعثر OCR على حوار أو سرد إنجليزي صالح للترجمة.",
            ocr_metrics=ocr_metrics,
        ), 422
    if sum(len(item["text"]) for item in normalized) > 120000:
        return jsonify(error="تجاوز حجم النصوص الحد الآمن."), 400

    prompt = (
        "Translate the provided vertical comic chapter into natural, literary Arabic. "
        "Keep the story meaning, speaker intent, dialogue order, and character names consistent. "
        "Do not translate sound effects, decorative lettering, watermarks, credits, isolated UI labels, "
        "non-English text, or uncertain crops; return an empty Arabic string for those. "
        "Translate dialogue, narration, captions, signs, and phone text. Preserve natural spoken Arabic. "
        "Return only JSON with shape {\"translations\":[{\"id\":string,\"translation\":string}]}. "
        "Return every input id exactly once; never add or alter ids.\nCHAPTER: "
        + str(data.get("chapter_title", ""))[:300]
        + "\nREGIONS:\n" + json.dumps(normalized, ensure_ascii=False)
    )
    started = time.perf_counter()
    if time.perf_counter() > deadline:
        return jsonify(
            partial=True, fingerprint=fingerprint,
            progress={"stage": "translation", "completed_regions": 0,
                      "total_regions": len(normalized)},
            ocr_metrics=ocr_metrics,
        )
    translation_request_logs = []
    response = None
    for attempt in range(1, 4):
        attempt_started = time.perf_counter()
        record = {"attempt": attempt, "request_sent": True, "status": None}
        try:
            response = requests.post(
                "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent",
                headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                json={"contents": [{"parts": [{"text": prompt}]}],
                      "generationConfig": {"responseMimeType": "application/json", "temperature": 0.25}},
                timeout=(10, 120),
            )
            record["status"] = response.status_code
            record["elapsed_seconds"] = round(time.perf_counter() - attempt_started, 3)
            translation_request_logs.append(record)
            if response.ok or response.status_code == 429:
                break
            if response.status_code not in {500, 502, 503, 504} or attempt == 3:
                break
            if time.perf_counter() > deadline:
                break
            retry_after = response.headers.get("Retry-After", "")
            try:
                delay = min(90.0, max(0.0, float(retry_after)))
            except (TypeError, ValueError):
                delay = min(60.0, 15.0 * (2 ** (attempt - 1)))
            record["retry_wait_seconds"] = round(delay, 1)
            time.sleep(delay)
        except requests.RequestException as exc:
            record["elapsed_seconds"] = round(time.perf_counter() - attempt_started, 3)
            record["error"] = type(exc).__name__
            translation_request_logs.append(record)
            break
    api_key = ""
    elapsed = round(time.perf_counter() - started, 3)
    if response is None:
        return jsonify(
            error="تعذر الاتصال بـGemini. لم تُعد المحاولة بعد انقطاع الاتصال.",
            elapsed_seconds=elapsed, translation_requests=translation_request_logs,
            ocr_metrics=ocr_metrics,
        ), 502
    if response.status_code != 200:
        return jsonify(
            error=("تجاوزت حصة الطلبات اليومية/الدقيقة (HTTP 429)." if response.status_code == 429
                   else f"رفض Gemini الطلب (HTTP {response.status_code})."),
            elapsed_seconds=elapsed, translation_requests=translation_request_logs,
            api_requests_sent=ocr_metrics.get("requests_sent", 0) + len(translation_request_logs),
            ocr_metrics=ocr_metrics,
        ), 429 if response.status_code == 429 else 502
    try:
        payload = response.json()
        raw = payload["candidates"][0]["content"]["parts"][0]["text"]
        decoded = json.loads(raw)
        translations = decoded.get("translations")
        if not isinstance(translations, list):
            raise ValueError("invalid translation list")
        mapped = {}
        returned_ids = []
        for item in translations:
            if (isinstance(item, dict) and isinstance(item.get("id"), str)
                    and isinstance(item.get("translation"), str)):
                returned_ids.append(item["id"])
                if item["id"] in mapped:
                    continue
                mapped[item["id"]] = item["translation"].strip()
        sent_ids = [item["id"] for item in normalized]
        missing = [region_id for region_id in sent_ids if region_id not in mapped]
        duplicates = sorted({region_id for region_id in returned_ids
                             if returned_ids.count(region_id) > 1})
        unknown = sorted(set(returned_ids) - set(sent_ids))
        enriched = []
        for item in normalized:
            recognized = ocr_by_id.get(item["id"], {})
            enriched.append({
                "id": item["id"],
                "translated": mapped.get(item["id"], ""),
                "source_text": item["text"],
                "content_category": item.get("content_category"),
                "ocr_status": recognized.get("cloud_ocr_status", "local"),
            })
        return jsonify(
            translations=enriched,
            missing_ids=missing, duplicate_ids=duplicates, unknown_ids=unknown,
            model="gemini-3.1-flash-lite",
            ocr_model=ocr_metrics.get("model"),
            ocr_metrics=ocr_metrics,
            translation_requests=translation_request_logs,
            api_requests_sent=ocr_metrics.get("requests_sent", 0) + len(translation_request_logs),
            translation_requests_sent=len(translation_request_logs),
            elapsed_seconds=elapsed,
            usage=payload.get("usageMetadata", {}),
        )
    except (KeyError, IndexError, TypeError, ValueError):
        return jsonify(
            error="وصل رد Gemini لكن تنسيقه غير صالح؛ لم تتم إعادة محاولة الترجمة تلقائياً.",
            model="gemini-3.1-flash-lite",
            api_requests_sent=ocr_metrics.get("requests_sent", 0) + len(translation_request_logs),
            translation_requests=translation_request_logs,
            ocr_metrics=ocr_metrics,
            elapsed_seconds=elapsed,
        ), 502


@app.post("/api/translate/region")
def translate_remote_region():
    """Re-translate one reviewed source region without repeating page OCR."""
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify(error="بيانات إعادة الترجمة غير صالحة."), 400
    if str(data.get("reading_layout", "")).strip().lower() != "vertical":
        return jsonify(error=UNSUPPORTED_LAYOUT_MESSAGE), 422
    source = data.get("source", "")
    context = data.get("context", "")
    if not isinstance(source, str) or not isinstance(context, str):
        return jsonify(error="النص والسياق يجب أن يكونا نصين."), 400
    source = " ".join(source.split()).strip()
    context = "\n".join(context.splitlines())[-1800:]
    region_type = data.get("region_type", "speech_bubble")
    if region_type not in {"speech_bubble", "external_text"}:
        region_type = "speech_bubble"
    context = _build_translation_context(region_type, context)
    chapter_title = str(data.get("chapter_title", ""))[:160]
    if not source or len(source) > 4000:
        return jsonify(error="أدخل نصاً إنجليزياً من حرف واحد إلى 4000 حرف."), 400
    started = time.perf_counter()
    try:
        translated = translate_text(
            source, chapter_title, data.get("page_index"), context=context,
            region_index=data.get("region_index"), bubble_id=data.get("bubble_id"),
            work_scope=str(data.get("work_scope", chapter_title))[:200],
            glossary_scope=str(data.get("series_title", ""))[:160],
        )
        if not has_arabic_letters(translated):
            raise RuntimeError("لم ينتج محرك الترجمة نصاً عربياً لهذه المنطقة.")
        return jsonify(
            source=source, translated=translated,
            translation_elapsed_seconds=round(time.perf_counter() - started, 4),
        )
    except (OSError, ValueError, RuntimeError, requests.RequestException) as exc:
        return jsonify(
            error=f"تعذرت إعادة ترجمة المنطقة: {exc}",
            attempt_elapsed_seconds=round(time.perf_counter() - started, 4),
        ), 502


@app.post("/api/translate/render")
def render_remote_translation():
    render_started = time.perf_counter()
    data = request.get_json(silent=True) or {}
    if str(data.get("reading_layout", "")).strip().lower() != "vertical":
        return jsonify(error=UNSUPPORTED_LAYOUT_MESSAGE), 422
    image_url = str(data.get("image", "")).strip()
    referer = str(data.get("referer", "")).strip()
    regions = data.get("regions")
    ref_host = (urlparse(referer).hostname or "").lower()
    if not public_https_url(image_url) or ref_host not in CHAPTER_HOSTS:
        return jsonify(error="رابط الصورة أو مرجع الفصل غير صالح."), 400
    if not isinstance(regions, list) or not regions or len(regions) > 300:
        return jsonify(error="لا توجد مناطق ترجمة صالحة للمعاينة."), 400
    if any(
        not isinstance(region, dict)
        or not has_arabic_letters(str(region.get("translated", "")))
        for region in regions
    ):
        return jsonify(error="كل منطقة مدرجة تحتاج ترجمة عربية؛ استبعد النص الذي تريد إبقاءه كما هو."), 400
    try:
        image_response = safe_get(
            image_url, headers={"Referer": referer}, timeout=30, max_bytes=12 * 1024 * 1024
        )
        content_type = image_response.headers.get("Content-Type", "").split(";")[0].lower()
        if not content_type.startswith("image/"):
            return jsonify(error="رابط الصفحة لم يُرجع صورة صالحة."), 415
        image_hash = hashlib.sha256(image_response.content).hexdigest()
        preview, quality = render_translated_page(image_response.content, regions)
        preview_id = hashlib.sha256(image_hash.encode("ascii") + preview).hexdigest()
        cache_remote_render(preview_id, preview)
        return jsonify(
            preview_url=f"/api/translate/preview/{preview_id}.png",
            rendered_regions=quality["rendered_regions"],
            skipped_regions=quality["skipped_regions"],
            render_elapsed_seconds=round(time.perf_counter() - render_started, 4),
            quality_note="المعاينة تشمل فقاعات الحوار والنصوص الخارجية المكتشفة؛ ألوان النص الخارجي وميله تقديرية. راجع النصوص التي أخطأ OCR في قراءتها قبل اعتماد الصورة.",
            bubble_repair_strategies=quality.get("bubble_repair_strategies", {}),
        )
    except (OSError, ValueError, RuntimeError, requests.RequestException) as exc:
        app.logger.exception("Remote translation preview failed")
        return jsonify(error=f"تعذر إنشاء المعاينة داخل الصورة: {exc}"), 422


@app.get("/api/translate/preview/<preview_id>.png")
def remote_translation_preview(preview_id):
    if not re.fullmatch(r"[0-9a-f]{64}", preview_id):
        return jsonify(error="معرّف المعاينة غير صالح."), 400
    with _REMOTE_RENDER_CACHE_LOCK:
        entry = REMOTE_RENDER_CACHE.get(preview_id)
        if entry is None or not entry[0].is_file():
            return jsonify(error="انتهت صلاحية المعاينة؛ أعد إنشاءها."), 404
        REMOTE_RENDER_CACHE.move_to_end(preview_id)
        return send_file(entry[0], mimetype="image/png", max_age=0)


if __name__ == "__main__":
    app.run(host=resolve_local_bind_host(), port=int(os.getenv("PORT", "5000")), debug=False)
