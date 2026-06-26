"""AI translator — translate transcript segments to Vietnamese via Google Gemini.

Used by the desktop GUI's "AI auto-translate" mode (and batch mode) to fill the
``text_vi`` field on each transcript segment, replacing the manual translation
step. Mirrors the Gemini client pattern in ``src/content_generator.py``.
"""
import json
import re
import time

from google import genai
from google.genai import types

from src.utils import setup_logging

logger = setup_logging("translator")

# Translate in chunks so a single prompt stays small and the model is less likely
# to drop or truncate segments on long videos.
CHUNK_SIZE = 40


def _lang_name(lang_code: str) -> str:
    table = {
        "en-US": "English",
        "ja-JP": "Japanese",
        "zh-CN": "Chinese (Simplified)",
        "zh-HK": "Chinese (Cantonese)",
        "zh-TW": "Chinese (Traditional)",
    }
    return table.get(lang_code, lang_code)


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _translate_chunk(
    client: "genai.Client",
    chunk: list[dict],
    source_name: str,
    model_id: str,
) -> dict[int, str]:
    """Translate one chunk; return {segment_id: text_vi}."""
    payload = [{"id": s["id"], "text": s.get("text", "")} for s in chunk]
    prompt = (
        f"You are a professional subtitle translator. Translate each segment's "
        f"`text` from {source_name} into natural, fluent Vietnamese suitable for "
        f"voice-over dubbing. Keep the meaning, keep it concise (similar length so "
        f"it fits the same time slot), and do NOT add explanations.\n\n"
        f"Return ONLY a JSON array of objects with keys `id` and `text_vi`, one per "
        f"input segment, preserving the ids. No markdown, no code fences.\n\n"
        f"Input segments:\n{json.dumps(payload, ensure_ascii=False)}"
    )

    # Disable "thinking" on gemini-2.5 models so the whole token budget goes to the
    # translation (otherwise reasoning tokens can eat the budget and return empty).
    gen_config = types.GenerateContentConfig(
        temperature=0.3,
        max_output_tokens=16000,
    )
    if model_id.startswith("gemini-2.5"):
        try:
            gen_config.thinking_config = types.ThinkingConfig(thinking_budget=0)
        except Exception:  # noqa: BLE001 - older SDKs lack ThinkingConfig; ignore
            pass

    response = None
    for attempt in range(5):
        try:
            response = client.models.generate_content(
                model=model_id,
                contents=prompt,
                config=gen_config,
            )
            break
        except Exception as e:  # noqa: BLE001 - retry transient, re-raise otherwise
            error_str = str(e)
            is_transient = (
                "503" in error_str
                or "UNAVAILABLE" in error_str
                or "429" in error_str
                or "ServerError" in type(e).__name__
            )
            if is_transient and attempt < 4:
                wait = (attempt + 1) * 15
                logger.warning(
                    f"Translate API error, retrying in {wait}s "
                    f"(attempt {attempt + 1}/5): {error_str[:80]}"
                )
                time.sleep(wait)
            else:
                raise

    if not response or not response.text:
        raise ValueError("Gemini trả về rỗng (có thể do bộ lọc nội dung hoặc hết token).")
    text = _strip_code_fence(response.text)
    try:
        items = json.loads(text)
    except json.JSONDecodeError:
        # Last-ditch: pull the array out of any surrounding prose.
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if not match:
            raise ValueError(f"Could not parse translation JSON: {text[:200]}")
        items = json.loads(match.group(0))

    return {int(it["id"]): str(it.get("text_vi", "")).strip() for it in items}


def translate_transcript(
    segments: list[dict],
    source_lang: str,
    api_key: str,
    model_id: str,
    target_lang: str = "vi-VN",
) -> list[dict]:
    """Translate every segment to Vietnamese, returning new segments with ``text_vi``.

    ``segments`` are the items from ``transcript_original.json`` (each has id, text,
    start, end, duration). The returned list is a copy with ``text_vi`` added.
    Raises if any segment ends up without a translation.
    """
    if not api_key:
        raise ValueError(
            "GOOGLE_API_KEY chưa được cấu hình — không thể dùng chế độ dịch AI. "
            "Hãy đặt google_api_key trong .env hoặc chọn chế độ dịch khác."
        )

    source_name = _lang_name(source_lang)
    client = genai.Client(api_key=api_key)

    translations: dict[int, str] = {}
    total = len(segments)
    for start in range(0, total, CHUNK_SIZE):
        chunk = segments[start:start + CHUNK_SIZE]
        logger.info(
            f"Translating segments {start + 1}-{start + len(chunk)} of {total} "
            f"({source_name} → Vietnamese)"
        )
        translations.update(_translate_chunk(client, chunk, source_name, model_id))

    result = []
    missing = []
    for seg in segments:
        text_vi = translations.get(seg["id"], "").strip()
        if not text_vi:
            missing.append(seg["id"])
        new_seg = dict(seg)
        new_seg["text_vi"] = text_vi
        result.append(new_seg)

    if missing:
        raise ValueError(
            f"Dịch AI thiếu {len(missing)} segment (ids: {missing[:10]}...). "
            "Thử lại hoặc dùng trình soạn dịch để bổ sung."
        )

    logger.info(f"Translated {total} segments to Vietnamese")
    return result
