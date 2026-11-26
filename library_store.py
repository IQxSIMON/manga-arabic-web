"""Small SQLite-backed local chapter library with generated page filenames."""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
import uuid
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit


_resolved_root: tuple[str, Path] | None = None


def _is_writable(directory: Path) -> bool:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def library_root() -> Path:
    global _resolved_root
    configured = os.getenv("MANGA_LIBRARY_DIR") or ""
    if _resolved_root is not None and _resolved_root[0] == configured:
        return _resolved_root[1]
    if configured:
        root = Path(configured)
    else:
        root = Path(__file__).resolve().parent / "instance" / "library"
        if not _is_writable(root):
            # Serverless hosts (Vercel, Lambda) ship a read-only bundle; /tmp is the only writable place.
            root = Path(tempfile.gettempdir()) / "manga-arabic-library"
    root.mkdir(parents=True, exist_ok=True)
    _resolved_root = (configured, root)
    return root


@contextmanager
def _database():
    db = sqlite3.connect(library_root() / "library.sqlite3", timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("""CREATE TABLE IF NOT EXISTS chapters (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        series_type TEXT NOT NULL,
        reading_direction TEXT NOT NULL,
        created_at TEXT NOT NULL,
        series_title TEXT NOT NULL DEFAULT '',
        series_key TEXT NOT NULL DEFAULT '',
        archived_at TEXT,
        last_read_page INTEGER NOT NULL DEFAULT 1,
        source_provider TEXT NOT NULL DEFAULT '',
        source_chapter_url TEXT NOT NULL DEFAULT '',
        source_attributions_json TEXT NOT NULL DEFAULT '[]'
    )""")
    chapter_columns = {row["name"] for row in db.execute("PRAGMA table_info(chapters)")}
    if "series_title" not in chapter_columns:
        db.execute("ALTER TABLE chapters ADD COLUMN series_title TEXT NOT NULL DEFAULT ''")
    if "series_key" not in chapter_columns:
        db.execute("ALTER TABLE chapters ADD COLUMN series_key TEXT NOT NULL DEFAULT ''")
    if "archived_at" not in chapter_columns:
        db.execute("ALTER TABLE chapters ADD COLUMN archived_at TEXT")
    if "last_read_page" not in chapter_columns:
        db.execute("ALTER TABLE chapters ADD COLUMN last_read_page INTEGER NOT NULL DEFAULT 1")
    if "source_provider" not in chapter_columns:
        db.execute("ALTER TABLE chapters ADD COLUMN source_provider TEXT NOT NULL DEFAULT ''")
    if "source_chapter_url" not in chapter_columns:
        db.execute("ALTER TABLE chapters ADD COLUMN source_chapter_url TEXT NOT NULL DEFAULT ''")
    if "source_attributions_json" not in chapter_columns:
        db.execute("ALTER TABLE chapters ADD COLUMN source_attributions_json TEXT NOT NULL DEFAULT '[]'")
    db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_chapters_remote_identity "
        "ON chapters(source_provider, source_chapter_url) "
        "WHERE source_provider <> '' AND source_chapter_url <> ''"
    )
    db.execute("""CREATE TABLE IF NOT EXISTS pages (
        chapter_id TEXT NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
        page_number INTEGER NOT NULL,
        filename TEXT NOT NULL,
        PRIMARY KEY (chapter_id, page_number)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS chapter_jobs (
        chapter_id TEXT PRIMARY KEY REFERENCES chapters(id) ON DELETE CASCADE,
        pause_requested INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS glossary (
        source_key TEXT PRIMARY KEY,
        source_term TEXT NOT NULL,
        target_term TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""")
    glossary_columns = {row["name"] for row in db.execute("PRAGMA table_info(glossary)")}
    if "scope_key" not in glossary_columns:
        db.execute("ALTER TABLE glossary ADD COLUMN scope_key TEXT NOT NULL DEFAULT '*'")
        # Preserve every existing entry as a global term while namespacing new
        # rows by series in the existing primary key.
        db.execute("UPDATE glossary SET source_key = '*|' || source_key")
    db.execute("""CREATE TABLE IF NOT EXISTS translation_memory (
        scope_key TEXT NOT NULL,
        source_key TEXT NOT NULL,
        source_text TEXT NOT NULL,
        target_text TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (scope_key, source_key)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS translation_chunk_cache (
        cache_key TEXT PRIMARY KEY,
        engine_id TEXT NOT NULL,
        translated_text TEXT NOT NULL,
        byte_size INTEGER NOT NULL,
        accessed_at TEXT NOT NULL
    )""")
    # Append-only audit records: human corrections and model output must never
    # overwrite each other even though the reader keeps a current page result.
    db.execute("""CREATE TABLE IF NOT EXISTS translation_events (
        event_id TEXT PRIMARY KEY,
        created_at TEXT NOT NULL,
        event_type TEXT NOT NULL,
        chapter_id TEXT,
        chapter_title TEXT NOT NULL DEFAULT '',
        page_index INTEGER,
        region_index INTEGER,
        bubble_id TEXT,
        source_text TEXT NOT NULL,
        context_text TEXT NOT NULL DEFAULT '',
        target_text TEXT NOT NULL,
        engine_id TEXT NOT NULL,
        model_name TEXT,
        model_revision TEXT,
        quantization TEXT,
        prompt_version TEXT,
        review_status TEXT NOT NULL DEFAULT 'unreviewed',
        quality_rating INTEGER,
        source_license TEXT,
        training_eligible INTEGER NOT NULL DEFAULT 0,
        metadata_json TEXT NOT NULL DEFAULT '{}'
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_translation_events_lookup "
               "ON translation_events(chapter_id, page_index, region_index, created_at)")
    db.execute("""CREATE TABLE IF NOT EXISTS source_blocks (
        provider_id TEXT NOT NULL,
        source_id TEXT NOT NULL,
        source_name TEXT NOT NULL,
        reason TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        PRIMARY KEY (provider_id, source_id)
    )""")
    db.commit()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def create_chapter(title: str, series_type: str, reading_direction: str,
                   pages: list[tuple[str, bytes]], series_title: str = "",
                   series_key: str = "", source_provider: str = "",
                   source_chapter_url: str = "",
                   source_attributions: list[dict] | None = None) -> dict:
    source_provider = str(source_provider or "").strip().lower()
    source_chapter_url = _normalize_source_chapter_url(source_chapter_url) if source_chapter_url else ""
    if source_provider and not re.fullmatch(r"[a-z0-9_-]{1,40}", source_provider):
        raise ValueError("معرّف مصدر الفصل غير صالح.")
    if bool(source_provider) != bool(source_chapter_url):
        raise ValueError("يجب حفظ مصدر الفصل ورابطه معاً.")
    attributions_json = json.dumps(
        _normalize_source_attributions(source_attributions), ensure_ascii=False,
        separators=(",", ":"),
    )
    chapter_id = uuid.uuid4().hex
    created_at = datetime.now(timezone.utc).isoformat()
    directory = library_root() / chapter_id
    directory.mkdir()
    try:
        with _database() as db:
            db.execute(
                """INSERT INTO chapters
                   (id, title, series_type, reading_direction, created_at, series_title, series_key,
                    source_provider, source_chapter_url, source_attributions_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (chapter_id, title, series_type, reading_direction, created_at,
                 (series_title or title).strip(), (series_key or series_title or title).strip(),
                 source_provider, source_chapter_url, attributions_json),
            )
            for index, (extension, content) in enumerate(pages, 1):
                filename = f"page-{index:04d}.{extension}"
                (directory / filename).write_bytes(content)
                db.execute(
                    "INSERT INTO pages VALUES (?, ?, ?)",
                    (chapter_id, index, filename),
                )
        return get_chapter(chapter_id)
    except Exception:
        # This folder was created for this failed import and cannot contain prior user data.
        for path in directory.glob("page-*.*"):
            path.unlink(missing_ok=True)
        directory.rmdir()
        raise


def update_chapter_series(chapter_id: str, series_title: str) -> dict | None:
    series_title = " ".join(str(series_title).split()).strip()
    if not series_title or len(series_title) > 160:
        raise ValueError("عنوان السلسلة يجب أن يتراوح بين حرف واحد و160 حرفاً.")
    with _database() as db:
        current = db.execute(
            "SELECT series_key FROM chapters WHERE id = ?", (chapter_id,)
        ).fetchone()
        if current is None:
            return None
        existing_key = str(current["series_key"] or "")
        # A display-title edit must not detach a stable provider identity.
        series_key = existing_key if existing_key and not existing_key.startswith("local:") else f"local:{series_title.casefold()}"
        db.execute(
            "UPDATE chapters SET series_title = ?, series_key = ? WHERE id = ?",
            (series_title, series_key, chapter_id),
        )
    return get_chapter(chapter_id)


def update_chapter_title(chapter_id: str, title: str) -> dict | None:
    title = " ".join(str(title).split()).strip()
    if not title or len(title) > 160:
        raise ValueError("عنوان الفصل يجب أن يتراوح بين حرف واحد و160 حرفاً.")
    with _database() as db:
        cursor = db.execute("UPDATE chapters SET title = ? WHERE id = ?", (title, chapter_id))
        if cursor.rowcount == 0:
            return None
    return get_chapter(chapter_id)


def set_chapter_paused(chapter_id: str, paused: bool) -> bool:
    with _database() as db:
        exists = db.execute("SELECT 1 FROM chapters WHERE id = ?", (chapter_id,)).fetchone()
        if exists is None:
            return False
        db.execute(
            """INSERT INTO chapter_jobs (chapter_id, pause_requested, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(chapter_id) DO UPDATE SET
                 pause_requested = excluded.pause_requested,
                 updated_at = excluded.updated_at""",
            (chapter_id, int(bool(paused)), datetime.now(timezone.utc).isoformat()),
        )
    return True


def is_chapter_paused(chapter_id: str) -> bool:
    with _database() as db:
        row = db.execute(
            "SELECT pause_requested FROM chapter_jobs WHERE chapter_id = ?", (chapter_id,)
        ).fetchone()
    return bool(row and row["pause_requested"])


def list_chapters(archived: bool = False) -> list[dict]:
    archive_filter = "IS NOT NULL" if archived else "IS NULL"
    with _database() as db:
        rows = db.execute("""
            SELECT c.id, c.title, c.series_type, c.reading_direction, c.created_at,
                   c.series_title, c.series_key, c.last_read_page,
                   c.source_provider, c.source_chapter_url, c.source_attributions_json,
                   COUNT(p.page_number) AS page_count
            FROM chapters c LEFT JOIN pages p ON p.chapter_id = c.id
            WHERE c.archived_at """ + archive_filter + """
            GROUP BY c.id ORDER BY c.created_at DESC
        """).fetchall()
    return [_decode_chapter_attributions(dict(row)) for row in rows]


def set_chapter_archived(chapter_id: str, archived: bool) -> dict | None:
    """Hide a chapter from the active library without deleting any user data."""
    archived_at = datetime.now(timezone.utc).isoformat() if archived else None
    with _database() as db:
        cursor = db.execute(
            "UPDATE chapters SET archived_at = ? WHERE id = ?",
            (archived_at, chapter_id),
        )
        if cursor.rowcount == 0:
            return None
    return get_chapter(chapter_id)


def update_reading_position(chapter_id: str, page_number: int) -> bool:
    """Persist a one-based page position only when it belongs to the chapter."""
    with _database() as db:
        page_exists = db.execute(
            "SELECT 1 FROM pages WHERE chapter_id = ? AND page_number = ?",
            (chapter_id, page_number),
        ).fetchone()
        if page_exists is None:
            return False
        db.execute(
            "UPDATE chapters SET last_read_page = ? WHERE id = ?",
            (page_number, chapter_id),
        )
    return True


def get_chapter(chapter_id: str) -> dict | None:
    with _database() as db:
        chapter = db.execute("SELECT * FROM chapters WHERE id = ?", (chapter_id,)).fetchone()
        if chapter is None:
            return None
        pages = db.execute(
            "SELECT page_number, filename FROM pages WHERE chapter_id = ? ORDER BY page_number",
            (chapter_id,),
        ).fetchall()
    result = dict(chapter)
    result = _decode_chapter_attributions(result)
    result["pages"] = [dict(page) for page in pages]
    result["page_count"] = len(pages)
    return result


def _decode_chapter_attributions(chapter: dict) -> dict:
    raw = chapter.pop("source_attributions_json", "[]")
    try:
        values = json.loads(raw) if isinstance(raw, str) else []
    except (TypeError, ValueError):
        values = []
    chapter["source_attributions"] = values if isinstance(values, list) else []
    return chapter


def _normalize_source_attributions(values: list[dict] | None) -> list[dict]:
    if not isinstance(values, list):
        return []
    normalized = []
    for entry in values[:30]:
        if not isinstance(entry, dict):
            continue
        name = " ".join(str(entry.get("name", "")).split()).strip()[:160]
        if not name:
            continue
        item = {"name": name}
        url = entry.get("url")
        if isinstance(url, str) and len(url) <= 2048:
            try:
                parsed = urlsplit(url.strip())
                if (parsed.scheme.lower() == "https" and parsed.hostname
                        and not parsed.username and not parsed.password
                        and parsed.port in (None, 443)):
                    item["url"] = url.strip()
            except ValueError:
                pass
        normalized.append(item)
    return normalized


def _normalize_source_chapter_url(url: str) -> str:
    """Persist a canonical HTTPS chapter URL without query tracking parameters."""
    if not isinstance(url, str) or len(url) > 2048:
        raise ValueError("رابط الفصل المصدر غير صالح.")
    try:
        parsed = urlsplit(url.strip())
        if (parsed.scheme.lower() != "https" or not parsed.hostname
                or parsed.username or parsed.password
                or parsed.port not in (None, 443)):
            raise ValueError("رابط الفصل المصدر يجب أن يكون HTTPS آمناً.")
    except ValueError as exc:
        raise ValueError("رابط الفصل المصدر يجب أن يكون HTTPS آمناً.") from exc
    path = parsed.path.rstrip("/") or "/"
    return f"https://{parsed.hostname.lower()}{path}"


def get_chapter_by_source(source_provider: str, source_chapter_url: str) -> dict | None:
    source_provider = str(source_provider or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9_-]{1,40}", source_provider):
        raise ValueError("معرّف مصدر الفصل غير صالح.")
    normalized_url = _normalize_source_chapter_url(source_chapter_url)
    with _database() as db:
        row = db.execute(
            "SELECT id FROM chapters WHERE source_provider = ? AND source_chapter_url = ?",
            (source_provider, normalized_url),
        ).fetchone()
    return get_chapter(row["id"]) if row else None


def create_library_backup() -> Path:
    """Create a consistent downloadable archive of the local library and state."""
    root = library_root().resolve()
    # Ensure an initialized schema before taking SQLite's transaction-safe snapshot.
    with _database() as db:
        db.execute("SELECT 1").fetchone()

    temporary_root = Path(tempfile.mkdtemp(prefix="manga-arabic-library-backup-"))
    snapshot_path = temporary_root / "library.sqlite3"
    archive_path = temporary_root / "manga-arabic-library-backup.zip"
    try:
        source_db = sqlite3.connect(root / "library.sqlite3")
        snapshot_db = sqlite3.connect(snapshot_path)
        try:
            source_db.backup(snapshot_db)
        finally:
            snapshot_db.close()
            source_db.close()
        snapshot_db = sqlite3.connect(snapshot_path)
        try:
            chapter_ids = [row[0] for row in snapshot_db.execute("SELECT id FROM chapters")]
        finally:
            snapshot_db.close()

        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=3) as archive:
            archive.write(snapshot_path, "library.sqlite3")
            archive.writestr(
                "RESTORE.txt",
                "Manga Arabic Translator local library backup.\n"
                "Contains the SQLite library database and chapter images, OCR/translation results, and rendered pages.\n"
                "Model weights and llama.cpp binaries are not included.\n"
                "To restore safely: stop the app, keep a separate copy of the current library, then extract this archive into the configured MANGA_LIBRARY_DIR (or instance/library).\n",
            )
            for chapter_id in chapter_ids:
                if not isinstance(chapter_id, str) or not re.fullmatch(r"[0-9a-f]{32}", chapter_id, re.IGNORECASE):
                    continue
                chapter_dir = (root / chapter_id).resolve()
                if chapter_dir.parent != root or not chapter_dir.is_dir():
                    continue
                for path in chapter_dir.rglob("*"):
                    if path.is_symlink() or not path.is_file() or path.name.endswith(".tmp"):
                        continue
                    resolved = path.resolve()
                    if chapter_dir not in resolved.parents:
                        continue
                    archive.write(resolved, (Path(chapter_id) / path.relative_to(chapter_dir)).as_posix())
        return archive_path
    except Exception:
        shutil.rmtree(temporary_root, ignore_errors=True)
        raise


