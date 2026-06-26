"""Opencode (headless) translator — let the local ``opencode`` CLI read the work
directory and write ``transcript_vi.json`` itself, then validate + normalize.

Used by the desktop GUI's "Opencode tự động" translate mode. Runs the user's
installed opencode CLI headlessly pointed at the session folder: opencode reads
``transcript_original.json`` and creates ``transcript_vi.json`` (keeping every
field, adding ``text_vi``). Python then validates all ids are covered and
rewrites the file from the originals + the translations so downstream timing
fields are always intact.

Includes an automatic database-schema fix for the known opencode v1.17.x bug
where ``replacement_seq`` and ``revision`` columns are missing from the SQLite
database, causing ``opencode run`` to fail with ``SQLiteError: no such column``.
"""
import json
import os
import shutil
import sqlite3
import subprocess

from src.utils import setup_logging

logger = setup_logging("translator_opencode")

ORIGINAL_NAME = "transcript_original.json"
VI_NAME = "transcript_vi.json"

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

    Returns True if any columns were added, False if DB was already OK.
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


def translate_via_opencode_cli(
    work_dir,
    source_lang: str,
    *,
    model: str | None = None,
    timeout: int = 600,
) -> None:
    """Translate ``transcript_original.json`` -> ``transcript_vi.json`` via opencode.

    Runs the ``opencode`` CLI headlessly with ``cwd=work_dir`` so opencode reads the
    original transcript and writes the Vietnamese one itself. After the run, validates
    every original id has a non-empty ``text_vi`` and rewrites the file from the
    originals + translations (guaranteeing all timing fields + order). Raises
    ``ValueError`` on any failure so the caller can fall back to manual editing.
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

    exe = _resolve_opencode()
    prompt = _build_prompt(_lang_name(source_lang))

    cmd = [exe, "run", prompt, "--dangerously-skip-permissions"]
    if model:
        cmd += ["--model", model]

    logger.info(
        f"Running opencode translate in {work_dir} "
        f"({len(segments)} segments, {_lang_name(source_lang)} -> Vietnamese)"
    )

    try:
        proc = subprocess.run(
            cmd,
            cwd=work_dir,
            timeout=timeout,
            capture_output=True,
            text=True,
            encoding="utf-8",
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError as e:
        raise ValueError(f"Không chạy được opencode CLI: {e}")
    except subprocess.TimeoutExpired:
        raise ValueError(f"Opencode chạy quá {timeout}s mà chưa xong — thử lại hoặc nhập tay.")

    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-500:]

        # Auto-fix the known DB schema bug and retry once
        if "no such column" in tail and ("replacement_seq" in tail or "revision" in tail):
            logger.warning("Detected opencode DB schema bug — auto-fixing and retrying...")
            if _fix_opencode_db():
                proc = subprocess.run(
                    cmd,
                    cwd=work_dir,
                    timeout=timeout,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    stdin=subprocess.DEVNULL,
                )
                if proc.returncode != 0:
                    tail = (proc.stderr or proc.stdout or "").strip()[-500:]
                    raise ValueError(f"Opencode lỗi sau khi fix DB (mã {proc.returncode}): {tail}")
            else:
                raise ValueError(f"Opencode lỗi DB và không fix được: {tail}")
        else:
            raise ValueError(f"Opencode lỗi (mã {proc.returncode}): {tail}")

    if not os.path.exists(vi_path):
        raise ValueError("Opencode không tạo được transcript_vi.json.")
    with open(vi_path, encoding="utf-8") as f:
        try:
            translated = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"transcript_vi.json không phải JSON hợp lệ: {e}")
    if not isinstance(translated, list):
        raise ValueError("transcript_vi.json phải là một JSON array.")

    # Map id (so sánh dạng chuỗi để tránh lệch int/str) -> text_vi.
    by_id: dict[str, str] = {}
    for item in translated:
        if isinstance(item, dict) and "id" in item:
            by_id[str(item["id"])] = str(item.get("text_vi", "")).strip()

    missing = [s["id"] for s in segments if not by_id.get(str(s["id"]))]
    if missing:
        raise ValueError(
            f"Dịch thiếu {len(missing)} segment (ids: {missing[:10]}...). "
            "Thử lại hoặc dùng trình soạn dịch để bổ sung."
        )

    # Chuẩn hóa: dựng lại từ bản gốc + text_vi để đảm bảo đủ field/đúng thứ tự, kể cả
    # khi opencode lỡ đổi cấu trúc hay bỏ sót field timing.
    normalized = []
    for seg in segments:
        new_seg = dict(seg)
        new_seg["text_vi"] = by_id[str(seg["id"])]
        normalized.append(new_seg)
    with open(vi_path, "w", encoding="utf-8") as f:
        json.dump(normalized, f, ensure_ascii=False, indent=2)

    logger.info(f"Opencode translated {len(normalized)} segments -> {VI_NAME}")
