"""Source-separation step for the dubbing pipeline.

Splits ``original_audio.wav`` into ``vocals.wav`` (the original speaker) and
``no_vocals.wav`` (everything else: music, SFX, ambient). The pipeline mixes
``no_vocals.wav`` with the synthesised TTS so dubbed videos retain their
original soundtrack.

Demucs is invoked through its Python API rather than the ``demucs.separate``
CLI so the writer never touches ``torchaudio.save`` — that path requires
``torchcodec`` on torchaudio>=2.10, whose Windows wheels rely on a specific
FFmpeg ABI we can't guarantee.  Audio is written via ``soundfile`` instead.

To avoid silent hangs on CPU, Demucs inference runs in a separate process with
a configurable timeout (``config.DEMUCS_TIMEOUT_SECONDS``).
"""
import multiprocessing as mp
import os
import subprocess
import threading
import time

import config
from src.utils import setup_logging

logger = setup_logging("vocal_separator")

DEFAULT_MODEL = "htdemucs"


def separate_vocals(
    input_wav: str,
    output_dir: str,
    model: str = DEFAULT_MODEL,
) -> dict[str, str | None]:
    """Run Demucs two-stem separation on ``input_wav``.

    Writes ``<output_dir>/vocals.wav`` and ``<output_dir>/no_vocals.wav``,
    re-encoded to match the sample rate / mono channel layout of
    ``original_audio.wav`` so they overlay cleanly with the TTS segments.

    Returns ``{"vocals": path, "no_vocals": path}`` on success, or both ``None``
    on failure (caller falls back to silent-base merge). If both output files
    already exist the function short-circuits — useful when resuming a
    partially-run work directory.
    """
    vocals_out = os.path.join(output_dir, "vocals.wav")
    no_vocals_out = os.path.join(output_dir, "no_vocals.wav")

    if (os.path.exists(no_vocals_out) and os.path.getsize(no_vocals_out) > 0
            and os.path.exists(vocals_out) and os.path.getsize(vocals_out) > 0):
        logger.info(f"Reusing existing separation: {no_vocals_out}")
        return {"vocals": vocals_out, "no_vocals": no_vocals_out}

    if not os.path.exists(input_wav):
        logger.error(f"Input audio not found: {input_wav}")
        return {"vocals": None, "no_vocals": None}

    raw_vocals = os.path.join(output_dir, "_vocals_raw.wav")
    raw_no_vocals = os.path.join(output_dir, "_no_vocals_raw.wav")

    try:
        _run_demucs(input_wav, raw_vocals, raw_no_vocals, model)
    except Exception as exc:
        logger.warning(f"Demucs separation failed: {exc}; falling back to silent base.")
        for path in (raw_vocals, raw_no_vocals):
            if os.path.exists(path):
                os.remove(path)
        return {"vocals": None, "no_vocals": None}

    sample_rate = str(config.AUDIO_SAMPLE_RATE)
    try:
        _normalize(raw_vocals, vocals_out, sample_rate)
        _normalize(raw_no_vocals, no_vocals_out, sample_rate)
    except RuntimeError as exc:
        logger.warning(f"Post-processing Demucs output failed: {exc}")
        for path in (vocals_out, no_vocals_out):
            if os.path.exists(path):
                os.remove(path)
        return {"vocals": None, "no_vocals": None}
    finally:
        for path in (raw_vocals, raw_no_vocals):
            if os.path.exists(path):
                os.remove(path)

    logger.info(f"Vocal separation complete: {no_vocals_out}")
    return {"vocals": vocals_out, "no_vocals": no_vocals_out}


