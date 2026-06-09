"""Reusable translate-gate logic shared by the single-video and YouTube tabs.

When the pipeline stops at ``translate_pending`` the GUI must supply a
transcript_vi.json before phase 2 can run. The four modes (manual editor, AI,
AI + review, external) are identical wherever a single video is dubbed, so they
live here as a mixin.

A host tab must provide these attributes:
  * ``self._ui_queue``  — queue drained on the Tk main thread
  * ``self.lang_var``   — source-language StringVar
  * ``self.tmode_var``  — translate-mode StringVar (key into TRANSLATE_MODES)
  * ``self.status_var`` — status StringVar
and be a Tk widget (CTkFrame) usable as a dialog parent.
"""
import json
import os
import threading

import customtkinter as ctk

import config
from pipeline_vi import LANG_MAP
from gui.translation_editor import TranslationEditor

# Dropdown value → translate behaviour
TRANSLATE_MODES = {
    "Trình soạn dịch (tự nhập)": "editor",
    "AI tự động (Gemini)": "ai",
    "AI tự động rồi xem lại": "ai_review",
    "Claude tự động (đọc & dịch)": "claude",
    "Claude tự động rồi xem lại": "claude_review",
    "Dịch ngoài app rồi Resume": "external",
}


class TranslateGateMixin:
    # ----- translate gate: called on WORKER thread, blocks until UI is done ----
    def _gate_handler(self, work_dir: str) -> bool:
        ev = threading.Event()
        holder = {"proceed": False}
        self._ui_queue.put(lambda: self._open_gate(work_dir, ev, holder))
        ev.wait()
        return holder["proceed"]

    def _open_gate(self, work_dir, ev, holder):
        mode = TRANSLATE_MODES[self.tmode_var.get()]

        def finish(proceed: bool):
            holder["proceed"] = proceed
            ev.set()
            if proceed:
                self.status_var.set("Đang chạy… (Pha 2: TTS → ghép audio → video)")

        if mode == "editor":
            self.status_var.set("Chờ bạn nhập bản dịch…")
            TranslationEditor(self, work_dir, on_finish=finish)
        elif mode in ("ai", "ai_review"):
            self.status_var.set("Đang dịch tự động bằng AI…")
            self._run_ai_translate(work_dir, review=(mode == "ai_review"), finish=finish)
        elif mode in ("claude", "claude_review"):
            self.status_var.set("Đang nhờ Claude đọc thư mục & dịch…")
            self._run_claude_translate(work_dir, review=(mode == "claude_review"), finish=finish)
        elif mode == "external":
            self._open_external_gate(work_dir, finish)

    def _run_ai_translate(self, work_dir, review: bool, finish):
        from src.translator import translate_transcript

        def work():
            try:
                with open(os.path.join(work_dir, "transcript_original.json"),
                          encoding="utf-8") as f:
                    segments = json.load(f)
                translated = translate_transcript(
                    segments=segments,
                    source_lang=LANG_MAP.get(self.lang_var.get(), self.lang_var.get()),
                    api_key=config.GOOGLE_API_KEY,
                    model_id=config.CONTENT_MODEL_ID,
                )
                with open(os.path.join(work_dir, "transcript_vi.json"),
                          "w", encoding="utf-8") as f:
                    json.dump(translated, f, ensure_ascii=False, indent=2)
                if review:
                    self._ui_queue.put(
                        lambda: TranslationEditor(self, work_dir, on_finish=finish))
                else:
                    self._ui_queue.put(lambda: finish(True))
            except Exception as e:  # noqa: BLE001
                msg = str(e)

                def fallback():
                    # AI failed (e.g. no Gemini key) — don't waste phase 1; let the
                    # user fill the translation by hand instead of aborting.
                    self.status_var.set(f"⚠ Dịch AI lỗi, chuyển sang nhập tay: {msg}")
                    TranslationEditor(self, work_dir, on_finish=finish)

                self._ui_queue.put(fallback)

        threading.Thread(target=work, daemon=True).start()

    def _run_claude_translate(self, work_dir, review: bool, finish):
        """Let the local Claude Code CLI read work_dir and write transcript_vi.json."""
        from src.translator_claude import translate_via_claude_cli

        self.status_var.set("Đang nhờ Claude đọc thư mục & dịch…")

        def work():
            try:
                translate_via_claude_cli(
                    work_dir,
                    source_lang=LANG_MAP.get(self.lang_var.get(), self.lang_var.get()),
                    model=(config.CLAUDE_MODEL_ID or None),
                )
                if review:
                    self._ui_queue.put(
                        lambda: TranslationEditor(self, work_dir, on_finish=finish))
                else:
                    self._ui_queue.put(lambda: finish(True))
            except Exception as e:  # noqa: BLE001
                msg = str(e)

                def fallback():
                    # Claude unavailable/failed — keep phase 1; let the user finish
                    # the translation by hand instead of aborting.
                    self.status_var.set(f"⚠ Claude dịch lỗi, chuyển sang nhập tay: {msg}")
                    TranslationEditor(self, work_dir, on_finish=finish)

                self._ui_queue.put(fallback)

        threading.Thread(target=work, daemon=True).start()

    def _open_external_gate(self, work_dir, finish):
        orig = os.path.join(work_dir, "transcript_original.json")
        vi = os.path.join(work_dir, "transcript_vi.json")
        win = ctk.CTkToplevel(self)
        win.title("Dịch ngoài app")
        win.geometry("640x300")
        win.after(120, lambda: (win.lift(), win.focus_force()))
        msg = (
            "Bấm “🤖 Nhờ Claude dịch tự động” để Claude tự đọc thư mục, dịch và tạo\n"
            "transcript_vi.json — hoặc tự dịch file dưới đây sang tiếng Việt (qua\n"
            "Claude/ChatGPT…), lưu thành transcript_vi.json (giữ id, thêm text_vi)\n"
            "vào cùng thư mục rồi bấm “Tiếp tục”.\n\n"
            f"Cần dịch:\n{orig}\n\nLưu thành:\n{vi}"
        )
        ctk.CTkLabel(win, text=msg, justify="left", wraplength=600).pack(
            fill="both", expand=True, padx=16, pady=16)

        def cont():
            if os.path.exists(vi):
                win.destroy()
                finish(True)
            else:
                win.title("Dịch ngoài app — chưa thấy transcript_vi.json")

        def ask_claude():
            win.destroy()
            self._run_claude_translate(work_dir, review=False, finish=finish)

        bar = ctk.CTkFrame(win, fg_color="transparent")
        bar.pack(fill="x", padx=16, pady=(0, 14))
        ctk.CTkButton(bar, text="Huỷ", fg_color="gray40", hover_color="gray30",
                      command=lambda: (win.destroy(), finish(False))).pack(side="right")
        ctk.CTkButton(bar, text="Tiếp tục ▶", command=cont).pack(side="right", padx=8)
        ctk.CTkButton(bar, text="🤖 Nhờ Claude dịch tự động", fg_color="#7b5cff",
                      hover_color="#6a4ce0", command=ask_claude).pack(side="left")
        win.protocol("WM_DELETE_WINDOW", lambda: (win.destroy(), finish(False)))
