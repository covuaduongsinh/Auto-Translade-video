"""Vietnamese TTS Synthesizer using LucyLab API.

Flow:
    1. POST ttsLongText → get projectExportId
    2. Poll getExportStatus until state == "completed"
    3. Download audio from returned URL
"""
import os
import time
import requests
from pydub import AudioSegment
import config
from src.utils import setup_logging

logger = setup_logging("synthesizer_vi")

POLL_INTERVAL = 2  # seconds between status checks
POLL_TIMEOUT = int(os.getenv("LUCYLAB_POLL_TIMEOUT", "300"))  # max seconds to wait for TTS completion
MAX_RETRIES = int(os.getenv("LUCYLAB_MAX_RETRIES", "5"))  # retries on transient server errors
RETRY_STATUSES = {429, 500, 502, 503, 504}  # transient — LucyLab CDN/API hiccups


def _request_with_retry(method: str, url: str, **kwargs):
    """HTTP request that retries transient errors (5xx/429/timeouts) with backoff.

    LucyLab's API and download CDN occasionally return 502/503; without retries
    a single hiccup aborts a whole multi-segment dub. Retries up to MAX_RETRIES
    times with exponential backoff before giving up.
    """
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.request(method, url, **kwargs)
            if response.status_code in RETRY_STATUSES:
                raise requests.HTTPError(
                    f"{response.status_code} {response.reason}", response=response
                )
            response.raise_for_status()
            return response
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as exc:
            last_exc = exc
            status = getattr(getattr(exc, "response", None), "status_code", None)
            # Don't retry genuine client errors (4xx except 429) — those won't fix themselves.
            if status is not None and status not in RETRY_STATUSES:
                raise
            if attempt < MAX_RETRIES:
                backoff = min(2 ** (attempt - 1), 10)
                logger.warning(
                    f"Transient error ({status or type(exc).__name__}) on {url[:60]}…; "
                    f"retry {attempt}/{MAX_RETRIES - 1} in {backoff}s"
                )
                time.sleep(backoff)
    raise RuntimeError(f"Request failed after {MAX_RETRIES} attempts: {last_exc}")


def _call_lucylab(method: str, input_data: dict) -> dict:
    """Call LucyLab JSON-RPC API (with retry on transient errors)."""
    response = _request_with_retry(
        "POST",
        config.LUCYLAB_API_URL,
        headers={
            "Authorization": f"Bearer {config.VIETNAMESE_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "method": method,
            "input": input_data,
        },
        timeout=30,
    )
    data = response.json()

    if "error" in data:
        raise RuntimeError(f"LucyLab API error: {data['error']}")

    return data.get("result", {})


def _wait_for_audio(export_id: str) -> str:
    """Poll getExportStatus until completed, return audio URL."""
    start = time.time()

    while time.time() - start < POLL_TIMEOUT:
        result = _call_lucylab("getExportStatus", {"projectExportId": export_id})
        state = result.get("state", "")

        if state == "completed":
            url = result.get("url", "")
            if not url:
                raise RuntimeError("TTS completed but no audio URL returned")
            return url

        if state == "failed":
            raise RuntimeError(f"TTS job failed: {result}")

        time.sleep(POLL_INTERVAL)

    raise TimeoutError(f"TTS polling timed out after {POLL_TIMEOUT}s for export {export_id}")


def _download_audio(url: str, output_path: str) -> str:
    """Download audio file from URL (with retry on transient CDN errors)."""
    response = _request_with_retry("GET", url, timeout=60)

    with open(output_path, "wb") as f:
        f.write(response.content)

    return output_path


