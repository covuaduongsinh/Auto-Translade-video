"""Background worker that drives run_pipeline_vi around its translate gate.

The pipeline runs in-process on a worker thread so the GUI can:
  * stream every module's log records (via a root-logger queue handler), and
  * react to the ``translate_pending`` gate (open an editor / call AI / wait).

All callbacks fire ON THE WORKER THREAD — the GUI is responsible for marshalling
back to the Tk main thread (it does so with a queue drained by ``widget.after``).
"""
import logging
import queue
import threading

import config
from pipeline_vi import run_pipeline_vi


class QueueLogHandler(logging.Handler):
    """Logging handler that pushes formatted lines into a thread-safe queue."""

    def __init__(self, log_queue: "queue.Queue[str]"):
        super().__init__()
        self.log_queue = log_queue
        self.setFormatter(logging.Formatter("%(asctime)s  %(name)s  %(message)s",
                                             datefmt="%H:%M:%S"))

    def emit(self, record: logging.LogRecord):
        try:
            self.log_queue.put_nowait(self.format(record))
        except Exception:  # noqa: BLE001 - never let logging break the pipeline
            pass


def install_log_handler(log_queue: "queue.Queue[str]") -> QueueLogHandler:
    """Attach a queue handler to the root logger so all module logs are captured.

    setup_logging() in src/utils.py uses named loggers that propagate to root, so
    a single root handler sees pipeline_vi, transcriber, synthesizer_vi, etc.
    """
    handler = QueueLogHandler(log_queue)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    return handler


class PipelineWorker(threading.Thread):
    """Runs the two-phase Vietnamese dubbing pipeline for a single video.

    gate_handler(work_dir) is invoked when phase 1 stops at the translate gate.
    It must BLOCK until transcript_vi.json is ready and return True to proceed,
    or return False to abort. Runs on this worker thread.
    """

    def __init__(
        self,
        *,
        url: str | None,
        file_path: str | None,
        source_lang: str,
        gender: str,
        tts_backend: str,
        bg_mode: str,
        bg_duck_db: float,
        skip_video: bool,
        output_dir: str,
        gate_handler,
        on_done,
        on_error,
        resume_dir: str | None = None,
        download_quality: dict | None = None,
        opencode_model: str | None = None,
    ):
        super().__init__(daemon=True)
        self.url = url
        self.file_path = file_path
        self.source_lang = source_lang
        self.gender = gender
        self.tts_backend = tts_backend
        self.bg_mode = bg_mode
        self.bg_duck_db = bg_duck_db
        self.skip_video = skip_video
        self.output_dir = output_dir
        self.download_quality = download_quality
        self.gate_handler = gate_handler
        self.on_done = on_done
        self.on_error = on_error
        self.resume_dir = resume_dir
        self.opencode_model = opencode_model
        self._cancel = threading.Event()

    def cancel(self):
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def _resolve_voice_id(self) -> str:
        # CLI parity: the backend must be set before resolving the voice so
        # config.vi_voice() picks the matching backend's voice id/code.
        config.TTS_BACKEND_VI = self.tts_backend
        return config.vi_voice(self.gender)

    def run(self):
        try:
            voice_id = self._resolve_voice_id()

            # --- Phase 1: acquire → (demucs) → ASR → transcript_original.json ---
            result = run_pipeline_vi(
                url=self.url,
                file_path=self.file_path,
                source_lang=self.source_lang,
                voice_id=voice_id,
                skip_video=self.skip_video,
                output_dir=self.output_dir,
                resume_dir=self.resume_dir,
                bg_mode=self.bg_mode,
                bg_duck_db=self.bg_duck_db,
                download_quality=self.download_quality,
            )

            # Already complete (e.g. resuming a dir that already had transcript_vi).
            if result.get("status") != "translate_pending":
                self.on_done(result)
                return

            work_dir = result["work_dir"]

            if self._cancel.is_set():
                self.on_error(RuntimeError("Đã huỷ trước bước dịch."))
                return

            # --- Translate gate (blocks until transcript_vi.json is ready) ---
            proceed = self.gate_handler(work_dir, opencode_model=self.opencode_model)
            if not proceed or self._cancel.is_set():
                self.on_error(RuntimeError("Đã huỷ ở bước dịch."))
                return

            # --- Phase 2: resume same work_dir → TTS → merge → video ---
            report = run_pipeline_vi(
                url=self.url,
                file_path=self.file_path,
                source_lang=self.source_lang,
                voice_id=voice_id,
                skip_video=self.skip_video,
                output_dir=self.output_dir,
                resume_dir=work_dir,
                bg_mode=self.bg_mode,
                bg_duck_db=self.bg_duck_db,
                download_quality=self.download_quality,
            )

            if report.get("status") == "translate_pending":
                self.on_error(RuntimeError(
                    "Vẫn chưa có transcript_vi.json sau bước dịch."
                ))
                return

            self.on_done(report)

        except Exception as e:  # noqa: BLE001 - surface to GUI
            self.on_error(e)
