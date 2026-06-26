"""Opencode (headless) translator — translates transcript segments via the local
``opencode`` CLI in chunks to avoid upstream idle timeouts with slower free models.

Used by the desktop GUI's "Opencode tự động" translate mode. opencode reads
``transcript_original.json`` and creates ``transcript_vi.json``. Python validates
all ids are covered and rewrites the final file from the originals + the
translations so downstream timing fields are always intact.

Includes an automatic database-schema fix for the known opencode v1.17.x bug
where ``replacement_seq`` and ``revision`` columns are missing from the SQLite
database, causing ``opencode run`` to fail with ``SQLiteError: no such column``.
"""
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import time

from src.utils import setup_logging

logger = setup_logging("translator_opencode")

ORIGINAL_NAME = "transcript_original.json"
VI_NAME = "transcript_vi.json"

# Number of segments per opencode chunk. Larger = fewer API calls but higher
# chance of "Upstream idle timeout exceeded" on slow free models.
CHUNK_SIZE = 10

# Retry config for transient upstream timeouts.
MAX_RETRIES = 2
RETRY_DELAY_SECONDS = 3

_LANG_NAMES = {
    "en-US": "English",
    "ja-JP": "Japanese",
    "zh-CN": "Chinese (Simplified)",
    "zh-HK": "Chinese (Cantonese)",
    "zh-TW": "Chinese (Traditional)",
}


def _lang_name(lang: str) -> str:
    return _LANG_NAMES.get(lang, lang)


def _resolve_opencode() -> str:
    """Locate the ``opencode`` executable (env override -> PATH). Raise if missing."""
    exe = os.getenv("OPENCODE_BIN") or shutil.which("opencode")
    if not exe:
        raise ValueError(
            "Không tìm thấy opencode CLI (`opencode`) trên PATH. "
            "Cài opencode hoặc đặt biến môi trường OPENCODE_BIN trỏ tới file thực thi."
        )
    return exe


def _fix_opencode_db() -> bool:
    """Auto-fix the known opencode DB schema bug (missing columns).

    opencode v1.17.x bundles app code that references ``replacement_seq`` on
    every table and ``revision`` on ``session_context_epoch``, but the bundled
    migration system fails to add these columns. This function finds the
    opencode database and adds the missing columns directly via sqlite3.
    """
    db_path = os.path.expanduser(r"~\.local\share\opencode\opencode.db")
    if not os.path.exists(db_path):
        return False

    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()

        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cur.fetchall()]

        added = 0
        for t in tables:
            if t == "sqlite_sequence":
                continue
            cur.execute(f"PRAGMA table_info({t})")
            cols = [c[1] for c in cur.fetchall()]
            if "replacement_seq" not in cols:
                cur.execute(f"ALTER TABLE {t} ADD COLUMN replacement_seq TEXT")
                added += 1
            if t == "session_context_epoch" and "revision" not in cols:
                cur.execute(
                    "ALTER TABLE session_context_epoch ADD COLUMN revision INTEGER"
                )
                added += 1

        conn.commit()
        conn.close()

        if added:
            logger.info(f"Auto-fixed opencode DB: added {added} missing columns")
        return added > 0
    except Exception as e:
        logger.warning(f"Could not auto-fix opencode DB: {e}")
        return False


def _build_prompt(source_name: str) -> str:
    return (
        f"Read the file {ORIGINAL_NAME} in the current directory. "
        f"It is a JSON array of objects with keys: id, text, start, end, duration. "
        f"Translate each text from {source_name} to Vietnamese. "
        f"Write the result to {VI_NAME} in the same directory: a JSON array "
        f"keeping all original keys and adding text_vi with the translation. "
        f"Preserve every id and the original order."
    )


def _run_opencode_once(
    work_dir: str,
    source_name: str,
    model: str | None,
    timeout: int,
) -> subprocess.CompletedProcess:
    """Run opencode once and return the CompletedProcess."""
    exe = _resolve_opencode()
    prompt = _build_prompt(source_name)
    cmd = [exe, "run", prompt, "--dangerously-skip-permissions"]
    if model:
        cmd += ["--model", model]

    return subprocess.run(
        cmd,
        cwd=work_dir,
        timeout=timeout,
        capture_output=True,
        text=True,
        encoding="utf-8",
        stdin=subprocess.DEVNULL,
    )


def _is_transient_timeout(tail: str) -> bool:
    return "timeout" in tail.lower() or "upstream idle" in tail.lower()