def cleanup_library_backup(archive_path: str | Path) -> None:
    """Remove only a temporary archive created by create_library_backup."""
    path = Path(archive_path)
    if path.name != "manga-arabic-library-backup.zip" or not path.parent.name.startswith("manga-arabic-library-backup-"):
        return
    shutil.rmtree(path.parent, ignore_errors=True)


def get_page_path(chapter_id: str, page_number: int, chapter: dict | None = None) -> Path | None:
    """Resolve a saved image path without reading its potentially large bytes."""
    if chapter is None:
        with _database() as db:
            row = db.execute(
                "SELECT filename FROM pages WHERE chapter_id = ? AND page_number = ?",
                (chapter_id, page_number),
            ).fetchone()
        page = dict(row) if row else None
    else:
        page = next((item for item in chapter.get("pages", [])
                     if int(item.get("page_number", -1)) == int(page_number)), None)
    if page is None:
        return None
    path = library_root() / chapter_id / page["filename"]
    return path if path.is_file() else None


def read_page(chapter_id: str, page_number: int) -> tuple[Path, bytes] | None:
    path = get_page_path(chapter_id, page_number)
    if path is None:
        return None
    return path, path.read_bytes()


def save_page_result(chapter_id: str, page_number: int, result: dict) -> None:
    directory = library_root() / chapter_id / "results"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"page-{page_number:04d}.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    _replace_with_retry(temporary, path)


