"""Claude Code (headless) translator — let the local ``claude`` CLI read the work
directory and write ``transcript_vi.json`` itself, then validate + normalize.

Used by the desktop GUI's "Claude tự động" translate mode as an alternative to the
Gemini API path in ``src/translator.py``. Instead of calling a model API, this runs
the user's installed Claude Code CLI (subscription, no API key) pointed at the
session folder: Claude reads ``transcript_original.json`` and creates
``transcript_vi.json`` (keeping every field, adding ``text_vi``). Python then
validates all ids are covered and rewrites the file from the originals + the
translations so downstream timing fields are always intact.
"""
import json
import os
import shutil
import subprocess

from src.utils import setup_logging

logger = setup_logging("translator_claude")

ORIGINAL_NAME = "transcript_original.json"
VI_NAME = "transcript_vi.json"

# Permission mode for the headless run. ``acceptEdits`` auto-approves file writes so
# the run never blocks for a prompt. Override via env if a stricter/looser mode is
# needed (e.g. ``bypassPermissions``).
_PERMISSION_MODE = os.getenv("CLAUDE_PERMISSION_MODE", "acceptEdits")

# Friendly source-language names for the prompt (mirrors src/translator.py).
_LANG_NAMES = {
    "en-US": "English",
    "ja-JP": "Japanese",
    "zh-CN": "Chinese (Simplified)",
    "zh-HK": "Chinese (Cantonese)",
    "zh-TW": "Chinese (Traditional)",
}


def _lang_name(lang: str) -> str:
    return _LANG_NAMES.get(lang, lang)


def _resolve_claude() -> str:
    """Locate the ``claude`` executable (env override → PATH). Raise if missing."""
    exe = os.getenv("CLAUDE_BIN") or shutil.which("claude")
    if not exe:
        raise ValueError(
            "Không tìm thấy Claude Code CLI (`claude`) trên PATH. Cài Claude Code "
            "hoặc đặt biến môi trường CLAUDE_BIN trỏ tới file thực thi."
        )
    return exe


def _build_prompt(source_name: str) -> str:
    return (
        "You are a professional subtitle/dubbing translator.\n"
        f"1. Read the file `{ORIGINAL_NAME}` in the current directory. It is a JSON "
        "array of segments; each object has keys: id, text, start, end, duration.\n"
        f"2. Translate each segment's `text` from {source_name} into natural, fluent "
        "Vietnamese suitable for voice-over dubbing. Keep the meaning, keep it concise "
        "(similar length so it fits the same time slot). Do NOT add explanations.\n"
        f"3. Write the result to `{VI_NAME}` in the SAME directory: a JSON array where "
        "each object keeps ALL original keys (id, text, start, end, duration) and adds "
        "a new key `text_vi` with the Vietnamese translation. Preserve every id and the "
        "original order.\n"
        "Output only the file — do not print the JSON array in your reply."
    )


def translate_via_claude_cli(
    work_dir,
    source_lang: str,
    *,
    model: str | None = None,
    timeout: int = 600,
) -> None:
    """Translate ``transcript_original.json`` → ``transcript_vi.json`` via Claude Code.

    Runs the ``claude`` CLI headlessly with ``cwd=work_dir`` so Claude reads the
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

    # Drop any stale vi file so a previous result can't be mistaken for success.
    if os.path.exists(vi_path):
        os.remove(vi_path)

    exe = _resolve_claude()
    cmd = [
        exe, "-p", _build_prompt(_lang_name(source_lang)),
        "--permission-mode", _PERMISSION_MODE,
        "--allowedTools", "Read,Write,Glob",
    ]
    if model:
        cmd += ["--model", model]

    logger.info(
        f"Running Claude Code translate in {work_dir} "
        f"({len(segments)} segments, {_lang_name(source_lang)} → Vietnamese)"
    )
    try:
        proc = subprocess.run(
            cmd,
            cwd=work_dir,
            timeout=timeout,
            capture_output=True,
            text=True,
            encoding="utf-8",
            # Under pythonw (no console/stdin) the claude CLI blocks forever
            # waiting on inherited stdin. DEVNULL gives it an immediate EOF.
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError as e:
        raise ValueError(f"Không chạy được Claude Code CLI: {e}")
    except subprocess.TimeoutExpired:
        raise ValueError(f"Claude Code chạy quá {timeout}s mà chưa xong — thử lại hoặc nhập tay.")

    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-300:]
        raise ValueError(f"Claude Code lỗi (mã {proc.returncode}): {tail}")

    if not os.path.exists(vi_path):
        raise ValueError("Claude Code không tạo được transcript_vi.json.")
    with open(vi_path, encoding="utf-8") as f:
        try:
            translated = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"transcript_vi.json không phải JSON hợp lệ: {e}")
    if not isinstance(translated, list):
        raise ValueError("transcript_vi.json phải là một JSON array.")

    # Map id (so sánh dạng chuỗi để tránh lệch int/str) → text_vi.
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
    # khi Claude lỡ đổi cấu trúc hay bỏ sót field timing.
    normalized = []
    for seg in segments:
        new_seg = dict(seg)
        new_seg["text_vi"] = by_id[str(seg["id"])]
        normalized.append(new_seg)
    with open(vi_path, "w", encoding="utf-8") as f:
        json.dump(normalized, f, ensure_ascii=False, indent=2)

    logger.info(f"Claude translated {len(normalized)} segments → {VI_NAME}")
