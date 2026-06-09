"""Tab: a YouTube-first entry point — preview a URL, pick quality, then either
dub a single video (with the translate gate) or push a whole playlist into the
batch tab.
"""
import os
import queue

import customtkinter as ctk

import config
from pipeline_vi import _get_default_vi_output_dir
from gui.pipeline_runner import PipelineWorker
from gui.tabs.history_tab import _open_path
from gui.translate_gate import TranslateGateMixin, TRANSLATE_MODES
from gui.youtube import (YouTubePreview, QUALITY_OPTIONS, QUALITY_LABELS,
                         format_duration)

SOURCE_LANGS = ["en", "ja", "zh", "en-US", "ja-JP", "zh-CN", "zh-HK", "zh-TW"]


class YouTubeTab(TranslateGateMixin, ctk.CTkFrame):
    def __init__(self, master, batch_tab=None):
        super().__init__(master, fg_color="transparent")
        self.batch_tab = batch_tab
        self.worker: PipelineWorker | None = None
        self._log_queue: "queue.Queue[str]" = queue.Queue()
        self._ui_queue: "queue.Queue" = queue.Queue()
        self._last_report: dict | None = None
        self._info: dict | None = None
        self._entry_vars: list = []  # (BooleanVar, entry dict) for playlists

        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._build_form()
        self._build_right()
        self._poll()

    @property
    def log_queue(self):
        return self._log_queue

    # ---------------------------------------------------------------- form ----
    def _build_form(self):
        form = ctk.CTkScrollableFrame(self, width=340, label_text="Thiết lập")
        form.grid(row=0, column=0, sticky="nsew", padx=(0, 10))

        def row(label):
            ctk.CTkLabel(form, text=label, anchor="w").pack(fill="x", padx=12, pady=(10, 2))

        row("URL YouTube (video hoặc playlist)")
        self.url_var = ctk.StringVar()
        url_row = ctk.CTkFrame(form, fg_color="transparent")
        url_row.pack(fill="x", padx=12)
        ctk.CTkEntry(url_row, textvariable=self.url_var,
                     placeholder_text="https://www.youtube.com/…").pack(
            side="left", fill="x", expand=True)
        ctk.CTkButton(url_row, text="Lấy thông tin", width=110,
                      command=self._fetch).pack(side="left", padx=(6, 0))

        row("Chất lượng tải")
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
        self.tmode_var = ctk.StringVar(value="Claude tự động (đọc & dịch)")
        ctk.CTkOptionMenu(form, values=list(TRANSLATE_MODES.keys()),
                          variable=self.tmode_var).pack(fill="x", padx=12)

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

        self.run_btn = ctk.CTkButton(form, text="▶ Xử lý video này", height=40,
                                     command=self._on_run, state="disabled")
        self.run_btn.pack(fill="x", padx=12, pady=(16, 4))
        self.batch_btn = ctk.CTkButton(form, text="➕ Đưa playlist vào hàng loạt",
                                       height=36, fg_color="#2d6a4f",
                                       hover_color="#245741",
                                       command=self._send_to_batch, state="disabled")
        self.batch_btn.pack(fill="x", padx=12, pady=(0, 4))
        self.cancel_btn = ctk.CTkButton(form, text="Huỷ", height=32, fg_color="gray40",
                                        hover_color="gray30", command=self._on_cancel,
                                        state="disabled")
        self.cancel_btn.pack(fill="x", padx=12, pady=(0, 12))

    def _build_right(self):
        right = ctk.CTkFrame(self)
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_rowconfigure(2, weight=1)
        right.grid_columnconfigure(0, weight=1)

        self.status_var = ctk.StringVar(value="Dán URL rồi bấm “Lấy thông tin”.")
        ctk.CTkLabel(right, textvariable=self.status_var, anchor="w",
                     font=ctk.CTkFont(size=14, weight="bold")).grid(
            row=0, column=0, sticky="ew", padx=12, pady=(10, 4))

        # Preview (single video) — swapped in/out with the playlist list.
        self.preview = YouTubePreview(right)
        self.playlist_box = ctk.CTkScrollableFrame(right, label_text="Video trong playlist",
                                                    height=180)

        self.log_box = ctk.CTkTextbox(right, wrap="word",
                                      font=ctk.CTkFont(family="Consolas", size=12))
        self.log_box.grid(row=2, column=0, sticky="nsew", padx=12, pady=(8, 8))
        self.log_box.configure(state="disabled")

        result_bar = ctk.CTkFrame(right, fg_color="transparent")
        result_bar.grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 12))
        self.open_video_btn = ctk.CTkButton(result_bar, text="▶ Mở video", width=140,
                                            state="disabled", command=self._open_result_video)
        self.open_video_btn.pack(side="left")
        self.open_folder_btn = ctk.CTkButton(result_bar, text="📁 Mở thư mục", width=140,
                                             fg_color="gray40", hover_color="gray30",
                                             state="disabled", command=self._open_result_folder)
        self.open_folder_btn.pack(side="left", padx=8)

    # ----------------------------------------------------------- fetch info ---
    def _fetch(self):
        url = self.url_var.get().strip()
        if not url:
            self.status_var.set("⚠ Hãy nhập URL YouTube.")
            return
        self._info = None
        self.run_btn.configure(state="disabled")
        self.batch_btn.configure(state="disabled")
        self.preview.grid_forget()
        self.playlist_box.grid_forget()
        self.status_var.set("Đang lấy thông tin…")
        self.preview.probe(url, self._on_info, self._on_info_error)

    def _on_info(self, info: dict):
        self._info = info
        if info.get("is_playlist"):
            self._show_playlist(info)
        else:
            self.preview.grid(row=1, column=0, sticky="ew", padx=12)
            self.preview.show(info)
            self.run_btn.configure(state="normal")
            self.status_var.set(f"Video: {info.get('title', '')}")

    def _on_info_error(self, exc: Exception):
        self.status_var.set(f"❌ Không lấy được thông tin: {exc}")

    def _show_playlist(self, info: dict):
        for w in self.playlist_box.winfo_children():
            w.destroy()
        self._entry_vars = []
        entries = info.get("entries", [])
        for e in entries:
            var = ctk.BooleanVar(value=True)
            dur = format_duration(e.get("duration"))
            text = f"{e.get('title', e.get('id', ''))}  ({dur})"
            ctk.CTkCheckBox(self.playlist_box, text=text, variable=var).pack(
                fill="x", padx=6, pady=2, anchor="w")
            self._entry_vars.append((var, e))
        self.playlist_box.grid(row=1, column=0, sticky="ew", padx=12)
        self.batch_btn.configure(state="normal" if self.batch_tab and entries else "disabled")
        self.status_var.set(
            f"Playlist “{info.get('title', '')}” — {len(entries)} video.")

    def _send_to_batch(self):
        if not self.batch_tab:
            self.status_var.set("⚠ Không tìm thấy tab Hàng loạt.")
            return
        urls = [e["url"] for var, e in self._entry_vars if var.get() and e.get("url")]
        if not urls:
            self.status_var.set("⚠ Chưa chọn video nào.")
            return
        self.batch_tab.add_videos(urls)
        self.status_var.set(f"Đã đưa {len(urls)} video sang tab Hàng loạt.")

    # -------------------------------------------------------------- handlers ---
    def _on_bg_change(self, _value=None):
        if self.bg_var.get() == "duck":
            self.duck_frame.pack(fill="x", padx=12, pady=(4, 0))
        else:
            self.duck_frame.pack_forget()

    def _on_duck(self, _value=None):
        self.duck_label.configure(text=f"Giảm âm nền: {self.duck_var.get():.0f} dB")

    def _set_running(self, running: bool):
        self.run_btn.configure(state="disabled" if running else "normal")
        self.cancel_btn.configure(state="normal" if running else "disabled")

    def _on_run(self):
        if not self._info or self._info.get("is_playlist"):
            self.status_var.set("⚠ Hãy lấy thông tin một video đơn trước.")
            return
        url = self._info.get("webpage_url") or self.url_var.get().strip()

        self._set_running(True)
        self._last_report = None
        self.open_video_btn.configure(state="disabled")
        self.open_folder_btn.configure(state="disabled")
        self.status_var.set("Đang chạy… (Pha 1: tải/tách nhạc/ASR)")

        self.worker = PipelineWorker(
            url=url,
            file_path=None,
            source_lang=self.lang_var.get(),
            gender=self.gender_var.get(),
            tts_backend=self.tts_var.get(),
            bg_mode=self.bg_var.get(),
            bg_duck_db=float(self.duck_var.get()),
            skip_video=self.skip_video_var.get(),
            output_dir=self.out_var.get().strip() or _get_default_vi_output_dir(),
            download_quality=QUALITY_OPTIONS[self.quality_var.get()],
            gate_handler=self._gate_handler,
            on_done=lambda report: self._ui_queue.put(lambda: self._on_done(report)),
            on_error=lambda exc: self._ui_queue.put(lambda: self._on_error(exc)),
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
        if self._last_report:
            _open_path((self._last_report.get("files", {}) or {}).get("dubbed_video"))

    def _open_result_folder(self):
        if self._last_report:
            _open_path(self._last_report.get("output_dir", ""))

    def _on_error(self, exc: Exception):
        self._set_running(False)
        self.status_var.set(f"❌ Lỗi: {exc}")

    # ---------------------------------------------------------------- poll ----
    def _poll(self):
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
                self._ui_queue.get_nowait()()
        except queue.Empty:
            pass
        self.after(150, self._poll)