def read_page_result(chapter_id: str, page_number: int) -> dict | None:
    path = library_root() / chapter_id / "results" / f"page-{page_number:04d}.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_rendered_page(chapter_id: str, page_number: int, image_bytes: bytes) -> Path:
    directory = library_root() / chapter_id / "rendered"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"page-{page_number:04d}.png"
    temporary = path.with_suffix(".png.tmp")
    temporary.write_bytes(image_bytes)
    _replace_with_retry(temporary, path)
    return path


def _replace_with_retry(source: Path, destination: Path) -> None:
    """Retry atomic replacement briefly when Windows scanners hold a file handle."""
    delays = (0.05, 0.1, 0.2, 0.4, 0.8, 1.0)
    for attempt, delay in enumerate(delays):
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if attempt == len(delays) - 1:
                raise
            time.sleep(delay)


def read_rendered_page(chapter_id: str, page_number: int) -> Path | None:
    result = read_page_result(chapter_id, page_number)
    if result and result.get("render_stale"):
        return None
    path = library_root() / chapter_id / "rendered" / f"page-{page_number:04d}.png"
    return path if path.is_file() else None


def normalize_term(text: str) -> str:
    return " ".join(text.casefold().split())


def normalize_glossary_scope(scope_key: str | None) -> str:
    scope = " ".join(str(scope_key or "*").casefold().split()).strip()
    return scope[:180] or "*"


