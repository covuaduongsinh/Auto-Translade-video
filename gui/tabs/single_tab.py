"""Tab: dub a single video end-to-end with live logs and the translate gate."""
import os
import queue
from tkinter import filedialog

import customtkinter as ctk

import config
from pipeline_vi import _get_default_vi_output_dir
from gui.pipeline_runner import PipelineWorker
from gui.tabs.history_tab import _open_path
from gui.translate_gate import TranslateGateMixin, TRANSLATE_MODES
from gui.youtube import YouTubePreview, QUALITY_OPTIONS, QUALITY_LABELS

SOURCE_LANGS = ["en", "ja", "zh", "en-US", "ja-JP", "zh-CN", "zh-HK", "zh-TW"]


class SingleTab(TranslateGateMixin, ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        self.worker: PipelineWorker | None = None
        self._log_queue: "queue.Queue[str]" = queue.Queue()
        self._ui_queue: "queue.Queue" = queue.Queue()
        self._last_report: dict | None = None

        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._build_form()
        self._build_log()
        self._poll()

    # The runner shares one root log handler installed by the app; expose its queue.
    @property
    def log_queue(self):
        return self._log_queue

    # ---------------------------------------------------------------- form ----
    def _build_form(self):
        form = ctk.CTkScrollableFrame(self, width=360, label_text="Thiết lập")
        form.grid(row=0, column=0, sticky="nsew", padx=(0, 10), pady=0)

        def row(label):
            ctk.CTkLabel(form, text=label, anchor="w").pack(fill="x", padx=12, pady=(10, 2))

        # Video source
        row("Nguồn video (URL hoặc file)")
        self.src_var = ctk.StringVar()
        src_row = ctk.CTkFrame(form, fg_color="transparent")
        src_row.pack(fill="x", padx=12)
        ctk.CTkEntry(src_row, textvariable=self.src_var,
                     placeholder_text="https://… hoặc đường dẫn file").pack(
            side="left", fill="x", expand=True)
        ctk.CTkButton(src_row, text="Chọn…", width=64,
                      command=self._pick_file).pack(side="left", padx=(6, 0))
        ctk.CTkButton(src_row, text="ℹ Xem", width=64,
                      command=self._preview_info).pack(side="left", padx=(6, 0))

        row("Chất lượng tải (YouTube)")
        self.quality_var = ctk.StringVar(value=QUALITY_LABELS[0])
        ctk.CTkOptionMenu(form, values=QUALITY_LABELS,
                          variable=self.quality_var).pack(fill="x", padx=12)

        row("Ngôn ngữ nguồn")
        self.lang_var = ctk.StringVar(value=config.DEFAULT_SOURCE_LANG
                                      if config.DEFAULT_SOURCE_LANG in SOURCE_LANGS else "en")
        ctk.CTkOptionMenu(form, values=SOURCE_LANGS, variable=self.lang_var).pack(
            fill="x", padx=12)

        row("Giọng đọc")
        self.gender_var = ctk.StringVar(value="male")
        gframe = ctk.CTkFrame(form, fg_color="transparent")
        gframe.pack(fill="x", padx=12)
        ctk.CTkRadioButton(gframe, text="Nam", variable=self.gender_var,
                           value="male").pack(side="left", padx=(0, 16))
        ctk.CTkRadioButton(gframe, text="Nữ", variable=self.gender_var,
                           value="female").pack(side="left")

        row("TTS backend")
        self.tts_var = ctk.StringVar(value=config.TTS_BACKEND_VI)
        ctk.CTkOptionMenu(form, values=["lucylab", "vbee"],
                          variable=self.tts_var).pack(fill="x", padx=12)

        row("Chế độ dịch")
        self.tmode_var = ctk.StringVar(value="Opencode tự động (đọc & dịch)")
        ctk.CTkOptionMenu(form, values=list(TRANSLATE_MODES.keys()),
                          variable=self.tmode_var).pack(fill="x", padx=12)

        row("Model dịch (opencode)")
        self.opencode_model_var = ctk.StringVar(value=config.OPENCODE_DEFAULT_MODEL)
        ctk.CTkOptionMenu(form, values=config.OPENCODE_FREE_MODELS,
                          variable=self.opencode_model_var).pack(fill="x", padx=12)

        row("Model dịch (Claude)")
        self.claude_model_var = ctk.StringVar(value=config.CLAUDE_DEFAULT_MODEL)
        ctk.CTkOptionMenu(form, values=config.CLAUDE_MODELS,
                          variable=self.claude_model_var).pack(fill="x", padx=12)

        row("Âm nền gốc")
        self.bg_var = ctk.StringVar(value="duck")
        ctk.CTkOptionMenu(form, values=["demucs", "duck", "none"], variable=self.bg_var,
                          command=self._on_bg_change).pack(fill="x", padx=12)
        self.duck_frame = ctk.CTkFrame(form, fg_color="transparent")
        self.duck_frame.pack(fill="x", padx=12, pady=(4, 0))
        self.duck_label = ctk.CTkLabel(self.duck_frame, text="Giảm âm nền: -12 dB", anchor="w")
        self.duck_label.pack(fill="x")
        self.duck_var = ctk.DoubleVar(value=-12.0)
        ctk.CTkSlider(self.duck_frame, from_=-20, to=-3, variable=self.duck_var,
                      command=self._on_duck).pack(fill="x")

        self.skip_video_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(form, text="Bỏ qua ghép video (chỉ tạo audio)",
                        variable=self.skip_video_var).pack(fill="x", padx=12, pady=(12, 4))

        self.autoopen_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(form, text="Tự mở video khi hoàn tất",
                        variable=self.autoopen_var).pack(fill="x", padx=12, pady=(0, 4))

        row("Thư mục output")
        self.out_var = ctk.StringVar(value=_get_default_vi_output_dir())
        ctk.CTkEntry(form, textvariable=self.out_var).pack(fill="x", padx=12)

        self.run_btn = ctk.CTkButton(form, text="▶ Chạy", height=40,
                                     command=self._on_run)
        self.run_btn.pack(fill="x", padx=12, pady=(16, 4))
        self.cancel_btn = ctk.CTkButton(form, text="Huỷ", height=32, fg_color="gray40",
                                        hover_color="gray30", command=self._on_cancel,
                                        state="disabled")
        self.cancel_btn.pack(fill="x", padx=12, pady=(0, 12))

    def _build_log(self):
        right = ctk.CTkFrame(self)
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_rowconfigure(1, weight=1)
        right.grid_columnconfigure(0, weight=1)

        self.status_var = ctk.StringVar(value="Sẵn sàng.")
        ctk.CTkLabel(right, textvariable=self.status_var, anchor="w",
                     font=ctk.CTkFont(size=14, weight="bold")).grid(
            row=0, column=0, sticky="ew", padx=12, pady=(10, 4))

        self.log_box = ctk.CTkTextbox(right, wrap="word", font=ctk.CTkFont(family="Consolas", size=12))
        self.log_box.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 8))
        self.log_box.configure(state="disabled")

        result_bar = ctk.CTkFrame(right, fg_color="transparent")
        result_bar.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 12))
        self.open_video_btn = ctk.CTkButton(result_bar, text="▶ Mở video", width=140,
                                            state="disabled", command=self._open_result_video)
        self.open_video_btn.pack(side="left")
        self.open_folder_btn = ctk.CTkButton(result_bar, text="📁 Mở thư mục", width=140,
                                             fg_color="gray40", hover_color="gray30",
                                             state="disabled", command=self._open_result_folder)
        self.open_folder_btn.pack(side="left", padx=8)

    # ------------------------------------------------------------- handlers ---
    def _pick_file(self):
        path = filedialog.askopenfilename(
            title="Chọn video",
            filetypes=[("Video", "*.mp4 *.mkv *.webm *.mov *.avi"), ("Tất cả", "*.*")],
        )
        if path:
            self.src_var.set(path)

    def _preview_info(self):
        url = self.src_var.get().strip()
        if not url or os.path.exists(url):
            self.status_var.set("⚠ Hãy nhập URL YouTube để xem trước.")
            return
        win = ctk.CTkToplevel(self)
        win.title("Thông tin video")
        win.geometry("380x300")
        win.after(120, lambda: (win.lift(), win.focus_force()))
        preview = YouTubePreview(win)
        preview.pack(fill="both", expand=True, padx=10, pady=10)
        preview.set_message("Đang lấy thông tin…")

        def on_result(info):
            if info.get("is_playlist"):
                n = len(info.get("entries", []))
                preview.set_message(
                    f"Đây là playlist ({n} video).\nDùng tab Hàng loạt để nạp.")
            else:
                preview.show(info)

        preview.probe(url, on_result,
                      lambda e: preview.set_message(f"Lỗi: {e}"))

    def _on_bg_change(self, _value=None):
        if self.bg_var.get() == "duck":
            self.duck_frame.pack(fill="x", padx=12, pady=(4, 0))
        else:
            self.duck_frame.pack_forget()

    def _on_duck(self, _value=None):
        self.duck_label.configure(text=f"Giảm âm nền: {self.duck_var.get():.0f} dB")

    def _on_duck_value(self):
        return float(self.duck_var.get())

    def _set_running(self, running: bool):
        self.run_btn.configure(state="disabled" if running else "normal")
        self.cancel_btn.configure(state="normal" if running else "disabled")

    def _append_log(self, text: str):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", text + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _on_run(self):
        src = self.src_var.get().strip()
        if not src:
            self.status_var.set("⚠ Hãy nhập URL hoặc chọn file video.")
            return
        is_file = os.path.exists(src)
        url = None if is_file else src
        file_path = src if is_file else None

        self._set_running(True)
        self._last_report = None
        self.open_video_btn.configure(state="disabled")
        self.open_folder_btn.configure(state="disabled")
        self.status_var.set("Đang chạy… (Pha 1: tải/tách nhạc/ASR)")

        self.worker = PipelineWorker(
            url=url,
            file_path=file_path,
            source_lang=self.lang_var.get(),
            gender=self.gender_var.get(),
            tts_backend=self.tts_var.get(),
            bg_mode=self.bg_var.get(),
            bg_duck_db=self._on_duck_value(),
            skip_video=self.skip_video_var.get(),
            output_dir=self.out_var.get().strip() or _get_default_vi_output_dir(),
            download_quality=QUALITY_OPTIONS[self.quality_var.get()],
            gate_handler=self._gate_handler,
            on_done=lambda report: self._ui_queue.put(lambda: self._on_done(report)),
            on_error=lambda exc: self._ui_queue.put(lambda: self._on_error(exc)),
            opencode_model=self.opencode_model_var.get(),
            claude_model=self.claude_model_var.get(),
        )
        self.worker.start()

    def _on_cancel(self):
        if self.worker:
            self.worker.cancel()
            self.status_var.set("Đang huỷ… (sẽ dừng ở mốc an toàn tiếp theo)")

    # ---------------------------------------------------------- completion ----
    def _on_done(self, report: dict):
        self._set_running(False)
        self._last_report = report
        out = report.get("output_dir", "")
        self.status_var.set(f"✅ Hoàn tất — {report.get('total_segments', '?')} câu · {out}")

        if out and os.path.isdir(out):
            self.open_folder_btn.configure(state="normal")

        video = (report.get("files", {}) or {}).get("dubbed_video")
        if video and os.path.exists(video):
            self.open_video_btn.configure(state="normal")
            if self.autoopen_var.get():
                _open_path(video)

    def _open_result_video(self):
        if not self._last_report:
            return
        video = (self._last_report.get("files", {}) or {}).get("dubbed_video")
        _open_path(video)

    def _open_result_folder(self):
        if not self._last_report:
            return
        _open_path(self._last_report.get("output_dir", ""))

    def _on_error(self, exc: Exception):
        self._set_running(False)
        self.status_var.set(f"❌ Lỗi: {exc}")

    # ---------------------------------------------------------------- poll ----
    def _poll(self):
        # Drain logs (bulk) then any queued main-thread callables.
        drained = []
        try:
            while True:
                drained.append(self._log_queue.get_nowait())
        except queue.Empty:
            pass
        if drained:
            self.log_box.configure(state="normal")
            for line in drained:
                self.log_box.insert("end", line + "\n")
            self.log_box.see("end")
            self.log_box.configure(state="disabled")

        try:
            while True:
                fn = self._ui_queue.get_nowait()
                fn()
        except queue.Empty:
            pass

        self.after(150, self._poll)