def _run_demucs(input_wav: str, vocals_out: str, no_vocals_out: str, model_name: str) -> None:
    """Run Demucs via its Python API; write stems with soundfile.

    To avoid blocking the main thread indefinitely, the actual inference is
    offloaded to a child process with a timeout. If the timeout is exceeded we
    abort and the caller falls back to a silent base.
    """
    timeout = getattr(config, "DEMUCS_TIMEOUT_SECONDS", 1800)

    # Guard required for Windows spawn: child re-imports the main module.
    ctx = mp.get_context("spawn")
    proc = ctx.Process(
        target=_demucs_worker,
        args=(input_wav, vocals_out, no_vocals_out, model_name),
    )

    logger.info(f"Starting Demucs worker (timeout {timeout}s) for {input_wav}")
    proc.start()

    # Heartbeat: log elapsed time so users know it is still running.
    start = time.time()
    while proc.is_alive():
        elapsed = time.time() - start
        if elapsed >= timeout:
            logger.warning(f"Demucs exceeded {timeout}s timeout; terminating worker.")
            proc.terminate()
            proc.join(timeout=10.0)
            if proc.is_alive():
                proc.kill()
                proc.join(timeout=5.0)
            raise RuntimeError(f"Demucs timed out after {timeout}s")

        int_elapsed = int(elapsed)
        if int_elapsed > 0 and int_elapsed % 60 == 0:
            logger.info(f"Demucs still running… {int_elapsed // 60} minute(s) elapsed")
        proc.join(timeout=1.0)

    proc.join(timeout=5.0)
    if proc.is_alive():
        logger.warning("Demucs worker did not stop; terminating.")
        proc.terminate()
        proc.join(timeout=5.0)

    if proc.exitcode != 0:
        raise RuntimeError(
            f"Demucs worker failed (exit code {proc.exitcode})"
        )


def _demucs_worker(
    input_wav: str,
    vocals_out: str,
    no_vocals_out: str,
    model_name: str,
) -> None:
    """Child-process entry point that runs the actual Demucs inference."""
    import numpy as np
    import soundfile as sf
    import torch
    from demucs.apply import apply_model
    from demucs.audio import convert_audio
    from demucs.pretrained import get_model

    logger.info(f"Loading Demucs model: {model_name}")
    model = get_model(model_name)
    model.eval()

    audio, src_sr = sf.read(input_wav, always_2d=True)
    wav = torch.from_numpy(audio.T).float()

    wav = convert_audio(wav, src_sr, model.samplerate, model.audio_channels)
    ref = wav.mean(0)
    wav = (wav - ref.mean()) / (ref.std() + 1e-8)

    logger.info(f"Running Demucs ({model_name}) on {input_wav}")
    with torch.no_grad():
        sources = apply_model(model, wav[None], split=True, overlap=0.25, progress=False)[0]
    sources = sources * (ref.std() + 1e-8) + ref.mean()

    stem_index = {name: i for i, name in enumerate(model.sources)}
    if "vocals" not in stem_index:
        raise RuntimeError(f"Model {model_name} has no 'vocals' stem; got {model.sources}")

    vocals = sources[stem_index["vocals"]]
    others = [sources[i] for name, i in stem_index.items() if name != "vocals"]
    no_vocals = torch.stack(others).sum(dim=0)

    _save_wav(vocals_out, vocals.cpu().numpy(), model.samplerate)
    _save_wav(no_vocals_out, no_vocals.cpu().numpy(), model.samplerate)
    logger.info("Demucs worker finished")


def _save_wav(path: str, arr, sample_rate: int) -> None:
    """Write a (channels, samples) float tensor to a 16-bit PCM WAV."""
    import numpy as np
    import soundfile as sf

    data = np.clip(arr, -1.0, 1.0).T  # (samples, channels) — soundfile convention
    sf.write(path, data, sample_rate, subtype="PCM_16")


def _normalize(src: str, dst: str, sample_rate: str) -> None:
    """Re-encode raw stem to mono PCM at the pipeline's sample rate."""
    cmd = [
        "ffmpeg", "-y", "-i", src,
        "-ac", "1",
        "-ar", sample_rate,
        "-acodec", "pcm_s16le",
        dst,
    ]
    result = subprocess.run(
        cmd, capture_output=True, encoding="utf-8", errors="replace"
    )
    if result.returncode != 0 or not os.path.exists(dst) or os.path.getsize(dst) == 0:
        tail = (result.stderr or "")[-300:].strip()
        raise RuntimeError(f"ffmpeg normalize failed for {src}: {tail}")