def glossary_scope_aliases(scope_key: str | None) -> list[str]:
    scope = normalize_glossary_scope(scope_key)
    aliases = [scope]
    if ":" in scope:
        suffix = scope.split(":", 1)[1].strip()
        if suffix and suffix not in aliases:
            aliases.append(suffix)
    return aliases


def list_glossary(scope_key: str | None = None) -> list[dict]:
    with _database() as db:
        if scope_key is None:
            rows = db.execute(
                "SELECT source_term, target_term, scope_key, updated_at FROM glossary ORDER BY scope_key, source_term COLLATE NOCASE"
            ).fetchall()
        else:
            scopes = glossary_scope_aliases(scope_key)
            placeholders = ", ".join("?" for _ in scopes)
            rows = db.execute(
                "SELECT source_term, target_term, scope_key, updated_at FROM glossary "
                f"WHERE scope_key = '*' OR scope_key IN ({placeholders}) "
                "ORDER BY source_term COLLATE NOCASE, scope_key",
                scopes,
            ).fetchall()
    return [dict(row) for row in rows]


def save_glossary_term(source_term: str, target_term: str, scope_key: str | None = None) -> None:
    scope = normalize_glossary_scope(scope_key)
    key = f"{scope}|{normalize_term(source_term)}"
    with _database() as db:
        db.execute("""
            INSERT INTO glossary (source_key, source_term, target_term, updated_at, scope_key)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(source_key) DO UPDATE SET
                source_term=excluded.source_term,
                target_term=excluded.target_term,
                updated_at=excluded.updated_at,
                scope_key=excluded.scope_key
        """, (key, source_term.strip(), target_term.strip(), datetime.now(timezone.utc).isoformat(), scope))


