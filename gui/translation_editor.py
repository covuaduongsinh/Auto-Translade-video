"""Translation editor dialog — fill / review the Vietnamese text per segment.

Opens on the Tk main thread while the pipeline worker is blocked at the translate
gate. On "Lưu & Tiếp tục" it writes transcript_vi.json (original segments + a
``text_vi`` field) and signals the worker to resume.
"""
import json
import os

import customtkinter as ctk


class TranslationEditor(ctk.CTkToplevel):
    def __init__(self, master, work_dir: str, on_finish):
        super().__init__(master)
        self.work_dir = work_dir
        self.on_finish = on_finish
        self._finished = False

        self.orig_path = os.path.join(work_dir, "transcript_original.json")
        self.vi_path = os.path.join(work_dir, "transcript_vi.json")

        with open(self.orig_path, encoding="utf-8") as f:
            self.segments = json.load(f)

        # Pre-fill from an existing transcript_vi.json (e.g. AI review mode).
        prefill: dict[int, str] = {}
        if os.path.exists(self.vi_path):
            try:
                with open(self.vi_path, encoding="utf-8") as f:
                    for s in json.load(f):
                        prefill[s["id"]] = s.get("text_vi", "")
            except Exception:  # noqa: BLE001 - bad file, just start blank
                prefill = {}

        self.title("Soạn bản dịch tiếng Việt")
        self.geometry("980x680")
        self.minsize(700, 480)

        header = ctk.CTkLabel(
            self,
            text=("Nhập / chỉnh bản dịch tiếng Việt cho từng câu. "
                  "Có thể dán toàn bộ JSON transcript_vi nếu đã dịch sẵn."),
            wraplength=940,
            justify="left",
        )
        header.pack(fill="x", padx=16, pady=(14, 6))

        self.scroll = ctk.CTkScrollableFrame(self, label_text="Các câu thoại")
        self.scroll.pack(fill="both", expand=True, padx=16, pady=6)
        self.scroll.grid_columnconfigure(1, weight=1)

        self._boxes: dict[int, ctk.CTkTextbox] = {}
        for row, seg in enumerate(self.segments):
            orig = ctk.CTkLabel(
                self.scroll,
                text=f"#{seg['id']}  ({seg['start']:.1f}–{seg['end']:.1f}s)\n{seg.get('text', '')}",
                wraplength=380,
                justify="left",
                anchor="nw",
            )
            orig.grid(row=row, column=0, sticky="nw", padx=(4, 10), pady=6)

            box = ctk.CTkTextbox(self.scroll, height=56, wrap="word")
            box.grid(row=row, column=1, sticky="ew", padx=4, pady=6)
            if prefill.get(seg["id"]):
                box.insert("1.0", prefill[seg["id"]])
            self._boxes[seg["id"]] = box

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=16, pady=(6, 14))

        ctk.CTkButton(bar, text="Dán toàn bộ JSON…", width=160,
                      command=self._paste_json).pack(side="left")
        ctk.CTkButton(bar, text="Huỷ", width=90, fg_color="gray40",
                      hover_color="gray30", command=self._cancel).pack(side="right")
        ctk.CTkButton(bar, text="Lưu & Tiếp tục ▶", width=160,
                      command=self._save).pack(side="right", padx=8)

        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.after(120, self._raise)

    def _raise(self):
        self.lift()
        self.focus_force()

    def _paste_json(self):
        dlg = ctk.CTkInputDialog(
            text="Dán mảng JSON transcript_vi (mỗi phần tử có id và text_vi):",
            title="Dán JSON bản dịch",
        )
        raw = dlg.get_input()
        if not raw:
            return
        try:
            data = json.loads(raw)
            mapping = {int(s["id"]): s.get("text_vi", s.get("text", "")) for s in data}
        except Exception as e:  # noqa: BLE001
            self._toast(f"JSON không hợp lệ: {e}")
            return
        filled = 0
        for sid, box in self._boxes.items():
            if sid in mapping:
                box.delete("1.0", "end")
                box.insert("1.0", str(mapping[sid]))
                filled += 1
        self._toast(f"Đã điền {filled} câu từ JSON.")

    def _toast(self, msg: str):
        # Lightweight inline feedback via the window title (no extra deps).
        self.title(f"Soạn bản dịch — {msg}")

    def _collect(self) -> list[dict]:
        out = []
        for seg in self.segments:
            new_seg = dict(seg)
            new_seg["text_vi"] = self._boxes[seg["id"]].get("1.0", "end").strip()
            out.append(new_seg)
        return out

    def _save(self):
        segments = self._collect()
        empty = [s["id"] for s in segments if not s["text_vi"]]
        if empty:
            self._toast(f"Còn {len(empty)} câu chưa dịch (id {empty[:6]}…)")
            return
        with open(self.vi_path, "w", encoding="utf-8") as f:
            json.dump(segments, f, ensure_ascii=False, indent=2)
        self._finish(True)

    def _cancel(self):
        self._finish(False)

    def _finish(self, proceed: bool):
        if self._finished:
            return
        self._finished = True
        cb = self.on_finish
        self.destroy()
        cb(proceed)
