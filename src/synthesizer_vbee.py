"""Vietnamese TTS Synthesizer using Vbee AIVoice API.

Drop-in alternative to synthesizer_vi (LucyLab). Selected when
config.TTS_BACKEND_VI == "vbee". Same signature and return shape as
synthesize_segment_vi so the pipeline doesn't care which backend is active.

Flow (Vbee REST v1):
    1. POST {VBEE_API_URL}            → result.request_id
    2. GET  {VBEE_API_URL}/{id}       → poll until result.status == "SUCCESS"
    3. Download result.audio_link (mp3) and convert to WAV
"""
import os
import time
import requests
from pydub import AudioSegment
import config
from src.utils import setup_logging
# Reuse the transient-error retry wrapper and poll timing from the LucyLab backend.
from src.synthesizer_vi import _request_with_retry, POLL_INTERVAL, POLL_TIMEOUT

logger = setup_logging("synthesizer_vbee")


def _call_vbee(method: str, url: str, **kwargs) -> dict:
    """Call the Vbee REST API (with retry on transient errors) and unwrap result.

    Vbee wraps responses as {"status": 1, "result": {...}}. status != 1 (or a
    missing result) signals an API-level error even on HTTP 200.
    """
    response = _request_with_retry(
        method,
        url,
        headers={
            "Authorization": f"Bearer {config.VBEE_TOKEN}",
            "Content-Type": "application/json",
        },
        timeout=30,
        **kwargs,
    )
    data = response.json()

    if data.get("status") != 1 or "result" not in data:
        raise RuntimeError(f"Vbee API error: {data}")

    return data["result"]


def _create_vbee(text: str, voice_code: str, speed: float) -> str:
    """Create a TTS request, return its request_id."""
    result = _call_vbee(
        "POST",
        config.VBEE_API_URL,
        json={
            "app_id": config.VBEE_APP_ID,
            "input_text": text,
            "voice_code": voice_code,
            "audio_type": config.VBEE_AUDIO_TYPE,
            "bitrate": config.VBEE_BITRATE,
            "speed_rate": str(speed),
            # Required by the API even though we poll for the result.
            "callback_url": config.VBEE_CALLBACK_URL,
        },
    )
    request_id = result.get("request_id")
    if not request_id:
        raise RuntimeError(f"No request_id in Vbee response: {result}")
    return request_id


def _wait_for_vbee(request_id: str) -> str:
    """Poll the request until SUCCESS, return the audio_link URL."""
    start = time.time()
    poll_url = f"{config.VBEE_API_URL.rstrip('/')}/{request_id}"

    while time.time() - start < POLL_TIMEOUT:
        result = _call_vbee("GET", poll_url)
        state = str(result.get("status", "")).upper()

        if state == "SUCCESS":
            url = result.get("audio_link", "")
            if not url:
                raise RuntimeError("Vbee TTS succeeded but no audio_link returned")
            return url

        if state in ("FAILURE", "FAILED", "ERROR"):
            raise RuntimeError(f"Vbee TTS job failed: {result}")

        time.sleep(POLL_INTERVAL)

    raise TimeoutError(f"Vbee TTS polling timed out after {POLL_TIMEOUT}s for request {request_id}")


def _download_audio(url: str, output_path: str) -> str:
    """Download audio file from URL (with retry on transient CDN errors)."""
    response = _request_with_retry("GET", url, timeout=60)
    with open(output_path, "wb") as f:
        f.write(response.content)
    return output_path


def _synthesize_once(text: str, voice_code: str, speed: float, output_path: str) -> AudioSegment:
    """Run one create→poll→download→WAV cycle, return the decoded AudioSegment."""
    request_id = _create_vbee(text, voice_code, speed)
    logger.info(f"Vbee TTS job created: {request_id}")

    audio_url = _wait_for_vbee(request_id)
    logger.info("Vbee TTS completed, downloading audio...")

    temp_path = output_path + ".tmp"
    _download_audio(audio_url, temp_path)

    audio = AudioSegment.from_file(temp_path)
    audio.export(output_path, format="wav")
    os.remove(temp_path)
    return audio


def synthesize_segment_vbee(
    text_vi: str,
    output_path: str,
    target_duration: float | None = None,
    voice_id: str | None = None,
) -> dict:
    """Synthesize Vietnamese text to audio using the Vbee AIVoice API.

    Mirrors synthesize_segment_vi (LucyLab). `voice_id` here is a Vbee voice_code.

    Returns:
        dict with path, actual_duration, speed_adjusted, rate_applied
    """
    if not voice_id:
        raise ValueError(
            "voice_id (Vbee voice_code) is required. "
            "Set VBEE_VOICE_MALE/VBEE_VOICE_FEMALE in .env or use --voice male/female."
        )
    if not config.VBEE_APP_ID or not config.VBEE_TOKEN:
        raise ValueError("VBEE_APP_ID and VBEE_TOKEN must be set in .env for the vbee backend")

    max_speed = config.VIETNAMESE_TTS_MAX_SPEED

    # --- Step 1: Estimate optimal speed based on text length and target duration ---
    # NOTE: 19 chars/sec was calibrated for the LucyLab male voice; Vbee's pace
    # differs and this estimate may need its own calibration. Reused here so both
    # backends share the same fit-to-timeline behaviour.
    chars_per_sec_normal = 19.0
    safety_headroom = 1.10
    estimated_normal_duration = len(text_vi) / chars_per_sec_normal

    speed = 1.0
    if target_duration and estimated_normal_duration > 0:
        estimated_ratio = estimated_normal_duration / (target_duration * safety_headroom)
        if estimated_ratio > 1.0:
            speed = round(min(estimated_ratio, max_speed), 2)

    logger.info(
        f"Vbee TTS request: {len(text_vi)} chars, speed={speed}, target={target_duration:.1f}s"
        if target_duration else f"Vbee TTS request: {len(text_vi)} chars, speed={speed}"
    )

    # --- Step 2-3: Synthesize ---
    audio = _synthesize_once(text_vi, voice_id, speed, output_path)
    actual_duration = len(audio) / 1000.0
    speed_adjusted = speed != 1.0

    # --- Step 4: One-shot re-speed if the output still overflows the target ---
    if target_duration and actual_duration > target_duration * 1.1:
        if speed < max_speed:
            new_speed = round(min(actual_duration / target_duration * speed, max_speed), 2)
            logger.info(
                f"Re-adjusting speed: {actual_duration:.1f}s → ~{target_duration:.1f}s "
                f"(speed: {speed} → {new_speed})"
            )
            audio = _synthesize_once(text_vi, voice_id, new_speed, output_path)
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