def import_glossary_terms(terms: list[dict]) -> int:
    """Upsert a previously validated glossary batch in one SQLite transaction."""
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for term in terms:
        source = term["source_term"].strip()
        target = term["target_term"].strip()
        scope = normalize_glossary_scope(term.get("scope_key"))
        rows.append((f"{scope}|{normalize_term(source)}", source, target, now, scope))
    with _database() as db:
        db.executemany("""
            INSERT INTO glossary (source_key, source_term, target_term, updated_at, scope_key)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(source_key) DO UPDATE SET
                source_term=excluded.source_term,
                target_term=excluded.target_term,
                updated_at=excluded.updated_at,
                scope_key=excluded.scope_key
        """, rows)
    return len(rows)


def get_glossary_exact(source_text: str, scope_key: str | None = None) -> str | None:
    normalized = normalize_term(source_text)
    scopes = glossary_scope_aliases(scope_key)
    keys = [f"{scope}|{normalized}" for scope in scopes] + [f"*|{normalized}"]
    placeholders = ", ".join("?" for _ in keys)
    with _database() as db:
        row = db.execute(
            f"SELECT target_term FROM glossary WHERE source_key IN ({placeholders}) "
            "ORDER BY CASE " + " ".join(
                f"WHEN scope_key = ? THEN {index}" for index, scope in enumerate(scopes)
            ) + f" ELSE {len(scopes)} END LIMIT 1",
            (*keys, *scopes),
        ).fetchone()
    return row["target_term"] if row else None


