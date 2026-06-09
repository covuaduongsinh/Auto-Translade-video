"""Tab: list past runs (scanned from report.json) and open their outputs."""
import glob
import json
import os
import subprocess
import sys

import customtkinter as ctk

from pipeline_vi import _get_default_vi_output_dir


def _open_path(path: str):
    """Open a file/folder with the OS default handler."""
    if not path or not os.path.exists(path):
        return
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 - intended shell open on Windows
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


class HistoryTab(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=4, pady=(4, 8))
        ctk.CTkButton(top, text="↻ Làm mới", command=self.refresh).pack(side="left")
        self.status_var = ctk.StringVar(value="")
        ctk.CTkLabel(top, textvariable=self.status_var, anchor="w").pack(
            side="left", padx=12)

        self.scroll = ctk.CTkScrollableFrame(self, label_text="Lịch sử các lần chạy")
        self.scroll.grid(row=1, column=0, sticky="nsew", padx=4, pady=4)
        self.scroll.grid_columnconfigure(0, weight=1)

        self.refresh()

    def refresh(self):
        for w in self.scroll.winfo_children():
            w.destroy()

        out_dir = _get_default_vi_output_dir()
        reports = sorted(glob.glob(os.path.join(out_dir, "*", "report.json")),
                         key=os.path.getmtime, reverse=True)
        if not reports:
            ctk.CTkLabel(self.scroll, text=f"Chưa có lần chạy nào trong {out_dir}",
                         anchor="w").grid(row=0, column=0, sticky="w", padx=6, pady=6)
            self.status_var.set("")
            return

        self.status_var.set(f"{len(reports)} phiên — {out_dir}")
        for r, rpath in enumerate(reports):
            try:
                with open(rpath, encoding="utf-8") as f:
                    rep = json.load(f)
            except Exception:  # noqa: BLE001
                continue
            self._render_card(r, rpath, rep)

    def _render_card(self, row: int, report_path: str, rep: dict):
        work_dir = os.path.dirname(report_path)
        card = ctk.CTkFrame(self.scroll)
        card.grid(row=row, column=0, sticky="ew", padx=4, pady=4)
        card.grid_columnconfigure(0, weight=1)

        title = rep.get("session_id", os.path.basename(work_dir))
        info = (f"{title}   ·   {rep.get('total_segments', '?')} câu   ·   "
                f"{rep.get('total_original_duration', 0):.0f}s gốc → "
                f"{rep.get('total_tts_duration', 0):.0f}s VI   ·   "
                f"{rep.get('processing_time_seconds', 0):.0f}s xử lý")
        ctk.CTkLabel(card, text=info, anchor="w").grid(
            row=0, column=0, sticky="w", padx=10, pady=(8, 2))
        ctk.CTkLabel(card, text=rep.get("source_url") or work_dir, anchor="w",
                     text_color="gray60").grid(row=1, column=0, sticky="w", padx=10, pady=(0, 8))

        bar = ctk.CTkFrame(card, fg_color="transparent")
        bar.grid(row=0, column=1, rowspan=2, sticky="e", padx=8)
        video = (rep.get("files", {}) or {}).get("dubbed_video")
        if video and os.path.exists(video):
            ctk.CTkButton(bar, text="▶ Mở video", width=110,
                          command=lambda v=video: _open_path(v)).pack(side="left", padx=4)
        ctk.CTkButton(bar, text="📁 Mở thư mục", width=110, fg_color="gray40",
                      hover_color="gray30",
                      command=lambda d=work_dir: _open_path(d)).pack(side="left", padx=4)