def _translate_chunk(
    chunk: list[dict],
    source_name: str,
    model: str | None,
    timeout: int,
) -> dict[int, str]:
    """Translate one chunk of segments via opencode CLI.

    Runs in a temporary directory with only this chunk's transcript to keep the
    prompt small and avoid upstream idle timeouts. Returns {id: text_vi}.
    """
    with tempfile.TemporaryDirectory(prefix="opencode_translate_") as tmpdir:
        orig_path = os.path.join(tmpdir, ORIGINAL_NAME)
        vi_path = os.path.join(tmpdir, VI_NAME)

        with open(orig_path, "w", encoding="utf-8") as f:
            json.dump(chunk, f, ensure_ascii=False, indent=2)

        last_tail = ""
        for attempt in range(1, MAX_RETRIES + 1):
            logger.info(
                f"  Chunk {chunk[0]['id']}-{chunk[-1]['id']} "
                f"(attempt {attempt}/{MAX_RETRIES})"
            )
            proc = _run_opencode_once(tmpdir, source_name, model, timeout)

            if proc.returncode == 0 and os.path.exists(vi_path):
                with open(vi_path, encoding="utf-8") as f:
                    translated = json.load(f)
                if isinstance(translated, list):
                    return {
                        int(it["id"]): str(it.get("text_vi", "")).strip()
                        for it in translated
                        if isinstance(it, dict) and "id" in it
                    }

            tail = (proc.stderr or proc.stdout or "").strip()[-500:]
            last_tail = tail

            # Auto-fix DB if needed and retry immediately
            if "no such column" in tail and (
                "replacement_seq" in tail or "revision" in tail
            ):
                logger.warning("Detected opencode DB schema bug — auto-fixing...")
                if _fix_opencode_db():
                    continue

            if _is_transient_timeout(tail) and attempt < MAX_RETRIES:
                logger.warning(
                    f"Opencode timeout, retrying in {RETRY_DELAY_SECONDS}s..."
                )
                time.sleep(RETRY_DELAY_SECONDS)
                continue

            break

        raise ValueError(f"Opencode lỗi khi dịch chunk: {last_tail}")


def translate_via_opencode_cli(
    work_dir,
    source_lang: str,
    *,
    model: str | None = None,
    timeout: int = 600,
) -> None:
    """Translate ``transcript_original.json`` -> ``transcript_vi.json`` via opencode.

    Splits long transcripts into chunks of ``CHUNK_SIZE`` segments to avoid
    upstream idle timeouts with free models. Each chunk is retried on transient
    timeouts. After all chunks finish, combines the results into a single
    ``transcript_vi.json`` with all original keys preserved.
    """
    work_dir = os.fspath(work_dir)
    orig_path = os.path.join(work_dir, ORIGINAL_NAME)
    vi_path = os.path.join(work_dir, VI_NAME)

    if not os.path.exists(orig_path):
        raise ValueError(f"Không thấy {ORIGINAL_NAME} trong {work_dir}.")
    with open(orig_path, encoding="utf-8") as f:
        segments = json.load(f)
    if not segments:
        raise ValueError(f"{ORIGINAL_NAME} rỗng — không có gì để dịch.")

    if os.path.exists(vi_path):
        os.remove(vi_path)

    source_name = _lang_name(source_lang)
    logger.info(
        f"Running opencode translate in {work_dir} "
        f"({len(segments)} segments, {source_name} -> Vietnamese, model={model})"
    )

    # Split into chunks and translate each one
    translations: dict[int, str] = {}
    for i in range(0, len(segments), CHUNK_SIZE):
        chunk = segments[i : i + CHUNK_SIZE]
        chunk_translations = _translate_chunk(chunk, source_name, model, timeout)
        translations.update(chunk_translations)

    # Validate all ids are translated
    missing = [s["id"] for s in segments if s["id"] not in translations]
    if missing:
        raise ValueError(
            f"Dịch thiếu {len(missing)} segment (ids: {missing[:10]}...). "
            "Thử lại hoặc dùng trình soạn dịch để bổ sung."
        )

    # Build normalized output from originals + translations
    normalized = []
    for seg in segments:
        new_seg = dict(seg)
        new_seg["text_vi"] = translations[seg["id"]]
        normalized.append(new_seg)

    with open(vi_path, "w", encoding="utf-8") as f:
        json.dump(normalized, f, ensure_ascii=False, indent=2)

    logger.info(f"Opencode translated {len(normalized)} segments -> {VI_NAME}")