def get_memory(scope: str, source_text: str) -> str | None:
    with _database() as db:
        row = db.execute(
            "SELECT target_text FROM translation_memory WHERE scope_key = ? AND source_key = ?",
            (normalize_term(scope) or "_default", normalize_term(source_text)),
        ).fetchone()
    return row["target_text"] if row else None


def save_memory(scope: str, source_text: str, target_text: str) -> None:
    with _database() as db:
        db.execute("""
            INSERT INTO translation_memory (scope_key, source_key, source_text, target_text, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(scope_key, source_key) DO UPDATE SET
                source_text=excluded.source_text,
                target_text=excluded.target_text,
                updated_at=excluded.updated_at
        """, (
            normalize_term(scope) or "_default", normalize_term(source_text),
            source_text.strip(), target_text.strip(), datetime.now(timezone.utc).isoformat(),
        ))


def get_translation_chunk(engine_id: str, cache_key: str) -> str | None:
    """Read an automatic model chunk and update its persistent LRU timestamp."""
    now = datetime.now(timezone.utc).isoformat()
    with _database() as db:
        row = db.execute(
            "SELECT translated_text FROM translation_chunk_cache "
            "WHERE cache_key = ? AND engine_id = ?",
            (cache_key, engine_id),
        ).fetchone()
        if row is not None:
            db.execute(
                "UPDATE translation_chunk_cache SET accessed_at = ? WHERE cache_key = ?",
                (now, cache_key),
            )
    return row["translated_text"] if row else None


def save_translation_chunk(engine_id: str, cache_key: str, translated_text: str,
                           max_bytes: int = 8 * 1024 * 1024,
                           max_entries: int = 512) -> None:
    """Persist generated chunks in a bounded cache separate from user edits."""
    text = str(translated_text).strip()
    byte_size = len(text.encode("utf-8"))
    if not text or byte_size > max_bytes:
        return
    now = datetime.now(timezone.utc).isoformat()
    with _database() as db:
        db.execute(
            """INSERT INTO translation_chunk_cache
               (cache_key, engine_id, translated_text, byte_size, accessed_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(cache_key) DO UPDATE SET
                   engine_id = excluded.engine_id,
                   translated_text = excluded.translated_text,
                   byte_size = excluded.byte_size,
                   accessed_at = excluded.accessed_at""",
            (cache_key, engine_id, text, byte_size, now),
        )
        total_bytes, total_entries = db.execute(
            "SELECT COALESCE(SUM(byte_size), 0), COUNT(*) FROM translation_chunk_cache"
        ).fetchone()
        while total_bytes > max_bytes or total_entries > max_entries:
            row = db.execute(
                "SELECT cache_key, byte_size FROM translation_chunk_cache "
                "ORDER BY accessed_at ASC LIMIT 1"
            ).fetchone()
            if row is None:
                break
            db.execute("DELETE FROM translation_chunk_cache WHERE cache_key = ?", (row["cache_key"],))
            total_bytes -= row["byte_size"]
            total_entries -= 1