def synthesize_segment_vi(
    text_vi: str,
    output_path: str,
    target_duration: float | None = None,
    voice_id: str | None = None,
) -> dict:
    """Synthesize Vietnamese text to audio using LucyLab API.

    Args:
        text_vi: Vietnamese text to speak
        output_path: Where to save the WAV file
        target_duration: Target duration in seconds (for speed adjustment)
        voice_id: LucyLab voice ID (default from config)

    Returns:
        dict with path, actual_duration, speed_adjusted, rate_applied
    """
    if not voice_id:
        raise ValueError("voice_id is required. Use --voice male/female or set VIETNAMESE_VOICEID_MALE/FEMALE in .env")

    # Dispatch to the Vbee backend when selected (env TTS_BACKEND_VI or --tts-backend).
    # Lazy import keeps LucyLab-only runs free of any Vbee dependency.
    if config.TTS_BACKEND_VI == "vbee":
        from src.synthesizer_vbee import synthesize_segment_vbee
        return synthesize_segment_vbee(text_vi, output_path, target_duration, voice_id)

    if not config.VIETNAMESE_API_KEY:
        raise ValueError("VIETNAMESE_API_KEY not set in .env")

    max_speed = config.VIETNAMESE_TTS_MAX_SPEED

    # --- Step 1: Estimate optimal speed based on text length and target duration ---
    # Calibrated from LucyLab male voice: ~19 chars/sec at 1.0x (measured by
    # running 114 chars through TTS at 1.3x → 4.6s output → 19.1 chars/sec at 1.0x).
    # Add a 10% safety headroom so we tolerate slight tail silence without
    # speeding up the audio unnecessarily — users complained the VI voice
    # sounded rushed compared to the original.
    chars_per_sec_normal = 19.0
    safety_headroom = 1.10
    estimated_normal_duration = len(text_vi) / chars_per_sec_normal

    speed = 1.0
    if target_duration and estimated_normal_duration > 0:
        # Only speed up if the natural-paced VI would overflow the target by
        # more than the safety headroom.
        estimated_ratio = estimated_normal_duration / (target_duration * safety_headroom)
        if estimated_ratio > 1.0:
            speed = min(estimated_ratio, max_speed)
            speed = round(speed, 2)

    # --- Step 2: Call TTS API ---
    logger.info(f"TTS request: {len(text_vi)} chars, speed={speed}, target={target_duration:.1f}s"
                if target_duration else f"TTS request: {len(text_vi)} chars, speed={speed}")

    result = _call_lucylab("ttsLongText", {
        "text": text_vi,
        "userVoiceId": voice_id,
        "speed": speed,
    })

    export_id = result.get("projectExportId")
    if not export_id:
        raise RuntimeError(f"No projectExportId in response: {result}")

    logger.info(f"TTS job created: {export_id} (chars={result.get('characterCount', '?')}, "
                f"blocks={result.get('blockCount', '?')})")

    # --- Step 3: Poll for completion and download ---
    audio_url = _wait_for_audio(export_id)
    logger.info(f"TTS completed, downloading audio...")

    # Download to a temp file first (API may return mp3 or wav)
    temp_path = output_path + ".tmp"
    _download_audio(audio_url, temp_path)

    # Convert to WAV for consistency with the rest of the pipeline
    audio = AudioSegment.from_file(temp_path)
    audio.export(output_path, format="wav")
    os.remove(temp_path)

    actual_duration = len(audio) / 1000.0
    speed_adjusted = speed != 1.0

    # --- Step 4: If still too long, we can't re-synthesize easily (API cost),
    # just log a warning for CapCut adjustment ---
    if target_duration and actual_duration > target_duration * 1.1:
        if speed < max_speed:
            # Try once more with higher speed
            new_speed = min(actual_duration / target_duration * speed, max_speed)
            new_speed = round(new_speed, 2)
            logger.info(
                f"Re-adjusting speed: {actual_duration:.1f}s → ~{target_duration:.1f}s "
                f"(speed: {speed} → {new_speed})"
            )

            result2 = _call_lucylab("ttsLongText", {
                "text": text_vi,
                "userVoiceId": voice_id,
                "speed": new_speed,
            })

            export_id2 = result2.get("projectExportId")
            if export_id2:
                audio_url2 = _wait_for_audio(export_id2)
                _download_audio(audio_url2, temp_path)
                audio = AudioSegment.from_file(temp_path)
                audio.export(output_path, format="wav")
                os.remove(temp_path)
                actual_duration = len(audio) / 1000.0
                speed = new_speed
                speed_adjusted = True
        else:
            logger.warning(
                f"Segment too long ({actual_duration:.1f}s vs {target_duration:.1f}s target). "
                f"Already at max speed {max_speed}x — adjust in CapCut."
            )

    return {
        "path": output_path,
        "actual_duration": round(actual_duration, 3),
        "speed_adjusted": speed_adjusted,
        "rate_applied": f"{speed}x",
    }