def record_translation_event(
    *, event_type: str, source_text: str, target_text: str, engine_id: str,
    chapter_id: str | None = None, chapter_title: str = "", page_index: int | None = None,
    region_index: int | None = None, bubble_id: str | int | None = None, context_text: str = "",
    model_name: str | None = None, model_revision: str | None = None,
    quantization: str | None = None, prompt_version: str | None = None,
    review_status: str = "unreviewed", quality_rating: int | None = None,
    source_license: str | None = None, training_eligible: bool = False,
    metadata: dict | None = None,
) -> str:
    """Append a translation, engine draft, or human correction without replacement."""
    if not event_type.strip() or not engine_id.strip():
        raise ValueError("event_type and engine_id are required")
    if not isinstance(source_text, str) or not isinstance(target_text, str):
        raise TypeError("source_text and target_text must be strings")
    if quality_rating is not None and not 1 <= quality_rating <= 5:
        raise ValueError("quality_rating must be between 1 and 5")
    event_id = uuid.uuid4().hex
    with _database() as db:
        db.execute("""INSERT INTO translation_events (
            event_id, created_at, event_type, chapter_id, chapter_title, page_index,
            region_index, bubble_id, source_text, context_text, target_text, engine_id,
            model_name, model_revision, quantization, prompt_version, review_status,
            quality_rating, source_license, training_eligible, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
            event_id, datetime.now(timezone.utc).isoformat(), event_type.strip(), chapter_id,
            chapter_title.strip(), page_index, region_index,
            str(bubble_id) if bubble_id is not None else None, source_text, context_text,
            target_text, engine_id.strip(), model_name, model_revision, quantization,
            prompt_version, review_status, quality_rating, source_license,
            int(bool(training_eligible)), json.dumps(metadata or {}, ensure_ascii=False),
        ))
    return event_id


def list_translation_events(
    *, chapter_id: str | None = None, page_index: int | None = None, limit: int = 500,
) -> list[dict]:
    """Return newest audit events first, with a bounded page size."""
    if not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")
    limit = min(limit, 2000)
    clauses, values = [], []
    if chapter_id:
        clauses.append("chapter_id = ?"); values.append(chapter_id)
    if page_index is not None:
        clauses.append("page_index = ?"); values.append(page_index)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with _database() as db:
        rows = db.execute(
            "SELECT * FROM translation_events" + where +
            " ORDER BY created_at DESC, event_id DESC LIMIT ?", (*values, limit),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["training_eligible"] = bool(item["training_eligible"])
        try:
            item["metadata"] = json.loads(item.pop("metadata_json"))
        except (TypeError, json.JSONDecodeError):
            item["metadata"] = {}
        result.append(item)
    return result


def list_source_blocks(provider_id: str = "mangadex") -> list[dict]:
    with _database() as db:
        rows = db.execute(
            "SELECT source_id, source_name, reason, created_at FROM source_blocks WHERE provider_id = ? ORDER BY source_name COLLATE NOCASE",
            (provider_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def save_source_block(provider_id: str, source_id: str, source_name: str, reason: str = "") -> None:
    with _database() as db:
        db.execute("""
            INSERT INTO source_blocks (provider_id, source_id, source_name, reason, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(provider_id, source_id) DO UPDATE SET
                source_name=excluded.source_name, reason=excluded.reason
        """, (provider_id, source_id, source_name.strip()[:160], reason.strip()[:300], datetime.now(timezone.utc).isoformat()))


def delete_source_block(provider_id: str, source_id: str) -> bool:
    """Remove a source exclusion and report whether one existed."""
    with _database() as db:
        cursor = db.execute(
            "DELETE FROM source_blocks WHERE provider_id = ? AND source_id = ?",
            (provider_id, source_id),
        )
    return cursor.rowcount > 0


def is_source_blocked(provider_id: str, source_id: str) -> bool:
    with _database() as db:
        return db.execute(
            "SELECT 1 FROM source_blocks WHERE provider_id = ? AND source_id = ?",
            (provider_id, source_id),
        ).fetchone() is not None
